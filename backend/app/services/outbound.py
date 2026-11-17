"""Mensajes salientes: ponerlos en la cola de envío y enviarlos por Evolution.

La tabla messages también es la cola de salida:
  queued   -> el mensaje está guardado y esperando al worker
  sending  -> el worker se lo pasó a Evolution
  sent     -> WhatsApp lo aceptó (luego messages.update lo pasa a delivered / read)
  failed   -> no se pudo enviar (número sin WhatsApp, o demasiados reintentos)

Guardar primero y enviar después nos da lo mismo que en la entrada: si Evolution o
Redis fallan un momento, el mensaje no se pierde y se reintenta.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import SessionFactory, tenant_session
from app.integrations.evolution import EvolutionClient, EvolutionError, EvolutionTimeout
from app.models import ChannelInstance, Contact, Conversation, Message
from app.models.enums import (
    Channel,
    ContentType,
    InstanceStatus,
    MessageDirection,
    MessageStatus,
    SenderType,
)
from app.services import usage
from app.services.message_ingest import STUCK_ERROR, open_conversation

logger = logging.getLogger(__name__)

MAX_ERROR_LENGTH = 500
# Un mensaje que lleva este tiempo en 'sending' sin confirmación se da por fallido.
STUCK_SENDING_SECONDS = 10 * 60
# El error que se guarda en ese caso es message_ingest.STUCK_ERROR: si después llega
# el aviso send.message, el mensaje pasa igual a 'sent'.


class SendOutcome(StrEnum):
    SENT = "sent"
    SKIPPED = "skipped"  # ya no estaba en cola (enviado, fallido u otro worker lo tiene)
    RETRY = "retry"  # fallo pasajero: se vuelve a intentar
    FAILED = "failed"  # no se puede enviar
    UNKNOWN = "unknown"  # Evolution no respondió: no sabemos si salió (no se reintenta)


@dataclass(frozen=True)
class SendResult:
    outcome: SendOutcome
    error: str | None = None


def jid_for_phone(phone: str) -> str:
    """'+58 412-123.4567' -> '584121234567@s.whatsapp.net'."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    if not digits:
        raise ValueError(f"Teléfono no válido: {phone!r}")
    return f"{digits}@s.whatsapp.net"


async def get_or_create_contact(
    session: AsyncSession, tenant_id: uuid.UUID, phone: str
) -> uuid.UUID:
    """Contacto por teléfono, para escribirle a alguien que todavía no nos escribió."""
    jid = jid_for_phone(phone)
    await session.execute(
        insert(Contact)
        .values(
            tenant_id=tenant_id,
            channel=Channel.WHATSAPP,
            external_id=jid,
            phone_e164="+" + jid.split("@", 1)[0],
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "channel", "external_id"])
    )
    return await session.scalar(
        select(Contact.id).where(
            Contact.tenant_id == tenant_id,
            Contact.channel == Channel.WHATSAPP,
            Contact.external_id == jid,
        )
    )


