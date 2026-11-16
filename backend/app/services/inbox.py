"""Bandeja de conversaciones del panel (bloque 5.4).

Todas las personas del negocio (dueño, administrador y agente) ven la bandeja, leen los
mensajes y pueden contestar. Además:

- Tomar una conversación: pasa a modo humano y queda asignada a quien la tomó. El bot
  deja de contestar en ella.
- Devolverla al bot: vuelve a modo bot y queda sin asignar.
- Contestar desde el panel en una conversación del bot la toma automáticamente, para que
  el bot y la persona no contesten a la vez.
- Asignar a otra persona: solo dueño y administrador.
- Cerrar: la conversación sale de la bandeja de abiertas. Si el cliente vuelve a escribir,
  se abre una conversación nueva.

Cada cambio entre bot y humano queda en handoff_events; asignar y cerrar, en audit_logs.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, and_, func, or_, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.db.session import SessionFactory, tenant_session
from app.models import (
    ChannelInstance,
    Contact,
    Conversation,
    HandoffEvent,
    Message,
    TenantMembership,
    User,
)
from app.models.enums import (
    ConversationMode,
    ConversationStatus,
    HandoffReason,
    MessageDirection,
    MessageStatus,
    SenderType,
)
from app.services import audit
from app.services.members import (
    ConflictError,
    InvalidInputError,
    Member,
    NotFoundError,
    PermissionDeniedError,
    can_edit_tenant,
)
from app.services.outbound import queue_text

Factory = async_sessionmaker[AsyncSession]

PAGE_SIZE = 30
MESSAGES_PAGE_SIZE = 50
MAX_TEXT = 4096  # WhatsApp corta los mensajes más largos


@dataclass(frozen=True)
class ConversationRow:
    id: uuid.UUID
    status: ConversationStatus
    mode: ConversationMode
    contact_id: uuid.UUID
    contact_name: str | None
    contact_phone: str | None
    number_id: uuid.UUID
    number_phone: str | None
    assigned_user_id: uuid.UUID | None
    assigned_user_name: str | None
    last_message_at: datetime | None
    last_message_text: str | None
    last_message_direction: MessageDirection | None
    # El cliente escribió y nadie le contestó todavía.
    waiting_reply: bool
    created_at: datetime


@dataclass(frozen=True)
class MessageRow:
    id: uuid.UUID
    direction: MessageDirection
    sender_type: SenderType
    sender_user_id: uuid.UUID | None
    sender_name: str | None
    content_type: str
    text: str | None
    transcript: str | None
    status: MessageStatus
    error: str | None
    created_at: datetime


def _conversation_query() -> Select:
    """Conversación + contacto + número + persona asignada + último mensaje."""
    last = (
        select(Message.text, Message.direction)
        .where(Message.conversation_id == Conversation.id)
        .order_by(Message.created_at.desc())
        .limit(1)
        .lateral("last_message")
    )
    assigned = aliased(User)
    return (
        select(
            Conversation.id,
            Conversation.status,
            Conversation.mode,
            Contact.id,
            Contact.display_name,
            Contact.phone_e164,
            ChannelInstance.id,
            ChannelInstance.display_identifier,
            Conversation.assigned_user_id,
            assigned.full_name,
            Conversation.last_message_at,
            last.c.text,
            last.c.direction,
            and_(
                Conversation.last_inbound_at.is_not(None),
                or_(
                    Conversation.last_outbound_at.is_(None),
                    Conversation.last_inbound_at > Conversation.last_outbound_at,
                ),
            ),
            Conversation.created_at,
        )
        .join(Contact, Contact.id == Conversation.contact_id)
        .join(ChannelInstance, ChannelInstance.id == Conversation.channel_instance_id)
        .outerjoin(assigned, assigned.id == Conversation.assigned_user_id)
        .outerjoin(last, true())
    )


async def list_conversations(
    member: Member,
    *,
    status: ConversationStatus = ConversationStatus.OPEN,
    mode: ConversationMode | None = None,
    mine: bool = False,
    number_id: uuid.UUID | None = None,
    search: str | None = None,
    before: datetime | None = None,
    limit: int = PAGE_SIZE,
    factory: Factory = SessionFactory,
) -> list[ConversationRow]:
    """La bandeja: la conversación con el mensaje más reciente primero.

    Para la página siguiente se pasa en `before` el last_message_at de la última fila.
    """
    query = _conversation_query().where(Conversation.status == status)
    if mode is not None:
        query = query.where(Conversation.mode == mode)
    if mine:
        query = query.where(Conversation.assigned_user_id == member.user_id)
    if number_id is not None:
        query = query.where(Conversation.channel_instance_id == number_id)
    if search:
        term = search.strip()
        digits = "".join(c for c in term if c.isdigit())
        conditions = [Contact.display_name.ilike(f"%{term}%")]
        if len(digits) >= 3:
            conditions.append(Contact.phone_e164.contains(digits))
        query = query.where(or_(*conditions))
    sort_key = func.coalesce(Conversation.last_message_at, Conversation.created_at)
    if before is not None:
        query = query.where(sort_key < before)
    query = query.order_by(sort_key.desc(), Conversation.id.desc()).limit(min(limit, 100))
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        rows = await session.execute(query)
        return [ConversationRow(*row) for row in rows]


async def get_conversation(
    member: Member, conversation_id: uuid.UUID, *, factory: Factory = SessionFactory
) -> ConversationRow:
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        row = (
            await session.execute(_conversation_query().where(Conversation.id == conversation_id))
        ).first()
    if row is None:
        raise NotFoundError
    return ConversationRow(*row)


async def list_messages(
    member: Member,
    conversation_id: uuid.UUID,
    *,
    before: datetime | None = None,
    limit: int = MESSAGES_PAGE_SIZE,
    factory: Factory = SessionFactory,
) -> list[MessageRow]:
    """Los mensajes en orden de lectura (el más viejo arriba). Para cargar más arriba,
    se pasa en `before` el created_at del primer mensaje de la página."""
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        exists = await session.scalar(
            select(Conversation.id).where(Conversation.id == conversation_id)
        )
        if exists is None:
            raise NotFoundError
        query = (
            select(
                Message.id,
                Message.direction,
                Message.sender_type,
                Message.sender_user_id,
                User.full_name,
                Message.content_type,
                Message.text,
                Message.transcript,
                Message.status,
                Message.error,
                Message.created_at,
            )
            .outerjoin(User, User.id == Message.sender_user_id)
            .where(Message.conversation_id == conversation_id)
        )
        if before is not None:
            query = query.where(Message.created_at < before)
        rows = await session.execute(
            query.order_by(Message.created_at.desc()).limit(min(limit, 200))
        )
        page = [MessageRow(*row) for row in rows]
    return list(reversed(page))


# --- Acciones ---


async def _lock(session: AsyncSession, conversation_id: uuid.UUID) -> Conversation:
    conversation = await session.scalar(
        select(Conversation).where(Conversation.id == conversation_id).with_for_update()
    )
    if conversation is None:
        raise NotFoundError
    return conversation


def _require_open(conversation: Conversation) -> None:
    if conversation.status is not ConversationStatus.OPEN:
        raise ConflictError("La conversación está cerrada")


def _change_mode(
    session: AsyncSession,
    conversation: Conversation,
    to_mode: ConversationMode,
    reason: HandoffReason,
    actor: Member,
) -> None:
    session.add(
        HandoffEvent(
            tenant_id=conversation.tenant_id,
            conversation_id=conversation.id,
            from_mode=conversation.mode,
            to_mode=to_mode,
            reason=reason,
            actor_user_id=actor.user_id,
        )
    )
    conversation.mode = to_mode


async def send_reply(
    member: Member,
    conversation_id: uuid.UUID,
    message_text: str,
    *,
    factory: Factory = SessionFactory,
) -> uuid.UUID:
    """Guarda la respuesta en la cola de salida y devuelve su id.

    Quien llama debe encolar el envío después (enqueue_outbound), cuando ya se guardó.
    """
    message_text = message_text.strip()
    if not message_text:
        raise InvalidInputError("El mensaje está vacío")
    if len(message_text) > MAX_TEXT:
        raise InvalidInputError(f"El mensaje no puede pasar de {MAX_TEXT} caracteres")
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        conversation = await _lock(session, conversation_id)
        _require_open(conversation)
        number = await session.get(ChannelInstance, conversation.channel_instance_id)
        if number is None or number.deleted_at is not None:
            raise ConflictError("El número de esta conversación ya no existe")
        if conversation.mode is ConversationMode.BOT:
            # Una persona contestó: el bot se aparta para no responder encima.
            _change_mode(session, conversation, ConversationMode.HUMAN, HandoffReason.PANEL, member)
        if conversation.assigned_user_id is None:
            conversation.assigned_user_id = member.user_id
        message_id = await queue_text(
            session,
            conversation_id,
            message_text,
            sender_type=SenderType.HUMAN_PANEL,
            sender_user_id=member.user_id,
        )
        # Lo que el cliente escribió antes ya lo atendió una persona: el bot no lo contesta.
        await session.execute(
            update(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.direction == MessageDirection.INBOUND,
                Message.processed_at.is_(None),
            )
            .values(processed_at=func.now())
        )
        now = func.now()
        conversation.last_outbound_at = now
        conversation.last_message_at = now
        return message_id


async def take(
    member: Member, conversation_id: uuid.UUID, *, factory: Factory = SessionFactory
) -> None:
    """La persona toma la conversación: el bot deja de contestar y queda asignada a ella."""
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        conversation = await _lock(session, conversation_id)
        _require_open(conversation)
        if conversation.mode is not ConversationMode.HUMAN:
            _change_mode(session, conversation, ConversationMode.HUMAN, HandoffReason.PANEL, member)
        conversation.assigned_user_id = member.user_id


async def release(
    member: Member, conversation_id: uuid.UUID, *, factory: Factory = SessionFactory
) -> None:
    """Devuelve la conversación al bot, que contesta los mensajes nuevos que lleguen."""
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        conversation = await _lock(session, conversation_id)
        _require_open(conversation)
        if conversation.mode is not ConversationMode.BOT:
            _change_mode(
                session, conversation, ConversationMode.BOT, HandoffReason.RETURNED, member
            )
        conversation.assigned_user_id = None


async def assign(
    member: Member,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID | None,
    *,
    ip: str | None = None,
    factory: Factory = SessionFactory,
) -> None:
    """Asigna la conversación a una persona del negocio (o a nadie con None)."""
    if not can_edit_tenant(member.role):
        raise PermissionDeniedError("Solo el dueño o un administrador asignan conversaciones")
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        conversation = await _lock(session, conversation_id)
        _require_open(conversation)
        if user_id is not None:
            is_member = await session.scalar(
                select(TenantMembership.id).where(
                    TenantMembership.tenant_id == member.tenant_id,
                    TenantMembership.user_id == user_id,
                )
            )
            if is_member is None:
                raise InvalidInputError("Esa persona no es miembro del negocio")
        previous = conversation.assigned_user_id
        conversation.assigned_user_id = user_id
        audit.record(
            session,
            tenant_id=member.tenant_id,
            actor_user_id=member.user_id,
            action="conversation.assign",
            entity_type="conversation",
            entity_id=conversation_id,
            diff={
                "assigned_user_id": [
                    str(previous) if previous else None,
                    str(user_id) if user_id else None,
                ]
            },
            ip=ip,
        )


async def close(
    member: Member,
    conversation_id: uuid.UUID,
    *,
    ip: str | None = None,
    factory: Factory = SessionFactory,
) -> None:
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        conversation = await _lock(session, conversation_id)
        _require_open(conversation)
        conversation.status = ConversationStatus.CLOSED
        conversation.closed_at = func.now()
        audit.record(
            session,
            tenant_id=member.tenant_id,
            actor_user_id=member.user_id,
            action="conversation.close",
            entity_type="conversation",
            entity_id=conversation_id,
            ip=ip,
        )
