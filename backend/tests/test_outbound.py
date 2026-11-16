"""Cola de salida: guardar, enviar por Evolution (simulado), reintentos y confirmación."""

import json
import uuid
from datetime import timedelta

import httpx
import pytest
from arq import Retry
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.session import tenant_session
from app.integrations.evolution import EvolutionClient
from app.models import ChannelInstance, Contact, Conversation, Message, WebhookEvent
from app.models.enums import InstanceStatus, MessageStatus, SenderType
from app.services.outbound import (
    SendOutcome,
    find_queued_messages,
    jid_for_phone,
    queue_text_to_phone,
    send_queued_message,
)
from app.services.webhook_processing import Outcome, process_event
from app.workers import ingest
from tests.factories import create_instance, create_tenant

PHONE = "+58 412-123.4567"
JID = "584121234567@s.whatsapp.net"


class FakeEvolution:
    """Responde a /message/sendText como Evolution v2.3.7 (o falla a propósito)."""

    def __init__(self, fail: int | Exception | None = None) -> None:
        self.fail = fail
        self.sent: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if isinstance(self.fail, Exception):
            raise self.fail
        if self.fail:
            body = {"status": self.fail, "error": "x", "response": {"message": ["falló"]}}
            return httpx.Response(self.fail, json=body)
        body = json.loads(request.content)
        self.sent.append({"path": request.url.path, **body})
        return httpx.Response(
            201,
            json={
                "key": {"remoteJid": body["number"], "fromMe": True, "id": f"3EB0{len(self.sent)}"},
                "message": {"conversation": body["text"]},
                "messageTimestamp": 1763250000,
                "status": "PENDING",
            },
        )

    def client(self) -> EvolutionClient:
        return EvolutionClient("http://evolution:8080", "k", transport=httpx.MockTransport(self))


@pytest.fixture
def factory(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(app_engine, expire_on_commit=False)


async def new_number(
    owner_engine: AsyncEngine, status: InstanceStatus = InstanceStatus.OPEN
) -> tuple[uuid.UUID, uuid.UUID, str]:
    async with AsyncSession(owner_engine) as s, s.begin():
        tenant = await create_tenant(s, "Salida")
        instance = await create_instance(s, tenant)
        instance.status = status
        ids = (tenant.id, instance.id, instance.evolution_instance_name)
    return ids


async def queue(factory, tenant_id, instance_id, text="Hola, ¿en qué te ayudo?") -> uuid.UUID:
    async with tenant_session(tenant_id, factory=factory) as s:
        return await queue_text_to_phone(s, tenant_id, instance_id, PHONE, text)


async def load(factory, tenant_id, message_id) -> Message:
    async with tenant_session(tenant_id, factory=factory) as s:
        return await s.get(Message, message_id)


async def send(fake, factory, tenant_id, message_id, final=False):
    async with fake.client() as evo:
        return await send_queued_message(
            message_id, tenant_id, evo, final_attempt=final, factory=factory
        )


def test_jid_desde_telefono() -> None:
    assert jid_for_phone(PHONE) == JID
    with pytest.raises(ValueError):
        jid_for_phone("sin número")


async def test_envia_y_guarda_el_id(owner_engine, factory) -> None:
    tenant_id, instance_id, instance_name = await new_number(owner_engine)
    message_id = await queue(factory, tenant_id, instance_id)
    queued = await load(factory, tenant_id, message_id)
    assert queued.status == MessageStatus.QUEUED and queued.sender_type == SenderType.SYSTEM

    fake = FakeEvolution()
    assert (await send(fake, factory, tenant_id, message_id)).outcome == SendOutcome.SENT
    assert fake.sent == [
        {"path": f"/message/sendText/{instance_name}", "number": JID, "text": queued.text}
    ]
    sent = await load(factory, tenant_id, message_id)
    assert (sent.status, sent.external_id, sent.error) == (MessageStatus.SENT, "3EB01", None)

    async with tenant_session(tenant_id, factory=factory) as s:
        [contact] = await s.scalars(select(Contact).where(Contact.tenant_id == tenant_id))
        assert (contact.external_id, contact.phone_e164) == (JID, "+584121234567")
        conversation = await s.get(Conversation, sent.conversation_id)
        assert conversation.last_outbound_at is not None

    # Un segundo intento (por ejemplo, el barrido) no lo reenvía.
    assert (await send(fake, factory, tenant_id, message_id)).outcome == SendOutcome.SKIPPED
    assert len(fake.sent) == 1


async def test_numero_sin_whatsapp_falla_sin_reintentar(owner_engine, factory) -> None:
    tenant_id, instance_id, _ = await new_number(owner_engine)
    message_id = await queue(factory, tenant_id, instance_id)
    result = await send(FakeEvolution(fail=400), factory, tenant_id, message_id)
    assert result.outcome == SendOutcome.FAILED
    message = await load(factory, tenant_id, message_id)
    assert message.status == MessageStatus.FAILED and "falló" in message.error


@pytest.mark.parametrize("fail", [500, httpx.ConnectError("sin conexión")])
async def test_fallo_pasajero_reintenta_y_al_final_falla(owner_engine, factory, fail) -> None:
    tenant_id, instance_id, _ = await new_number(owner_engine)
    message_id = await queue(factory, tenant_id, instance_id)
    fake = FakeEvolution(fail=fail)
    assert (await send(fake, factory, tenant_id, message_id)).outcome == SendOutcome.RETRY
    message = await load(factory, tenant_id, message_id)
    assert message.status == MessageStatus.QUEUED and message.error

    final = await send(fake, factory, tenant_id, message_id, final=True)
    assert final.outcome == SendOutcome.FAILED
    assert (await load(factory, tenant_id, message_id)).status == MessageStatus.FAILED


async def test_numero_desconectado_espera(owner_engine, factory) -> None:
    tenant_id, instance_id, _ = await new_number(owner_engine, InstanceStatus.CLOSE)
    message_id = await queue(factory, tenant_id, instance_id)
    fake = FakeEvolution()
    assert (await send(fake, factory, tenant_id, message_id)).outcome == SendOutcome.RETRY
    assert fake.sent == []
    assert (await load(factory, tenant_id, message_id)).status == MessageStatus.QUEUED


@pytest.mark.parametrize(
    ("fail", "event_type"),
    [
        (httpx.ReadTimeout("lento"), "send.message"),
        (httpx.RemoteProtocolError("se cortó"), "send.message"),
        (httpx.ReadTimeout("lento"), "messages.upsert"),
    ],
)
async def test_sin_respuesta_no_reenvia_y_lo_confirma_el_aviso(
    owner_engine, factory, fail, event_type
) -> None:
    """Si Evolution no responde (tarda o se corta la conexión) no reintentamos: se
    mandaría dos veces. El aviso de Evolution confirma que sí salió."""
    tenant_id, instance_id, _ = await new_number(owner_engine)
    message_id = await queue(factory, tenant_id, instance_id, "Abrimos a las 7")
    result = await send(FakeEvolution(fail=fail), factory, tenant_id, message_id)
    assert result.outcome == SendOutcome.UNKNOWN
    assert (await load(factory, tenant_id, message_id)).status == MessageStatus.SENDING

    data = {
        "key": {"remoteJid": JID, "fromMe": True, "id": "3EB0TARDE"},
        "message": {"conversation": "Abrimos a las 7"},
        "messageType": "conversation",
        "messageTimestamp": 1763250000,
    }
    async with AsyncSession(owner_engine) as s, s.begin():
        event = WebhookEvent(
            tenant_id=tenant_id,
            channel_instance_id=instance_id,
            event_type=event_type,
            external_id="3EB0TARDE",
            payload={"event": event_type, "data": data},
        )
        s.add(event)
        await s.flush()
        event_id = event.id
    assert (await process_event(event_id, tenant_id, factory=factory)).outcome == Outcome.PROCESSED

    message = await load(factory, tenant_id, message_id)
    assert (message.status, message.external_id) == (MessageStatus.SENT, "3EB0TARDE")
    async with tenant_session(tenant_id, factory=factory) as s:
        count = await s.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.channel_instance_id == instance_id)
        )
        conversation = await s.get(Conversation, message.conversation_id)
    assert count == 1  # no se creó un mensaje duplicado
    assert conversation.last_outbound_at is not None


