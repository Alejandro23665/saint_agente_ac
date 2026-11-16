"""Rutas del conocimiento del negocio activo: /knowledge/... (bloque 5.7).

- GET    /knowledge               los documentos y su estado
- GET    /knowledge/{id}          un documento con sus fragmentos
- POST   /knowledge               subir un texto (202: los embeddings se calculan después)
- POST   /knowledge/{id}/retry    reintentar uno que falló
- DELETE /knowledge/{id}          borrarlo con sus fragmentos

Estados: processing (calculando), ready (el bot ya lo puede usar), failed (ver error).
Subir otro texto con el mismo título reemplaza al anterior cuando el nuevo queda listo.
Qué puede hacer cada rol: ver services/knowledge_panel.py.
"""

import logging
import uuid
from datetime import datetime
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.api.auth import Factory, client_ip, current_member, get_redis
from app.api.webhooks import get_queue
from app.core import events
from app.core.queue import enqueue_knowledge
from app.models.enums import ProcessingStatus
from app.services import knowledge_panel, members
from app.services.knowledge_panel import MAX_PANEL_CHARS, TITLE_MAX
from app.services.members import Member

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["conocimiento del negocio"])

CurrentMember = Annotated[Member, Depends(current_member)]
Queue = Annotated[ArqRedis, Depends(get_queue)]
RedisDep = Annotated[Redis, Depends(get_redis)]


class SourceOut(BaseModel):
    id: uuid.UUID
    title: str
    source_type: str
    status: str
    error: str | None
    chunk_count: int
    characters: int
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    can_edit: bool


class ChunkOut(BaseModel):
    index: int
    content: str
    token_count: int | None
    has_embedding: bool


class SourceDetailOut(SourceOut):
    chunks: list[ChunkOut]


class UploadIn(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX + 50)
    text: str = Field(min_length=1, max_length=MAX_PANEL_CHARS + 10_000)


class UploadOut(SourceOut):
    already_loaded: bool  # el mismo texto ya estaba listo: no se hizo nada


ERRORS = (
    members.PermissionDeniedError,
    members.InvalidInputError,
    members.NotFoundError,
    members.ConflictError,
)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, members.PermissionDeniedError):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    if isinstance(exc, members.InvalidInputError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    if isinstance(exc, members.ConflictError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))


def _fields(member: Member, s: knowledge_panel.SourceInfo) -> dict:
    return {
        "id": s.id,
        "title": s.title,
        "source_type": s.source_type,
        "status": s.status.value,
        "error": s.error,
        "chunk_count": s.chunk_count,
        "characters": s.characters,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        "created_by": s.created_by_name,
        "can_edit": members.can_edit_tenant(member.role),
    }


async def _process(queue: ArqRedis, redis: Redis, member: Member, source_id: uuid.UUID) -> None:
    """Encola los embeddings y avisa a los paneles abiertos."""
    try:
        await enqueue_knowledge(queue, source_id, member.tenant_id)
    except Exception:
        # Queda 'processing'; desde el panel se puede reintentar.
        logger.exception("No se pudo encolar el documento %s", source_id)
    await events.publish(redis, member.tenant_id, events.KNOWLEDGE_CHANGED, source_id=source_id)


@router.get("", response_model=list[SourceOut])
async def list_sources(member: CurrentMember, factory: Factory) -> list[SourceOut]:
    return [
        SourceOut(**_fields(member, s))
        for s in await knowledge_panel.list_sources(member, factory=factory)
    ]


@router.get("/{source_id}", response_model=SourceDetailOut)
async def get_source(
    source_id: uuid.UUID, member: CurrentMember, factory: Factory
) -> SourceDetailOut:
    try:
        info, chunks = await knowledge_panel.get_source(member, source_id, factory=factory)
    except ERRORS as exc:
        raise _http_error(exc) from exc
    return SourceDetailOut(
        **_fields(member, info),
        chunks=[
            ChunkOut(
                index=c.index,
                content=c.content,
                token_count=c.token_count,
                has_embedding=c.has_embedding,
            )
            for c in chunks
        ],
    )


@router.post("", response_model=UploadOut, status_code=status.HTTP_202_ACCEPTED)
async def upload(
    body: UploadIn,
    member: CurrentMember,
    factory: Factory,
    request: Request,
    queue: Queue,
    redis: RedisDep,
) -> UploadOut:
    """202: el texto quedó guardado en 'processing'. Llega 'knowledge.changed' por
    /events cuando queda 'ready' o 'failed'."""
    try:
        result = await knowledge_panel.upload_text(
            member, body.title, body.text, ip=client_ip(request), factory=factory
        )
    except ERRORS as exc:
        raise _http_error(exc) from exc
    if result.source.status == ProcessingStatus.PROCESSING:
        await _process(queue, redis, member, result.source.id)
    return UploadOut(**_fields(member, result.source), already_loaded=result.already_loaded)


@router.post("/{source_id}/retry", response_model=SourceOut, status_code=status.HTTP_202_ACCEPTED)
async def retry(
    source_id: uuid.UUID, member: CurrentMember, factory: Factory, queue: Queue, redis: RedisDep
) -> SourceOut:
    try:
        info = await knowledge_panel.retry(member, source_id, factory=factory)
    except ERRORS as exc:
        raise _http_error(exc) from exc
    await _process(queue, redis, member, info.id)
    return SourceOut(**_fields(member, info))


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: uuid.UUID,
    member: CurrentMember,
    factory: Factory,
    request: Request,
    redis: RedisDep,
) -> Response:
    try:
        await knowledge_panel.delete_source(
            member, source_id, ip=client_ip(request), factory=factory
        )
    except ERRORS as exc:
        raise _http_error(exc) from exc
    await events.publish(redis, member.tenant_id, events.KNOWLEDGE_CHANGED, source_id=source_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
