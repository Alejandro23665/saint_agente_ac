"""Conocimiento del negocio: guardar un texto partido en fragmentos con su embedding.

El negocio da un texto (horarios, productos, precios, preguntas frecuentes...). Se guarda
como un documento (knowledge_sources) y se parte en fragmentos de unos 500 tokens (unas
350 palabras, en knowledge_chunks). Cada fragmento lleva su embedding para buscarlo por
significado, y PostgreSQL le calcula solo la columna tsv para buscarlo por palabras
(bloque 4.5).

Pasos:
1. prepare_text (rápido, lo hace la API): crea el documento (o lo reutiliza si el mismo
   texto ya se cargó), lo parte y guarda los fragmentos todavía sin embedding, en
   'processing'.
2. embed_source (lo hace el worker, bloque 5.7): pide los embeddings a OpenRouter, que
   tarda, sin tener la base bloqueada mientras tanto.
3. Guarda los embeddings y marca 'ready'. Si OpenRouter falló (o falta la clave), marca
   'failed' con el error; los fragmentos quedan y se puede reintentar.

load_text hace los tres pasos de una vez (el script de la terminal lo usa así).

Si ya había otro documento con el mismo título, se borra al final: así "actualizar los
horarios" es cargar el texto nuevo con el mismo título, sin que queden los viejos.
"""

import hashlib
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select, update
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
MISSING_KEY = "Falta OPENROUTER_API_KEY en el .env: agrégala y vuelve a intentar"

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


@dataclass(frozen=True)
class Prepared:
    source_id: uuid.UUID
    status: ProcessingStatus
    chunk_count: int
    already_loaded: bool = False


async def prepare_text(
    tenant_id: uuid.UUID,
    title: str,
    text: str,
    *,
    source_type: KnowledgeSourceType = KnowledgeSourceType.TEXT,
    file_url: str | None = None,
    created_by: uuid.UUID | None = None,
    max_chars: int = MAX_TEXT_CHARS,
    factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> Prepared:
    """Pasos 1 y 2 sin OpenRouter: guarda el documento y sus fragmentos todavía sin
    embedding, en 'processing'. Es rápido; el embedding lo pone después embed_source."""
    title = title.strip()
    text = normalize(text)
    if not title:
        raise KnowledgeError("El documento necesita un título")
    if not text:
        raise KnowledgeError("El texto está vacío")
    if len(text) > max_chars:
        raise KnowledgeError(
            f"El texto es demasiado grande ({len(text)} caracteres; máximo {max_chars})"
        )
    digest = checksum(text)
    chunks = split_into_chunks(text)

    async with tenant_session(tenant_id, factory=factory) as session:
        # Crear el documento, o reutilizar el que tiene exactamente el mismo texto.
        await session.execute(
            insert(KnowledgeSource)
            .values(
                tenant_id=tenant_id,
                title=title,
                source_type=source_type,
                file_url=file_url,
                checksum=digest,
                created_by=created_by,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "checksum"])
        )
        source = await session.scalar(
            select(KnowledgeSource).where(KnowledgeSource.checksum == digest).with_for_update()
        )
        if source.status == ProcessingStatus.READY:
            return Prepared(source.id, source.status, source.chunk_count, already_loaded=True)
        source.title = title
        source.status = ProcessingStatus.PROCESSING
        source.error = None
        source.chunk_count = len(chunks)
        await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.source_id == source.id))
        session.add_all(
            KnowledgeChunk(
                tenant_id=tenant_id,
                source_id=source.id,
                chunk_index=index,
                content=content,
                token_count=estimate_tokens(content),
            )
            for index, content in enumerate(chunks)
        )
        return Prepared(source.id, ProcessingStatus.PROCESSING, len(chunks))


async def embed_source(
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    *,
    embedder: Embedder | None,
    factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> LoadResult | None:
    """Pasos 2 y 3: pide los embeddings de los fragmentos y deja el documento 'ready'
    (o 'failed' con el error). None si el documento ya no existe o no está pendiente."""
    async with tenant_session(tenant_id, factory=factory) as session:
        source = await session.get(KnowledgeSource, source_id)
        if source is None or source.status not in (
            ProcessingStatus.PROCESSING,
            ProcessingStatus.PENDING,
        ):
            return None
        rows = list(
            await session.execute(
                select(KnowledgeChunk.id, KnowledgeChunk.content)
                .where(KnowledgeChunk.source_id == source_id)
                .order_by(KnowledgeChunk.chunk_index)
            )
        )

    # Sin tener la base bloqueada mientras OpenRouter contesta.
    error: str | None = None
    result = None
    if embedder is None:
        error = MISSING_KEY
    elif not rows:
        error = "El documento no tiene fragmentos; vuelve a subir el texto"
    else:
        try:
            result = await embedder.embed([content for _, content in rows])
        except EmbeddingError as exc:
            error = str(exc)[:MAX_ERROR_LENGTH]

    async with tenant_session(tenant_id, factory=factory) as session:
        source = await session.get(KnowledgeSource, source_id, with_for_update=True)
        if source is None:  # lo borraron mientras tanto
            return None
        if result is None:
            source.status = ProcessingStatus.FAILED
            source.error = error
            return LoadResult(source_id, ProcessingStatus.FAILED, 0, error=error)
        for (chunk_id, _), vector in zip(rows, result.vectors, strict=True):
            await session.execute(
                update(KnowledgeChunk)
                .where(KnowledgeChunk.id == chunk_id)
                .values(embedding=vector, embedding_model=result.model)
            )
        source.status = ProcessingStatus.READY
        source.error = None
        source.chunk_count = len(rows)
        # Las versiones viejas con el mismo título se van (y sus fragmentos, por CASCADE).
        old = await session.execute(
            delete(KnowledgeSource)
            .where(KnowledgeSource.title == source.title, KnowledgeSource.id != source_id)
            .returning(KnowledgeSource.id)
        )
        replaced = len(old.all())
    return LoadResult(source_id, ProcessingStatus.READY, len(rows), replaced=replaced)


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
    """Todo de una vez (lo usa el script de la terminal): preparar y pedir embeddings."""
    prepared = await prepare_text(
        tenant_id, title, text, source_type=source_type, file_url=file_url, factory=factory
    )
    if prepared.already_loaded:
        return LoadResult(
            prepared.source_id, prepared.status, prepared.chunk_count, already_loaded=True
        )
    result = await embed_source(tenant_id, prepared.source_id, embedder=embedder, factory=factory)
    if result is None:  # otro proceso lo terminó o lo borró justo ahora
        return LoadResult(prepared.source_id, prepared.status, prepared.chunk_count)
    return result


async def list_sources(session: AsyncSession) -> list[KnowledgeSource]:
    return list(await session.scalars(select(KnowledgeSource).order_by(KnowledgeSource.created_at)))


async def delete_source(session: AsyncSession, title: str) -> int:
    """Borra los documentos con ese título (y sus fragmentos). Devuelve cuántos borró."""
    deleted = await session.execute(
        delete(KnowledgeSource).where(KnowledgeSource.title == title).returning(KnowledgeSource.id)
    )
    return len(deleted.all())
