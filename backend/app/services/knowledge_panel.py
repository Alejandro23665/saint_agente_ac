"""El conocimiento del negocio desde el panel (bloque 5.7).

- Todos ven los documentos, su estado y sus fragmentos.
- Dueño y administrador suben textos, reintentan los que fallaron y borran.

Subir un texto es rápido: se guarda partido en fragmentos y queda 'processing'. El
worker pide los embeddings a OpenRouter (embed_knowledge_source) y lo deja 'ready' o
'failed'. Mientras no haya OPENROUTER_API_KEY, queda 'failed' con ese aviso y se puede
reintentar cuando la clave esté.

Ojo: el modelo gratuito de OpenRouter guarda lo que se le manda. Solo información
pública del negocio (horarios, productos, precios), nunca datos de clientes.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import SessionFactory, tenant_session
from app.models import KnowledgeChunk, KnowledgeSource, User
from app.models.enums import KnowledgeSourceType, ProcessingStatus
from app.services import audit, knowledge
from app.services.members import (
    ConflictError,
    InvalidInputError,
    Member,
    NotFoundError,
    PermissionDeniedError,
    can_edit_tenant,
)

Factory = async_sessionmaker[AsyncSession]

# Desde el panel, hasta ~100 páginas por documento (unos 100 fragmentos).
MAX_PANEL_CHARS = 200_000
TITLE_MAX = 200


@dataclass(frozen=True)
class SourceInfo:
    id: uuid.UUID
    title: str
    source_type: str
    status: ProcessingStatus
    error: str | None
    chunk_count: int
    characters: int
    created_at: datetime
    updated_at: datetime
    created_by_name: str | None


@dataclass(frozen=True)
class ChunkInfo:
    index: int
    content: str
    token_count: int | None
    has_embedding: bool


@dataclass(frozen=True)
class Uploaded:
    source: SourceInfo
    already_loaded: bool  # el mismo texto ya estaba listo: no hay nada que procesar


def _require_edit(member: Member) -> None:
    if not can_edit_tenant(member.role):
        raise PermissionDeniedError("Tu rol no te permite cambiar el conocimiento del negocio")


def _sources_query():
    # Caracteres de cada documento, sumando sus fragmentos (con el solape, es aproximado).
    size = (
        select(func.coalesce(func.sum(func.length(KnowledgeChunk.content)), 0))
        .where(KnowledgeChunk.source_id == KnowledgeSource.id)
        .correlate(KnowledgeSource)
        .scalar_subquery()
    )
    return select(KnowledgeSource, User.full_name, size).outerjoin(
        User, User.id == KnowledgeSource.created_by
    )


def _info(source: KnowledgeSource, name: str | None, characters: int) -> SourceInfo:
    return SourceInfo(
        id=source.id,
        title=source.title,
        source_type=str(source.source_type),
        status=source.status,
        error=source.error,
        chunk_count=source.chunk_count,
        characters=int(characters),
        created_at=source.created_at,
        updated_at=source.updated_at,
        created_by_name=name,
    )


async def _one(session: AsyncSession, source_id: uuid.UUID) -> SourceInfo:
    row = (await session.execute(_sources_query().where(KnowledgeSource.id == source_id))).first()
    if row is None:
        raise NotFoundError("Ese documento no existe")
    return _info(*row)


async def list_sources(member: Member, *, factory: Factory = SessionFactory) -> list[SourceInfo]:
    async with tenant_session(member.tenant_id, factory=factory) as session:
        rows = await session.execute(_sources_query().order_by(KnowledgeSource.title))
        return [_info(*row) for row in rows]


async def get_source(
    member: Member, source_id: uuid.UUID, *, factory: Factory = SessionFactory
) -> tuple[SourceInfo, list[ChunkInfo]]:
    async with tenant_session(member.tenant_id, factory=factory) as session:
        info = await _one(session, source_id)
        chunks = await session.execute(
            select(
                KnowledgeChunk.chunk_index,
                KnowledgeChunk.content,
                KnowledgeChunk.token_count,
                KnowledgeChunk.embedding.is_not(None),
            )
            .where(KnowledgeChunk.source_id == source_id)
            .order_by(KnowledgeChunk.chunk_index)
        )
        return info, [ChunkInfo(*row) for row in chunks]


async def upload_text(
    member: Member,
    title: str,
    text: str,
    *,
    ip: str | None = None,
    factory: Factory = SessionFactory,
) -> Uploaded:
    """Guarda el texto partido en fragmentos. Si queda 'processing', hay que encolar
    embed_knowledge_source (lo hace la ruta)."""
    _require_edit(member)
    title = " ".join(title.split())
    if len(title) > TITLE_MAX:
        raise InvalidInputError(f"El título admite hasta {TITLE_MAX} caracteres")
    try:
        prepared = await knowledge.prepare_text(
            member.tenant_id,
            title,
            text,
            source_type=KnowledgeSourceType.TEXT,
            created_by=member.user_id,
            max_chars=MAX_PANEL_CHARS,
            factory=factory,
        )
    except knowledge.KnowledgeError as exc:
        raise InvalidInputError(str(exc)) from exc
    async with tenant_session(member.tenant_id, factory=factory) as session:
        if not prepared.already_loaded:
            audit.record(
                session,
                tenant_id=member.tenant_id,
                actor_user_id=member.user_id,
                action="knowledge.upload",
                entity_type="knowledge_source",
                entity_id=prepared.source_id,
                diff={"title": title, "chunks": prepared.chunk_count},
                ip=ip,
            )
        info = await _one(session, prepared.source_id)
    return Uploaded(info, prepared.already_loaded)


async def retry(
    member: Member, source_id: uuid.UUID, *, factory: Factory = SessionFactory
) -> SourceInfo:
    """Vuelve a dejarlo 'processing' para que el worker lo intente otra vez."""
    _require_edit(member)
    async with tenant_session(member.tenant_id, factory=factory) as session:
        source = await session.get(KnowledgeSource, source_id, with_for_update=True)
        if source is None:
            raise NotFoundError("Ese documento no existe")
        if source.status == ProcessingStatus.READY:
            raise ConflictError("Ese documento ya está listo")
        source.status = ProcessingStatus.PROCESSING
        source.error = None
        await session.flush()
        return await _one(session, source_id)


async def delete_source(
    member: Member,
    source_id: uuid.UUID,
    *,
    ip: str | None = None,
    factory: Factory = SessionFactory,
) -> None:
    _require_edit(member)
    async with tenant_session(member.tenant_id, factory=factory) as session:
        source = await session.get(KnowledgeSource, source_id, with_for_update=True)
        if source is None:
            raise NotFoundError("Ese documento no existe")
        audit.record(
            session,
            tenant_id=member.tenant_id,
            actor_user_id=member.user_id,
            action="knowledge.delete",
            entity_type="knowledge_source",
            entity_id=source.id,
            diff={"title": source.title},
            ip=ip,
        )
        # Los fragmentos se borran solos (ON DELETE CASCADE).
        await session.execute(delete(KnowledgeSource).where(KnowledgeSource.id == source.id))
