"""Convertir los avisos de mensajes de Evolution en contacto, conversación y mensaje.

Lo llama el worker de la cola ingest (ver webhook_processing.HANDLERS):
- messages.upsert: llegó un mensaje. Si es del cliente es entrante; si fromMe=true lo
  escribió el dueño desde su teléfono (saliente, sender_type human_phone).
- send.message: Evolution envió un mensaje por la API (saliente del bot).
- messages.update: cambió el estado de un mensaje saliente (entregado, leído...).

Evolution v2.3.7 ya nos da el mensaje "normalizado": extendedTextMessage llega como
conversation, documentWithCaptionMessage como documentMessage y los contactos con
dirección @lid llegan con su número real en remoteJid.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChannelInstance, Contact, Conversation, Message, WebhookEvent
from app.models.enums import (
    Channel,
    ContentType,
    ConversationStatus,
    MessageDirection,
    MessageStatus,
    SenderType,
    TranscriptionStatus,
)

logger = logging.getLogger(__name__)

# Tipo de mensaje de WhatsApp -> nuestro content_type.
CONTENT_TYPES: dict[str, ContentType] = {
    "conversation": ContentType.TEXT,
    "imageMessage": ContentType.IMAGE,
    "videoMessage": ContentType.VIDEO,
    "ptvMessage": ContentType.VIDEO,  # video redondo
    "audioMessage": ContentType.AUDIO,
    "documentMessage": ContentType.DOCUMENT,
    "stickerMessage": ContentType.STICKER,
    "locationMessage": ContentType.LOCATION,
    "liveLocationMessage": ContentType.LOCATION,
}

# Estados de WhatsApp (messages.update) -> nuestro estado. Orden: solo se avanza.
STATUS_MAP: dict[str, MessageStatus] = {
    "PENDING": MessageStatus.SENDING,
    "SERVER_ACK": MessageStatus.SENT,
    "DELIVERY_ACK": MessageStatus.DELIVERED,
    "READ": MessageStatus.READ,
    "PLAYED": MessageStatus.READ,  # nota de voz escuchada
}
STATUS_ORDER = [
    MessageStatus.QUEUED,
    MessageStatus.SENDING,
    MessageStatus.SENT,
    MessageStatus.DELIVERED,
    MessageStatus.READ,
]

USER_JID = "@s.whatsapp.net"
GROUP_JID = "@g.us"


@dataclass(frozen=True)
class ParsedMessage:
    external_id: str
    remote_jid: str
    from_me: bool
    is_group: bool
    participant: str | None
    push_name: str | None
    content_type: ContentType
    text: str | None
    media: dict[str, Any] | None
    reply_to: str | None
    timestamp: datetime | None


def phone_from_jid(jid: str | None) -> str | None:
    """'584121234567@s.whatsapp.net' -> '+584121234567'. Los grupos no tienen teléfono."""
    if not jid or not jid.endswith(USER_JID):
        return None
    number = jid.split("@", 1)[0].split(":", 1)[0]
    return f"+{number}" if number.isdigit() else None


def _media_info(kind: str, body: dict[str, Any]) -> dict[str, Any] | None:
    """Datos útiles del archivo, sin la url ni la mediaKey de WhatsApp.

    Para descargar el archivo más adelante basta con el id del mensaje (Evolution lo
    descarga por nosotros), así que no guardamos claves de cifrado en la tabla.
    """
    if kind in ("locationMessage", "liveLocationMessage"):
        return {
            "latitude": body.get("degreesLatitude"),
            "longitude": body.get("degreesLongitude"),
            "name": body.get("name"),
            "address": body.get("address"),
        }
    info = {
        "mime_type": body.get("mimetype"),
        "file_length": _to_int(body.get("fileLength")),
        "seconds": body.get("seconds"),
        "file_name": body.get("fileName"),
        "ptt": body.get("ptt"),  # True = nota de voz grabada en el momento
    }
    return {k: v for k, v in info.items() if v is not None} or None


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_message(data: Any) -> ParsedMessage | None:
    """Lee el 'data' de messages.upsert / send.message. None si no es un mensaje que
    guardemos (reacciones, encuestas, mensajes de sistema, estados de WhatsApp...)."""
    if not isinstance(data, dict):
        return None
    key = data.get("key") or {}
    message = data.get("message") or {}
    kind = data.get("messageType")
    remote_jid = key.get("remoteJid")
    external_id = key.get("id")
    if not remote_jid or not external_id or remote_jid == "status@broadcast":
        return None
    content_type = CONTENT_TYPES.get(kind or "")
    if content_type is None:
        return None

    body = message.get(kind) if kind != "conversation" else None
    body = body if isinstance(body, dict) else {}
    if content_type == ContentType.TEXT:
        message_text = message.get("conversation")
    elif content_type == ContentType.LOCATION:
        message_text = body.get("name") or body.get("address")
    else:
        message_text = body.get("caption")  # pie de foto, video o documento

    context = data.get("contextInfo") or {}
    timestamp = _to_int(data.get("messageTimestamp"))
    return ParsedMessage(
        external_id=str(external_id),
        remote_jid=str(remote_jid),
        from_me=bool(key.get("fromMe")),
        is_group=str(remote_jid).endswith(GROUP_JID),
        participant=key.get("participant"),
        push_name=data.get("pushName"),
        content_type=content_type,
        text=message_text,
        media=None if content_type == ContentType.TEXT else _media_info(kind, body),
        reply_to=context.get("stanzaId") if isinstance(context, dict) else None,
        timestamp=datetime.fromtimestamp(timestamp, UTC) if timestamp else None,
    )


async def upsert_contact(
    session: AsyncSession, tenant_id: uuid.UUID, parsed: ParsedMessage
) -> uuid.UUID:
    """Crea el contacto si no existe; si existe, actualiza su nombre y última visita.

    INSERT ... ON CONFLICT DO UPDATE es atómico: si dos workers procesan a la vez dos
    mensajes del mismo cliente nuevo, no se crean dos contactos.
    """
    # El pushName de un mensaje fromMe es el nombre del dueño, no del cliente.
    # En un grupo, el pushName es de quien escribió, no del grupo.
    name = None if parsed.from_me or parsed.is_group else parsed.push_name
    stmt = insert(Contact).values(
        tenant_id=tenant_id,
        channel=Channel.WHATSAPP,
        external_id=parsed.remote_jid,
        phone_e164=phone_from_jid(parsed.remote_jid),
        display_name=name,
        is_group=parsed.is_group,
        last_seen_at=None if parsed.from_me else func.now(),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["tenant_id", "channel", "external_id"],
        set_={
            "display_name": func.coalesce(stmt.excluded.display_name, Contact.display_name),
            "last_seen_at": func.coalesce(stmt.excluded.last_seen_at, Contact.last_seen_at),
        },
    ).returning(Contact.id)
    return await session.scalar(stmt)


async def open_conversation(
    session: AsyncSession, tenant_id: uuid.UUID, instance_id: uuid.UUID, contact_id: uuid.UUID
) -> uuid.UUID:
    """La conversación abierta de este contacto en este número; si no hay, la crea.

    El índice único parcial (solo status='open') garantiza una sola abierta. Si otro
    worker la crea justo antes, ON CONFLICT DO NOTHING no falla y la leemos después.
    """
    await session.execute(
        insert(Conversation)
        .values(tenant_id=tenant_id, channel_instance_id=instance_id, contact_id=contact_id)
        .on_conflict_do_nothing(
            index_elements=["channel_instance_id", "contact_id"],
            index_where=text("status = 'open'"),
        )
    )
    return await session.scalar(
        select(Conversation.id).where(
            Conversation.channel_instance_id == instance_id,
            Conversation.contact_id == contact_id,
            Conversation.status == ConversationStatus.OPEN,
        )
    )


async def store_message(
    session: AsyncSession, event: WebhookEvent, *, sent_by_api: bool = False
) -> uuid.UUID | None:
    """Guarda el mensaje del aviso. Devuelve su id, o None si no había que guardar nada
    (tipo no soportado, grupo con grupos apagados, o mensaje ya guardado)."""
    parsed = parse_message(event.payload.get("data"))
    if parsed is None:
        logger.info("Aviso %s: mensaje de un tipo que no guardamos", event.id)
        return None

    instance = await session.get(ChannelInstance, event.channel_instance_id)
    if parsed.is_group and not instance.groups_enabled:
        logger.info("Aviso %s: mensaje de grupo y el número tiene grupos apagados", event.id)
        return None

    contact_id = await upsert_contact(session, event.tenant_id, parsed)
    conversation_id = await open_conversation(
        session, event.tenant_id, event.channel_instance_id, contact_id
    )

    outbound = parsed.from_me or sent_by_api
    if sent_by_api:
        sender = SenderType.BOT
    elif parsed.from_me:
        sender = SenderType.HUMAN_PHONE  # el dueño respondió desde su teléfono
    else:
        sender = SenderType.CONTACT

    # ON CONFLICT: si este mensaje ya estaba (por ejemplo, llegaron send.message y
    # messages.upsert del mismo envío), no se duplica.
    message_id = await session.scalar(
        insert(Message)
        .values(
            tenant_id=event.tenant_id,
            conversation_id=conversation_id,
            channel_instance_id=event.channel_instance_id,
            external_id=parsed.external_id,
            direction=MessageDirection.OUTBOUND if outbound else MessageDirection.INBOUND,
            sender_type=sender,
            group_participant_id=parsed.participant if parsed.is_group else None,
            content_type=parsed.content_type,
            text=parsed.text,
            transcription_status=(
                TranscriptionStatus.PENDING
                if parsed.content_type == ContentType.AUDIO and not outbound
                else TranscriptionStatus.NONE
            ),
            media=parsed.media,
            status=MessageStatus.SENT if outbound else MessageStatus.RECEIVED,
            reply_to_external_id=parsed.reply_to,
            external_timestamp=parsed.timestamp,
        )
        .on_conflict_do_nothing(index_elements=["channel_instance_id", "external_id"])
        .returning(Message.id)
    )
    if message_id is None:
        return None

    moment = parsed.timestamp or datetime.now(UTC)
    values: dict[str, Any] = {
        "last_message_at": func.greatest(Conversation.last_message_at, moment)
    }
    if outbound:
        values["last_outbound_at"] = func.greatest(Conversation.last_outbound_at, moment)
    else:
        values["last_inbound_at"] = func.greatest(Conversation.last_inbound_at, moment)
    await session.execute(
        update(Conversation).where(Conversation.id == conversation_id).values(**values)
    )
    return message_id


async def handle_message_upsert(session: AsyncSession, event: WebhookEvent) -> None:
    await store_message(session, event)


async def handle_send_message(session: AsyncSession, event: WebhookEvent) -> None:
    await store_message(session, event, sent_by_api=True)


async def handle_message_update(session: AsyncSession, event: WebhookEvent) -> None:
    """Actualiza el estado de un mensaje SALIENTE (enviado, entregado, leído).

    Los avisos sobre mensajes entrantes (el dueño leyó el mensaje del cliente) no
    cambian nada: el estado de un entrante siempre es 'received'.
    """
    data = event.payload.get("data")
    if not isinstance(data, dict) or data.get("fromMe") is False:
        return
    new_status = STATUS_MAP.get(str(data.get("status")))
    message_id = data.get("keyId")
    if new_status is None or not message_id:
        return
    # Solo se avanza: si llega "entregado" después de "leído", no retrocede.
    earlier = STATUS_ORDER[: STATUS_ORDER.index(new_status)]
    await session.execute(
        update(Message)
        .where(
            Message.channel_instance_id == event.channel_instance_id,
            Message.external_id == str(message_id),
            Message.direction == MessageDirection.OUTBOUND,
            Message.status.in_(earlier),
        )
        .values(status=new_status)
    )