async def queue_text(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    message_text: str,
    *,
    sender_type: SenderType = SenderType.BOT,
    sender_user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Guarda un texto saliente en la cola (status=queued) y devuelve su id.

    Quien lo llama debe encolar el envío DESPUÉS del commit (enqueue_outbound), para que
    el worker encuentre la fila.
    """
    if not message_text.strip():
        raise ValueError("El mensaje está vacío")
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        raise LookupError("La conversación no existe en este negocio")
    message = Message(
        tenant_id=conversation.tenant_id,
        conversation_id=conversation.id,
        channel_instance_id=conversation.channel_instance_id,
        direction=MessageDirection.OUTBOUND,
        sender_type=sender_type,
        sender_user_id=sender_user_id,
        content_type=ContentType.TEXT,
        text=message_text,
        status=MessageStatus.QUEUED,
    )
    session.add(message)
    await session.flush()
    return message.id


async def queue_text_to_phone(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    channel_instance_id: uuid.UUID,
    phone: str,
    message_text: str,
    *,
    sender_type: SenderType = SenderType.SYSTEM,
) -> uuid.UUID:
    """Igual que queue_text, pero a un teléfono (crea contacto y conversación si hace falta)."""
    contact_id = await get_or_create_contact(session, tenant_id, phone)
    conversation_id = await open_conversation(session, tenant_id, channel_instance_id, contact_id)
    return await queue_text(session, conversation_id, message_text, sender_type=sender_type)


async def send_queued_message(
    message_id: uuid.UUID,
    tenant_id: uuid.UUID,
    evo: EvolutionClient,
    *,
    final_attempt: bool = False,
    factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> SendResult:
    """Envía un mensaje de la cola. Lo llama el worker."""
    # 1) Tomar el mensaje y marcarlo 'sending'. Se confirma ANTES de llamar a Evolution
    #    para no tener una transacción abierta mientras esperamos a WhatsApp.
    async with tenant_session(tenant_id, factory=factory) as session:
        row = (
            await session.execute(
                select(Message, Contact.external_id, ChannelInstance)
                .join(Conversation, Conversation.id == Message.conversation_id)
                .join(Contact, Contact.id == Conversation.contact_id)
                .join(ChannelInstance, ChannelInstance.id == Message.channel_instance_id)
                .where(Message.id == message_id, Message.status == MessageStatus.QUEUED)
                .with_for_update(of=Message, skip_locked=True)
            )
        ).first()
        if row is None:
            return SendResult(SendOutcome.SKIPPED)
        message, to_jid, instance = row
        if instance.status != InstanceStatus.OPEN or not instance.evolution_instance_name:
            error = f"El número no está conectado (estado {instance.status})"
            return _retry_or_fail(message, error, final_attempt)
        message.status = MessageStatus.SENDING
        instance_name = instance.evolution_instance_name
        body = message.text or ""

    # 2) Enviar.
    try:
        sent = await evo.send_text(instance_name, to_jid, body)
    except EvolutionTimeout as exc:
        # Puede que sí haya salido. Se queda en 'sending': si llega el aviso send.message
        # de Evolution, ese aviso lo marcará como enviado. No se reintenta para no
        # mandarlo dos veces.
        logger.warning("Mensaje %s: estado desconocido tras timeout", message_id)
        return SendResult(SendOutcome.UNKNOWN, str(exc))
    except EvolutionError as exc:
        permanent = exc.status_code is not None and 400 <= exc.status_code < 500
        async with tenant_session(tenant_id, factory=factory) as session:
            message = await session.get(Message, message_id, with_for_update=True)
            return _retry_or_fail(message, str(exc), final_attempt or permanent)

    # 3) Guardar el id de WhatsApp. Si el aviso send.message llegó antes, ya lo puso.
    now = datetime.now(UTC)
    async with tenant_session(tenant_id, factory=factory) as session:
        # Se cuenta como enviado solo si el aviso send.message no lo había confirmado ya.
        before = await session.scalar(
            select(Message.status).where(Message.id == message_id).with_for_update()
        )
        await session.execute(
            update(Message)
            .where(Message.id == message_id)
            .values(
                external_id=func.coalesce(Message.external_id, sent.message_id),
                error=None,
                status=_advance_to_sent(),
            )
        )
        await session.execute(
            update(Conversation)
            .where(Conversation.id == message.conversation_id)
            .values(
                last_outbound_at=func.greatest(Conversation.last_outbound_at, now),
                last_message_at=func.greatest(Conversation.last_message_at, now),
            )
        )
        if before in (MessageStatus.QUEUED, MessageStatus.SENDING):
            await usage.record(session, tenant_id, at=now, outbound_messages=1)
    return SendResult(SendOutcome.SENT)


def _advance_to_sent():
    # Si un messages.update ya lo pasó a delivered/read, no se retrocede a sent.
    return text(
        "CASE WHEN status IN ('queued', 'sending') THEN 'sent'::message_status ELSE status END"
    )


def _retry_or_fail(message: Message, error: str, final: bool) -> SendResult:
    message.error = error[:MAX_ERROR_LENGTH]
    if final:
        message.status = MessageStatus.FAILED
        logger.error("Mensaje %s no se pudo enviar: %s", message.id, error)
        return SendResult(SendOutcome.FAILED, error)
    message.status = MessageStatus.QUEUED
    logger.warning("Mensaje %s: fallo pasajero, se reintenta: %s", message.id, error)
    return SendResult(SendOutcome.RETRY, error)


async def find_queued_messages(
    older_than_seconds: int = 120,
    limit: int = 100,
    factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Mensajes en cola que nadie tomó (por ejemplo, Redis estaba caído al encolarlos)."""
    async with factory() as session, session.begin():
        rows = await session.execute(
            text(
                "SELECT id, tenant_id FROM queued_outbound_messages("
                "make_interval(secs => :secs), :limit)"
            ),
            {"secs": older_than_seconds, "limit": limit},
        )
        return [(row.id, row.tenant_id) for row in rows]


async def fail_stuck_sending(
    older_than_seconds: int = STUCK_SENDING_SECONDS,
    limit: int = 100,
    factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Marca 'failed' los mensajes atascados en 'sending' y devuelve (id, negocio).

    Pasa cuando Evolution no respondió a tiempo y nunca llegó el aviso send.message, o
    cuando el worker se cayó justo después de marcarlo. No se reenvía solo, porque puede
    que sí haya salido y el cliente lo recibiría dos veces; queda 'failed' con un aviso
    para que el negocio lo vea en la bandeja.
    """
    async with factory() as session, session.begin():
        rows = (
            await session.execute(
                text(
                    "SELECT id, tenant_id FROM stuck_sending_messages("
                    "make_interval(secs => :secs), :limit)"
                ),
                {"secs": older_than_seconds, "limit": limit},
            )
        ).all()
    failed: list[tuple[uuid.UUID, uuid.UUID]] = []
    for message_id, tenant_id in rows:
        async with tenant_session(tenant_id, factory=factory) as session:
            # Se vuelve a comprobar: el aviso send.message pudo llegar mientras tanto.
            changed = await session.scalar(
                update(Message)
                .where(
                    Message.id == message_id,
                    Message.status == MessageStatus.SENDING,
                    Message.updated_at < func.now() - timedelta(seconds=older_than_seconds),
                )
                .values(status=MessageStatus.FAILED, error=STUCK_ERROR)
                .returning(Message.id)
            )
        if changed is not None:
            logger.error("Mensaje %s: sin confirmación de Evolution, queda fallido", message_id)
            failed.append((message_id, tenant_id))
    return failed
