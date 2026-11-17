"""Rutas de la bandeja del panel: /conversations/...

- GET  /conversations                       la bandeja (filtros y páginas)
- GET  /conversations/counts                los contadores de Inicio (todas las abiertas)
- GET  /conversations/{id}                  una conversación
- GET  /conversations/{id}/messages         sus mensajes (páginas hacia atrás)
- POST /conversations/{id}/messages         contestar como persona
- POST /conversations/{id}/take             tomarla (el bot deja de contestar)
- POST /conversations/{id}/release          devolverla al bot
- POST /conversations/{id}/assign           asignarla a alguien (dueño y administrador)
- POST /conversations/{id}/close            cerrarla

Qué puede hacer cada rol: ver services/inbox.py.
"""

import logging
import uuid
from datetime import datetime
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.api.auth import Factory, client_ip, current_member, get_redis
from app.api.webhooks import get_queue
from app.core import events
from app.core.queue import enqueue_outbound
from app.models.enums import ConversationMode, ConversationStatus
from app.services import inbox, members
from app.services.members import Member

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["bandeja de conversaciones"])

CurrentMember = Annotated[Member, Depends(current_member)]
RedisDep = Annotated[Redis, Depends(get_redis)]


# --- Lo que recibe y devuelve la API ---


class ContactOut(BaseModel):
    id: uuid.UUID
    name: str | None
    phone: str | None


class AssignedOut(BaseModel):
    user_id: uuid.UUID
    name: str | None


class LastMessageOut(BaseModel):
    text: str | None
    direction: str


class ConversationOut(BaseModel):
    id: uuid.UUID
    status: str  # open | closed
    mode: str  # bot | human | paused
    contact: ContactOut
    number_id: uuid.UUID
    number_phone: str | None
    assigned: AssignedOut | None
    last_message: LastMessageOut | None
    last_message_at: datetime | None
    waiting_reply: bool
    created_at: datetime


class MessageOut(BaseModel):
    id: uuid.UUID
    direction: str  # inbound | outbound
    # contact | bot | human_panel | human_phone | system
    sender_type: str
    sender_name: str | None  # quién contestó desde el panel
    content_type: str
    text: str | None
    transcript: str | None
    status: str
    error: str | None
    created_at: datetime


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=inbox.MAX_TEXT)


class ReplyOut(BaseModel):
    message_id: uuid.UUID


class AssignIn(BaseModel):
    user_id: uuid.UUID | None


# --- Ayudantes ---


def _out(row: inbox.ConversationRow) -> ConversationOut:
    return ConversationOut(
        id=row.id,
        status=row.status.value,
        mode=row.mode.value,
        contact=ContactOut(id=row.contact_id, name=row.contact_name, phone=row.contact_phone),
        number_id=row.number_id,
        number_phone=row.number_phone,
        assigned=(
            AssignedOut(user_id=row.assigned_user_id, name=row.assigned_user_name)
            if row.assigned_user_id
            else None
        ),
        last_message=(
            LastMessageOut(text=row.last_message_text, direction=row.last_message_direction.value)
            if row.last_message_direction
            else None
        ),
        last_message_at=row.last_message_at,
        waiting_reply=bool(row.waiting_reply),
        created_at=row.created_at,
    )


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, members.PermissionDeniedError):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    if isinstance(exc, members.ConflictError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, members.InvalidInputError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    return HTTPException(status.HTTP_404_NOT_FOUND, "Esa conversación no existe en este negocio")


ERRORS = (
    members.PermissionDeniedError,
    members.ConflictError,
    members.InvalidInputError,
    members.NotFoundError,
)


async def _changed(redis: Redis, member: Member, conversation_id: uuid.UUID) -> None:
    """Avisa a los paneles abiertos del negocio que esta conversación cambió."""
    await events.publish(
        redis, member.tenant_id, events.CONVERSATION_CHANGED, conversation_id=conversation_id
    )


def _done() -> Response:
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Rutas ---


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    member: CurrentMember,
    factory: Factory,
    status_: Annotated[ConversationStatus, Query(alias="status")] = ConversationStatus.OPEN,
    mode: ConversationMode | None = None,
    mine: bool = False,
    number_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = inbox.PAGE_SIZE,
) -> list[ConversationOut]:
    """Filtros: status (open/closed), mode (bot/human), mine (asignadas a mí), number_id,
    q (nombre o teléfono). Página siguiente: before = last_message_at de la última fila."""
    rows = await inbox.list_conversations(
        member,
        status=status_,
        mode=mode,
        mine=mine,
        number_id=number_id,
        search=q,
        before=before,
        limit=limit,
        factory=factory,
    )
    return [_out(r) for r in rows]


