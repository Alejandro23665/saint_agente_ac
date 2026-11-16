"""Responder a un cliente: esperar a que termine de escribir y contestar.

Los clientes de WhatsApp suelen mandar varios mensajes seguidos ("Hola" / "¿tienen
pan?" / "¿a qué hora abren?"). Si el bot contestara cada uno por separado, respondería
tres veces y a medias. Por eso espera (debounce): cuando llega un mensaje se programa
una respuesta para dentro de 15 s; si antes llega otro, la espera vuelve a empezar.
Pasados 15 s sin mensajes nuevos, el bot lee todos los pendientes juntos y contesta una
sola vez.

En el bloque 4.2 la respuesta es un eco (sin IA), para probar la maquinaria. En el 4.3
la escribirá el modelo de IA.

Los mensajes pendientes son los entrantes del cliente con processed_at vacío. Al
contestar se marcan como atendidos.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.session import SessionFactory, tenant_session
from app.models import Contact, Conversation, Message
from app.models.enums import (
    ContentType,
    ConversationMode,
    ConversationStatus,
    MessageDirection,
    SenderType,
)
from app.services.outbound import queue_text

logger = logging.getLogger(__name__)

# Segundos sin mensajes nuevos antes de contestar.
DEBOUNCE_SECONDS = 15
# Si el cliente no para de escribir, se contesta igual pasado este tiempo.
MAX_WAIT_SECONDS = 120
# Mensajes más viejos que esto no se contestan (por ejemplo, tras días apagado).
MAX_AGE = timedelta(hours=1)

# Cómo se muestra en el eco lo que no es texto.
PLACEHOLDERS: dict[ContentType, str] = {
    ContentType.AUDIO: "[nota de voz]",
    ContentType.IMAGE: "[foto]",
    ContentType.VIDEO: "[video]",
    ContentType.DOCUMENT: "[documento]",
    ContentType.STICKER: "[sticker]",
    ContentType.LOCATION: "[ubicación]",
}


class ReplyOutcome(StrEnum):
    REPLIED = "replied"  # se encoló una respuesta
    WAIT = "wait"  # el cliente sigue escribiendo: volver a mirar en wait_seconds
    NOTHING = "nothing"  # no había mensajes pendientes
    SKIPPED = "skipped"  # otro worker está contestando esta conversación
    NOT_BOT = "not_bot"  # la conversación la atiende una persona, o no se contesta


@dataclass(frozen=True)
class ReplyResult:
    outcome: ReplyOutcome
    message_id: uuid.UUID | None = None  # la respuesta encolada (enviarla tras el commit)
    wait_seconds: float = 0


def echo_text(messages: list[Message]) -> str:
    """Eco del bloque 4.2: repite lo que dijo el cliente, en un solo mensaje."""
    lines = []
    for message in messages:
        placeholder = PLACEHOLDERS.get(message.content_type)
        if message.text and placeholder:
            lines.append(f"{placeholder} {message.text}")
        else:
            lines.append(message.text or placeholder or "[mensaje]")
    return "Recibí:\n" + "\n".join(lines)


def allowed_phones() -> set[str]:
    """Teléfonos a los que el bot puede contestar (BOT_TEST_PHONES). Vacío = a todos."""
    raw = get_settings().bot_test_phones
    return {"+" + "".join(ch for ch in p if ch.isdigit()) for p in raw.split(",") if p.strip()}


async def answer_conversation(
    conversation_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    debounce_seconds: float = DEBOUNCE_SECONDS,
    max_wait_seconds: float = MAX_WAIT_SECONDS,
    factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> ReplyResult:
    """Si el cliente ya terminó de escribir, encola una respuesta. Lo llama el worker."""
    async with tenant_session(tenant_id, factory=factory) as session:
        # La conversación bloqueada: dos workers nunca contestan a la vez el mismo chat.
        row = (
            await session.execute(
                select(Conversation, Contact)
                .join(Contact, Contact.id == Conversation.contact_id)
                .where(Conversation.id == conversation_id)
                .with_for_update(of=Conversation, skip_locked=True)
            )
        ).first()
        if row is None:
            return ReplyResult(ReplyOutcome.SKIPPED)
        conversation, contact = row

        pending = list(
            await session.scalars(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.direction == MessageDirection.INBOUND,
                    Message.processed_at.is_(None),
                )
                .order_by(Message.created_at, Message.id)
            )
        )
        if not pending:
            return ReplyResult(ReplyOutcome.NOTHING)

        if not _bot_answers(conversation, contact):
            # Lo atiende una persona (o es un grupo, o no está en la lista de prueba):
            # se dan por vistos para que el bot no los conteste después.
            await _mark_processed(session, pending)
            return ReplyResult(ReplyOutcome.NOT_BOT)

        now = await session.scalar(select(func.clock_timestamp()))
        fresh = [m for m in pending if now - m.created_at <= MAX_AGE]
        if not fresh:
            await _mark_processed(session, pending)
            return ReplyResult(ReplyOutcome.NOTHING)

        since_last = (now - fresh[-1].created_at).total_seconds()
        since_first = (now - fresh[0].created_at).total_seconds()
        if since_last < debounce_seconds and since_first < max_wait_seconds:
            wait = min(debounce_seconds - since_last, max_wait_seconds - since_first)
            return ReplyResult(ReplyOutcome.WAIT, wait_seconds=max(wait, 0.5))

        reply_id = await queue_text(
            session, conversation_id, echo_text(fresh), sender_type=SenderType.BOT
        )
        await _mark_processed(session, pending)
        logger.info(
            "Conversación %s: %s mensajes contestados con %s", conversation_id, len(fresh), reply_id
        )
        return ReplyResult(ReplyOutcome.REPLIED, reply_id)


def _bot_answers(conversation: Conversation, contact: Contact) -> bool:
    if conversation.status != ConversationStatus.OPEN:
        return False
    if conversation.mode != ConversationMode.BOT:
        return False
    if contact.is_group:
        return False  # en grupos el bot no contesta (por ahora)
    phones = allowed_phones()
    return not phones or contact.phone_e164 in phones


async def _mark_processed(session: AsyncSession, messages: list[Message]) -> None:
    await session.execute(
        update(Message)
        .where(Message.id.in_([m.id for m in messages]))
        .values(processed_at=func.now())
    )


async def find_conversations_awaiting_reply(
    older_than_seconds: int = 30,
    limit: int = 100,
    factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Conversaciones con mensajes sin contestar que nadie tomó (Redis caído, etc.)."""
    async with factory() as session, session.begin():
        rows = await session.execute(
            text(
                "SELECT conversation_id, tenant_id FROM conversations_awaiting_reply("
                "make_interval(secs => :secs), make_interval(secs => :max_age), :limit)"
            ),
            {
                "secs": older_than_seconds,
                "max_age": MAX_AGE.total_seconds(),
                "limit": limit,
            },
        )
        return [(row.conversation_id, row.tenant_id) for row in rows]
