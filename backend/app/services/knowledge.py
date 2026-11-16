"""Conocimiento del negocio: guardar un texto partido en fragmentos con su embedding.

El negocio da un texto (horarios, productos, precios, preguntas frecuentes...). Se guarda
como un documento (knowledge_sources) y se parte en fragmentos de unos 500 tokens (unas 350 palabras)
(knowledge_chunks). Cada fragmento lleva su embedding para buscarlo por significado, y
PostgreSQL le calcula solo la columna tsv para buscarlo por palabras (bloque 4.5).

Pasos de load_text:
1. Transacción corta: crea el documento (o lo reutiliza si el mismo texto ya se cargó)
   y lo marca 'processing'.
2. Fuera de la transacción: parte el texto y pide los embeddings a OpenRouter, que tarda.
3. Transacción corta: guarda los fragmentos y marca 'ready'. Si OpenRouter falló, marca
   'failed' con el error, y se puede volver a cargar.

Si ya había otro documento con el mismo título, se borra al final: así "actualizar los
horarios" es cargar el texto nuevo con el mismo título, sin que queden los viejos.
"""

import hashlib
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import SessionFactory, tenant_session
from app.integrations.embeddings import Embedder, EmbeddingError
from app.models import KnowledgeChunk, KnowledgeSource
from app.models.enums import KnowledgeSourceType, ProcessingStatus

# En español un token son unos 4 caracteres: 500 tokens ~ 2000 caracteres.
CHARS_PER_TOKEN = 4
CHUNK_TOKENS = 500
# Cada fragmento repite el final del anterior, para no cortar una idea por la mitad.
OVERLAP_TOKENS = 60
MAX_TEXT_CHARS = 2_000_000  # ~1000 páginas: más que eso no es un texto de negocio
MAX_ERROR_LENGTH = 500

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


class KnowledgeError(Exception):
    """El texto no se puede cargar (vacío, demasiado grande...)."""


@dataclass(frozen=True)
class LoadResult:
    source_id: uuid.UUID
    status: ProcessingStatus
    chunk_count: int
    already_loaded: bool = False  # el mismo texto ya estaba cargado: no se hizo nada
    replaced: int = 0  # documentos viejos con el mismo título que se borraron
    error: str | None = None