async def test_foto_con_el_mismo_texto_no_confirma_un_texto(owner_engine, factory) -> None:
    tenant_id, instance_id, _ = await new_number(owner_engine)
    message_id = await queue(factory, tenant_id, instance_id, "Hola")
    await send(FakeEvolution(fail=httpx.ReadTimeout("lento")), factory, tenant_id, message_id)
    data = {
        "key": {"remoteJid": JID, "fromMe": True, "id": "FOTO1"},
        "message": {"imageMessage": {"caption": "Hola", "mimetype": "image/jpeg"}},
        "messageType": "imageMessage",
        "messageTimestamp": 1763250000,
    }
    async with AsyncSession(owner_engine) as s, s.begin():
        event = WebhookEvent(
            tenant_id=tenant_id,
            channel_instance_id=instance_id,
            event_type="send.message",
            external_id="FOTO1",
            payload={"event": "send.message", "data": data},
        )
        s.add(event)
        await s.flush()
        event_id = event.id
    await process_event(event_id, tenant_id, factory=factory)
    message = await load(factory, tenant_id, message_id)
    assert (message.status, message.external_id) == (MessageStatus.SENDING, None)
    async with tenant_session(tenant_id, factory=factory) as s:
        photo = await s.scalar(select(Message).where(Message.external_id == "FOTO1"))
    assert photo is not None and photo.media == {"mime_type": "image/jpeg"}


async def test_barrido_encuentra_los_que_quedaron_en_cola(owner_engine, factory) -> None:
    tenant_id, instance_id, _ = await new_number(owner_engine)
    old_id = await queue(factory, tenant_id, instance_id, "viejo")
    new_id = await queue(factory, tenant_id, instance_id, "nuevo")
    async with AsyncSession(owner_engine) as s, s.begin():
        await s.execute(
            update(Message)
            .where(Message.id == old_id)
            .values(created_at=func.now() - timedelta(minutes=5))
        )
    found = [message_id for message_id, _ in await find_queued_messages(factory=factory)]
    assert old_id in found and new_id not in found


class NoPanel:
    """Redis de mentira para los avisos al panel (aquí no se revisan)."""

    async def publish(self, channel: str, payload: str) -> int:
        return 0


async def test_tarea_del_worker_pide_reintento(owner_engine, factory) -> None:
    tenant_id, instance_id, _ = await new_number(owner_engine, InstanceStatus.CONNECTING)
    message_id = await queue(factory, tenant_id, instance_id)
    async with FakeEvolution().client() as evo:
        ctx = {"factory": factory, "evolution": evo, "redis": NoPanel(), "job_try": 1}
        with pytest.raises(Retry):
            await ingest.send_outbound_message(ctx, str(message_id), str(tenant_id))
        ctx["job_try"] = ingest.MAX_SEND_TRIES
        result = await ingest.send_outbound_message(ctx, str(message_id), str(tenant_id))
    assert result == "failed"
    async with AsyncSession(owner_engine) as s:
        instance = await s.get(ChannelInstance, instance_id)
        assert instance.status == InstanceStatus.CONNECTING