class CountsOut(BaseModel):
    open: int
    waiting: int
    human: int
    mine: int


# Va antes de /{conversation_id}: si no, "counts" se leería como un id.
@router.get("/counts", response_model=CountsOut)
async def count_conversations(member: CurrentMember, factory: Factory) -> CountsOut:
    """Abiertas, esperando respuesta, atendidas por una persona y asignadas a mí."""
    counts = await inbox.count_conversations(member, factory=factory)
    return CountsOut(open=counts.open, waiting=counts.waiting, human=counts.human, mine=counts.mine)


@router.get("/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    conversation_id: uuid.UUID, member: CurrentMember, factory: Factory
) -> ConversationOut:
    try:
        return _out(await inbox.get_conversation(member, conversation_id, factory=factory))
    except ERRORS as exc:
        raise _http_error(exc) from exc


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
async def list_messages(
    conversation_id: uuid.UUID,
    member: CurrentMember,
    factory: Factory,
    before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = inbox.MESSAGES_PAGE_SIZE,
) -> list[MessageOut]:
    try:
        rows = await inbox.list_messages(
            member, conversation_id, before=before, limit=limit, factory=factory
        )
    except ERRORS as exc:
        raise _http_error(exc) from exc
    return [
        MessageOut(
            id=m.id,
            direction=m.direction.value,
            sender_type=m.sender_type.value,
            sender_name=m.sender_name,
            content_type=str(m.content_type),
            text=m.text,
            transcript=m.transcript,
            status=m.status.value,
            error=m.error,
            created_at=m.created_at,
        )
        for m in rows
    ]


@router.post(
    "/{conversation_id}/messages", response_model=ReplyOut, status_code=status.HTTP_202_ACCEPTED
)
async def reply(
    conversation_id: uuid.UUID,
    body: ReplyIn,
    member: CurrentMember,
    factory: Factory,
    queue: Annotated[ArqRedis, Depends(get_queue)],
    redis: RedisDep,
) -> ReplyOut:
    """202: el mensaje quedó en la cola de salida; el estado (sent, delivered...) se ve
    después en los mensajes de la conversación."""
    try:
        message_id = await inbox.send_reply(member, conversation_id, body.text, factory=factory)
    except ERRORS as exc:
        raise _http_error(exc) from exc
    # Si Redis fallara aquí, el barrido del worker encuentra el mensaje en cola y lo envía.
    try:
        await enqueue_outbound(queue, message_id, member.tenant_id)
    except Exception:
        logger.exception("No se pudo encolar %s; lo enviará el barrido del worker", message_id)
    await _changed(redis, member, conversation_id)
    return ReplyOut(message_id=message_id)


@router.post("/{conversation_id}/take", status_code=status.HTTP_204_NO_CONTENT)
async def take(
    conversation_id: uuid.UUID, member: CurrentMember, factory: Factory, redis: RedisDep
) -> Response:
    try:
        await inbox.take(member, conversation_id, factory=factory)
    except ERRORS as exc:
        raise _http_error(exc) from exc
    await _changed(redis, member, conversation_id)
    return _done()


@router.post("/{conversation_id}/release", status_code=status.HTTP_204_NO_CONTENT)
async def release(
    conversation_id: uuid.UUID, member: CurrentMember, factory: Factory, redis: RedisDep
) -> Response:
    try:
        await inbox.release(member, conversation_id, factory=factory)
    except ERRORS as exc:
        raise _http_error(exc) from exc
    await _changed(redis, member, conversation_id)
    return _done()


@router.post("/{conversation_id}/assign", status_code=status.HTTP_204_NO_CONTENT)
async def assign(
    conversation_id: uuid.UUID,
    body: AssignIn,
    member: CurrentMember,
    factory: Factory,
    request: Request,
    redis: RedisDep,
) -> Response:
    try:
        await inbox.assign(
            member, conversation_id, body.user_id, ip=client_ip(request), factory=factory
        )
    except ERRORS as exc:
        raise _http_error(exc) from exc
    await _changed(redis, member, conversation_id)
    return _done()


@router.post("/{conversation_id}/close", status_code=status.HTTP_204_NO_CONTENT)
async def close(
    conversation_id: uuid.UUID,
    member: CurrentMember,
    factory: Factory,
    request: Request,
    redis: RedisDep,
) -> Response:
    try:
        await inbox.close(member, conversation_id, ip=client_ip(request), factory=factory)
    except ERRORS as exc:
        raise _http_error(exc) from exc
    await _changed(redis, member, conversation_id)
    return _done()