def normalize(text: str) -> str:
    """Saltos de línea uniformes, sin espacios al final de cada línea ni líneas vacías de más."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text) / CHARS_PER_TOKEN))


def _pieces(text: str, max_chars: int) -> list[str]:
    """Trozos que caben en un fragmento: párrafos, si no frases, si no grupos de palabras."""
    pieces: list[str] = []
    for paragraph in text.split("\n\n"):
        if len(paragraph) <= max_chars:
            pieces.append(paragraph)
            continue
        for sentence in _SENTENCE_END.split(paragraph):
            if len(sentence) <= max_chars:
                pieces.append(sentence)
                continue
            words: list[str] = []
            for long_word in sentence.split(" "):
                # Una "palabra" más larga que un fragmento (un enlace enorme) se corta.
                for i in range(0, max(len(long_word), 1), max_chars):
                    word = long_word[i : i + max_chars]
                    if words and len(" ".join([*words, word])) > max_chars:
                        pieces.append(" ".join(words))
                        words = []
                    words.append(word)
            if words:
                pieces.append(" ".join(words))
    return [p.strip() for p in pieces if p.strip()]


def split_into_chunks(
    text: str, chunk_tokens: int = CHUNK_TOKENS, overlap_tokens: int = OVERLAP_TOKENS
) -> list[str]:
    """Parte el texto en fragmentos de ~chunk_tokens, respetando párrafos y frases."""
    max_chars = chunk_tokens * CHARS_PER_TOKEN
    overlap_chars = overlap_tokens * CHARS_PER_TOKEN
    chunks: list[str] = []
    current: list[str] = []

    def size(parts: list[str]) -> int:
        return len("\n\n".join(parts))

    for piece in _pieces(normalize(text), max_chars):
        if current and size([*current, piece]) > max_chars:
            chunks.append("\n\n".join(current))
            # El siguiente empieza con los últimos trozos del anterior que quepan en el solape.
            tail: list[str] = []
            for previous in reversed(current):
                if (
                    size([previous, *tail, piece]) > max_chars
                    or size([previous, *tail]) > overlap_chars
                ):
                    break
                tail.insert(0, previous)
            current = tail
        current.append(piece)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def load_text(
    tenant_id: uuid.UUID,
    title: str,
    text: str,
    *,
    embedder: Embedder,
    source_type: KnowledgeSourceType = KnowledgeSourceType.TEXT,
    file_url: str | None = None,
    factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> LoadResult:
    """Guarda el texto como conocimiento del negocio (ver los pasos arriba)."""
    title = title.strip()
    text = normalize(text)
    if not title:
        raise KnowledgeError("El documento necesita un título")
    if not text:
        raise KnowledgeError("El texto está vacío")
    if len(text) > MAX_TEXT_CHARS:
        raise KnowledgeError(f"El texto es demasiado grande ({len(text)} caracteres)")
    digest = checksum(text)

    # 1. Crear el documento, o reutilizar el que tiene exactamente el mismo texto.
    async with tenant_session(tenant_id, factory=factory) as session:
        await session.execute(
            insert(KnowledgeSource)
            .values(
                tenant_id=tenant_id,
                title=title,
                source_type=source_type,
                file_url=file_url,
                checksum=digest,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "checksum"])
        )
        source = await session.scalar(
            select(KnowledgeSource).where(KnowledgeSource.checksum == digest).with_for_update()
        )
        if source.status == ProcessingStatus.READY:
            return LoadResult(source.id, source.status, source.chunk_count, already_loaded=True)
        source.title = title
        source.status = ProcessingStatus.PROCESSING
        source.error = None
        source_id = source.id

    # 2. Partir y pedir los embeddings, sin tener la base bloqueada mientras tanto.
    chunks = split_into_chunks(text)
    try:
        result = await embedder.embed(chunks)
    except EmbeddingError as exc:
        error = str(exc)[:MAX_ERROR_LENGTH]
        async with tenant_session(tenant_id, factory=factory) as session:
            source = await session.get(KnowledgeSource, source_id, with_for_update=True)
            source.status = ProcessingStatus.FAILED
            source.error = error
        return LoadResult(source_id, ProcessingStatus.FAILED, 0, error=error)

    # 3. Guardar los fragmentos y borrar las versiones viejas con el mismo título.
    async with tenant_session(tenant_id, factory=factory) as session:
        source = await session.get(KnowledgeSource, source_id, with_for_update=True)
        await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.source_id == source_id))
        session.add_all(
            KnowledgeChunk(
                tenant_id=tenant_id,
                source_id=source_id,
                chunk_index=index,
                content=content,
                token_count=estimate_tokens(content),
                embedding=vector,
                embedding_model=result.model,
            )
            for index, (content, vector) in enumerate(zip(chunks, result.vectors, strict=True))
        )
        source.status = ProcessingStatus.READY
        source.chunk_count = len(chunks)
        # Los fragmentos se borran solos con el documento (ON DELETE CASCADE).
        old = await session.execute(
            delete(KnowledgeSource)
            .where(KnowledgeSource.title == title, KnowledgeSource.id != source_id)
            .returning(KnowledgeSource.id)
        )
        replaced = len(old.all())
    return LoadResult(source_id, ProcessingStatus.READY, len(chunks), replaced=replaced)


async def list_sources(session: AsyncSession) -> list[KnowledgeSource]:
    return list(await session.scalars(select(KnowledgeSource).order_by(KnowledgeSource.created_at)))


async def delete_source(session: AsyncSession, title: str) -> int:
    """Borra los documentos con ese título (y sus fragmentos). Devuelve cuántos borró."""
    deleted = await session.execute(
        delete(KnowledgeSource).where(KnowledgeSource.title == title).returning(KnowledgeSource.id)
    )
    return len(deleted.all())
