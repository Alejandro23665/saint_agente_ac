"""Avisos de mensajes -> contacto, conversación y mensaje (con el rol de la app y RLS)."""

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.session import tenant_session
from app.models import ChannelInstance, Contact, Conversation, Message, WebhookEvent
from app.models.enums import (
    ContentType,
    MessageDirection,
    MessageStatus,
    SenderType,
    TranscriptionStatus,
    WebhookEventStatus,
)
from app.services.message_ingest import parse_message, phone_from_jid
from app.services.webhook_ingest import extract_external_id
from app.services.webhook_processing import Outcome, process_event
from tests.factories import create_instance, create_tenant

CLIENT = "584121234567@s.whatsapp.net"
GROUP = "120363025555555555@g.us"


@pytest.fixture
def factory(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(app_engine, expire_on_commit=False)


@dataclass
class Number:
    owner_engine: AsyncEngine
    factory: async_sessionmaker[AsyncSession]
    tenant_id: uuid.UUID
    instance_id: uuid.UUID

    async def receive(self, event_type: str, data: dict) -> Outcome:
        """Guarda el aviso como lo haría el webhook y lo procesa como el worker."""
        async with AsyncSession(self.owner_engine) as s, s.begin():
            event = WebhookEvent(
                tenant_id=self.tenant_id,
                channel_instance_id=self.instance_id,
                event_type=event_type,
                external_id=extract_external_id(event_type, data),
                payload={"event": event_type, "instance": "saint-x", "data": data},
            )
            s.add(event)
            await s.flush()
            event_id = event.id
        result = await process_event(event_id, self.tenant_id, factory=self.factory)
        async with tenant_session(self.tenant_id, factory=self.factory) as s:
            stored = await s.get(WebhookEvent, event_id)
        assert stored.status == WebhookEventStatus.PROCESSED, stored.error
        return result.outcome

    async def all(self, model):
        async with tenant_session(self.tenant_id, factory=self.factory) as s:
            return list(await s.scalars(select(model).order_by(model.id)))


@pytest.fixture
async def number(owner_engine: AsyncEngine, factory) -> Number:
    async with AsyncSession(owner_engine) as s, s.begin():
        tenant = await create_tenant(s, "Mensajes")
        instance = await create_instance(s, tenant)
        n = Number(owner_engine, factory, tenant.id, instance.id)
    return n


def upsert(
    msg_id: str = "3EB0AAA1",
    *,
    text: str = "Hola, ¿tienen pan dulce?",
    from_me: bool = False,
    jid: str = CLIENT,
    push_name: str = "María",
    **extra,
) -> dict:
    """data de messages.upsert tal como lo manda Evolution v2.3.7."""
    data = {
        "key": {"remoteJid": jid, "fromMe": from_me, "id": msg_id},
        "pushName": push_name,
        "status": "DELIVERY_ACK",
        "message": {"conversation": text},
        "messageType": "conversation",
        "messageTimestamp": 1763240000,
        "instanceId": "x",
        "source": "android",
    }
    data.update(extra)
    return data


def voice_note(msg_id: str = "ACD89E81") -> dict:
    return upsert(
        msg_id,
        message={
            "audioMessage": {
                "url": "https://mmg.whatsapp.net/secreto",
                "mimetype": "audio/ogg; codecs=opus",
                "fileLength": "11234",
                "seconds": 4,
                "ptt": True,
                "mediaKey": "clave-secreta",
            }
        },
        messageType="audioMessage",
    )


async def test_mensaje_de_texto_crea_todo(number: Number) -> None:
    assert await number.receive("messages.upsert", upsert()) == Outcome.PROCESSED

    [contact] = await number.all(Contact)
    assert contact.external_id == CLIENT
    assert contact.phone_e164 == "+584121234567"
    assert contact.display_name == "María"
    assert contact.last_seen_at is not None and not contact.is_group

    [conversation] = await number.all(Conversation)
    assert conversation.contact_id == contact.id
    assert conversation.channel_instance_id == number.instance_id
    assert conversation.last_inbound_at is not None
    assert conversation.last_message_at == conversation.last_inbound_at

    [message] = await number.all(Message)
    assert message.conversation_id == conversation.id
    assert message.external_id == "3EB0AAA1"
    assert (message.direction, message.sender_type) == (
        MessageDirection.INBOUND,
        SenderType.CONTACT,
    )
    assert (message.content_type, message.text) == (ContentType.TEXT, "Hola, ¿tienen pan dulce?")
    assert message.status == MessageStatus.RECEIVED
    assert message.processed_at is None  # el bot aún no lo atendió
    assert message.external_timestamp.timestamp() == 1763240000


async def test_mismo_cliente_misma_conversacion(number: Number) -> None:
    await number.receive("messages.upsert", upsert("M1"))
    await number.receive(
        "messages.upsert", upsert("M2", text="¿A qué hora abren?", push_name="Mari")
    )
    assert len(await number.all(Contact)) == 1
    assert len(await number.all(Conversation)) == 1
    assert [m.text for m in await number.all(Message)] == [
        "Hola, ¿tienen pan dulce?",
        "¿A qué hora abren?",
    ]
    # El nombre se actualiza al último que usa el cliente.
    [contact] = await number.all(Contact)
    assert contact.display_name == "Mari"


async def test_nota_de_voz(number: Number) -> None:
    await number.receive("messages.upsert", voice_note())
    [message] = await number.all(Message)
    assert message.content_type == ContentType.AUDIO
    assert message.transcription_status == TranscriptionStatus.PENDING
    assert message.media == {
        "mime_type": "audio/ogg; codecs=opus",
        "file_length": 11234,
        "seconds": 4,
        "ptt": True,
    }
    # Ni la url ni la clave de cifrado de WhatsApp se guardan en la tabla.
    assert "secreto" not in str(message.media) and "clave" not in str(message.media)


async def test_el_dueno_responde_desde_su_telefono(number: Number) -> None:
    await number.receive("messages.upsert", upsert("M1"))
    await number.receive(
        "messages.upsert",
        upsert("M2", text="Sí, hasta las 7", from_me=True, push_name="Panadería"),
    )
    inbound, outbound = await number.all(Message)
    assert (outbound.direction, outbound.sender_type) == (
        MessageDirection.OUTBOUND,
        SenderType.HUMAN_PHONE,
    )
    assert outbound.status == MessageStatus.SENT
    assert outbound.conversation_id == inbound.conversation_id
    # El nombre del dueño no pisa el del cliente.
    [contact] = await number.all(Contact)
    assert contact.display_name == "María"
    [conversation] = await number.all(Conversation)
    assert conversation.last_outbound_at is not None


async def test_envio_por_api_y_upsert_del_mismo_mensaje(number: Number) -> None:
    sent = upsert("OUT1", text="Hola, soy el asistente", from_me=True)
    await number.receive("send.message", sent)
    await number.receive("messages.upsert", sent)  # el mismo envío visto otra vez
    [message] = await number.all(Message)
    assert message.sender_type == SenderType.BOT


async def test_estados_de_un_saliente(number: Number) -> None:
    await number.receive("send.message", upsert("OUT1", from_me=True))

    async def status_update(status: str, from_me: bool = True) -> MessageStatus:
        data = {"keyId": "OUT1", "remoteJid": CLIENT, "fromMe": from_me, "status": status}
        await number.receive("messages.update", data)
        [message] = await number.all(Message)
        return message.status

    assert await status_update("DELIVERY_ACK") == MessageStatus.DELIVERED
    assert await status_update("READ") == MessageStatus.READ
    # Un aviso atrasado no hace retroceder el estado.
    assert await status_update("SERVER_ACK") == MessageStatus.READ


async def test_leido_de_un_entrante_no_cambia_nada(number: Number) -> None:
    await number.receive("messages.upsert", upsert("IN1"))
    data = {"keyId": "IN1", "remoteJid": CLIENT, "fromMe": False, "status": "READ"}
    await number.receive("messages.update", data)
    [message] = await number.all(Message)
    assert message.status == MessageStatus.RECEIVED


async def test_grupos_apagados(number: Number) -> None:
    data = upsert(
        "G1",
        jid=GROUP,
        key={"remoteJid": GROUP, "fromMe": False, "id": "G1", "participant": CLIENT},
    )
    await number.receive("messages.upsert", data)
    assert await number.all(Message) == []
    assert await number.all(Contact) == []


async def test_grupos_encendidos(number: Number) -> None:
    async with AsyncSession(number.owner_engine) as s, s.begin():
        await s.execute(
            update(ChannelInstance)
            .where(ChannelInstance.id == number.instance_id)
            .values(groups_enabled=True)
        )
    data = upsert(
        "G1", key={"remoteJid": GROUP, "fromMe": False, "id": "G1", "participant": CLIENT}
    )
    await number.receive("messages.upsert", data)
    [contact] = await number.all(Contact)
    assert contact.is_group and contact.phone_e164 is None
    assert contact.display_name is None  # el pushName es de quien escribió, no del grupo
    [message] = await number.all(Message)
    assert message.group_participant_id == CLIENT


async def test_tipos_que_no_guardamos(number: Number) -> None:
    reaction = upsert(
        "R1",
        message={"reactionMessage": {"text": "👍", "key": {"id": "M1"}}},
        messageType="reactionMessage",
    )
    assert await number.receive("messages.upsert", reaction) == Outcome.PROCESSED
    status = upsert("S1", key={"remoteJid": "status@broadcast", "fromMe": False, "id": "S1"})
    await number.receive("messages.upsert", status)
    assert await number.all(Message) == []


def test_parse_foto_con_pie_y_respuesta() -> None:
    parsed = parse_message(
        upsert(
            "P1",
            message={
                "imageMessage": {
                    "caption": "Este",
                    "mimetype": "image/jpeg",
                    "mediaKey": "x",
                    "url": "https://x",
                }
            },
            messageType="imageMessage",
            contextInfo={"stanzaId": "M0", "quotedMessage": {"conversation": "¿Cuál?"}},
        )
    )
    assert parsed.content_type == ContentType.IMAGE
    assert parsed.text == "Este"
    assert parsed.media == {"mime_type": "image/jpeg"}
    assert parsed.reply_to == "M0"


def test_parse_ubicacion() -> None:
    parsed = parse_message(
        upsert(
            "L1",
            message={
                "locationMessage": {
                    "degreesLatitude": 10.5,
                    "degreesLongitude": -66.9,
                    "name": "Plaza Bolívar",
                }
            },
            messageType="locationMessage",
        )
    )
    assert parsed.content_type == ContentType.LOCATION
    assert parsed.text == "Plaza Bolívar"
    assert parsed.media["latitude"] == 10.5


def test_telefono_desde_jid() -> None:
    assert phone_from_jid("584121234567@s.whatsapp.net") == "+584121234567"
    assert phone_from_jid("584121234567:12@s.whatsapp.net") == "+584121234567"
    assert phone_from_jid(GROUP) is None
    assert phone_from_jid("123456@lid") is None
