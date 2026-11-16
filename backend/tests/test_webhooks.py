"""Endpoint del webhook de Evolution: autenticación, guardado y duplicados."""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.webhooks import get_queue, get_session_factory
from app.core.security import hash_secret, new_secret
from app.db.session import tenant_session
from app.main import create_app
from app.models import ChannelInstance, WebhookEvent
from app.models.enums import WebhookEventStatus
from app.services.channel_registration import WEBHOOK_SECRET_HEADER
from app.services.webhook_ingest import extract_external_id
from tests.factories import create_instance, create_tenant


@dataclass
class Number:
    tenant_id: uuid.UUID
    instance_id: uuid.UUID
    public_id: str
    secret: str

    @property
    def url(self) -> str:
        return f"/webhooks/evolution/{self.public_id}"

    @property
    def headers(self) -> dict[str, str]:
        return {WEBHOOK_SECRET_HEADER: self.secret}


@pytest.fixture
def factory(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(app_engine, expire_on_commit=False)


class FakeQueue:
    """Hace de cola ARQ: recuerda lo que se encoló."""

    def __init__(self, broken: bool = False) -> None:
        self.jobs: list[tuple[str, tuple, dict]] = []
        self.broken = broken

    async def enqueue_job(self, name: str, *args, **kwargs):
        if self.broken:
            raise ConnectionError("Redis caído")
        self.jobs.append((name, args, kwargs))
        return object()


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
async def client(factory, queue) -> AsyncIterator[httpx.AsyncClient]:
    """La API real, pero usando la conexión con permisos de saint_app (RLS activo)."""
    app = create_app()
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_queue] = lambda: queue
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def new_number(owner_engine: AsyncEngine) -> Number:
    secret = new_secret()
    async with AsyncSession(owner_engine) as s, s.begin():
        tenant = await create_tenant(s, "Webhook")
        instance = await create_instance(s, tenant)
        instance.webhook_secret_hash = hash_secret(secret)
        number = Number(tenant.id, instance.id, instance.public_id, secret)
    return number


def message(message_id: str = "3EB0A1", text: str = "Hola") -> dict:
    """Aviso messages.upsert como lo manda Evolution v2.3.7."""
    return {
        "event": "messages.upsert",
        "instance": "saint-abc",
        "data": {
            "key": {"remoteJid": "584121234567@s.whatsapp.net", "fromMe": False, "id": message_id},
            "pushName": "Cliente",
            "message": {"conversation": text},
            "messageType": "conversation",
            "messageTimestamp": 1763240000,
        },
        "date_time": "2026-11-15T17:00:00.000Z",
        "sender": "584146090675@s.whatsapp.net",
        "apikey": "no-debe-guardarse",
    }


async def events_of(number: Number, factory) -> list[WebhookEvent]:
    async with tenant_session(number.tenant_id, factory=factory) as s:
        return list(
            await s.scalars(
                select(WebhookEvent).where(WebhookEvent.channel_instance_id == number.instance_id)
            )
        )


async def test_guarda_el_aviso(owner_engine: AsyncEngine, client, factory) -> None:
    number = await new_number(owner_engine)
    response = await client.post(number.url, json=message(), headers=number.headers)
    assert response.status_code == 200
    assert response.json() == {"status": "stored", "event": "messages.upsert"}

    [event] = await events_of(number, factory)
    assert event.tenant_id == number.tenant_id
    assert event.event_type == "messages.upsert"
    assert event.external_id == "3EB0A1"
    assert event.status == WebhookEventStatus.PENDING
    assert event.payload["data"]["message"] == {"conversation": "Hola"}
    # La clave de Evolution nunca se guarda.
    assert "apikey" not in event.payload

    async with tenant_session(number.tenant_id, factory=factory) as s:
        instance = await s.get(ChannelInstance, number.instance_id)
    assert instance.last_event_at is not None


async def test_encola_el_aviso(owner_engine: AsyncEngine, client, queue) -> None:
    number = await new_number(owner_engine)
    await client.post(number.url, json=message("E1"), headers=number.headers)
    # El reintento de Evolution no se vuelve a encolar.
    await client.post(number.url, json=message("E1"), headers=number.headers)
    [(name, args, kwargs)] = queue.jobs
    assert name == "process_webhook_event"
    assert args[1] == str(number.tenant_id)
    assert kwargs["_job_id"] == f"webhook-event:{args[0]}"
    assert kwargs["_queue_name"] == "saint:ingest"


async def test_redis_caido_no_pierde_el_aviso(owner_engine: AsyncEngine, factory) -> None:
    number = await new_number(owner_engine)
    app = create_app()
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_queue] = lambda: FakeQueue(broken=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.post(number.url, json=message(), headers=number.headers)
    # Se responde 200 y el aviso queda guardado; el barrido del worker lo encolará.
    assert response.status_code == 200
    [event] = await events_of(number, factory)
    assert event.status == WebhookEventStatus.PENDING


async def test_reintento_no_duplica(owner_engine: AsyncEngine, client, factory) -> None:
    number = await new_number(owner_engine)
    first = await client.post(number.url, json=message("X1"), headers=number.headers)
    again = await client.post(number.url, json=message("X1"), headers=number.headers)
    other = await client.post(number.url, json=message("X2"), headers=number.headers)
    assert first.json()["status"] == "stored"
    assert again.status_code == 200 and again.json()["status"] == "duplicate"
    assert other.json()["status"] == "stored"
    assert sorted(e.external_id for e in await events_of(number, factory)) == ["X1", "X2"]


async def test_secreto_incorrecto_o_ausente(owner_engine: AsyncEngine, client, factory) -> None:
    number = await new_number(owner_engine)
    bad = {WEBHOOK_SECRET_HEADER: "otro-secreto"}
    assert (await client.post(number.url, json=message(), headers=bad)).status_code == 401
    assert (await client.post(number.url, json=message())).status_code == 401
    assert await events_of(number, factory) == []


async def test_secreto_de_otro_numero(owner_engine: AsyncEngine, client, factory) -> None:
    a = await new_number(owner_engine)
    b = await new_number(owner_engine)
    response = await client.post(a.url, json=message(), headers=b.headers)
    assert response.status_code == 401
    assert await events_of(a, factory) == []


async def test_numero_desconocido(client) -> None:
    response = await client.post(
        "/webhooks/evolution/no-existe", json=message(), headers={WEBHOOK_SECRET_HEADER: "x"}
    )
    assert response.status_code == 404


async def test_cuerpo_invalido(owner_engine: AsyncEngine, client) -> None:
    number = await new_number(owner_engine)
    h = number.headers | {"content-type": "application/json"}
    assert (await client.post(number.url, content=b"no es json", headers=h)).status_code == 400
    assert (await client.post(number.url, json=[1, 2], headers=h)).status_code == 400
    assert (await client.post(number.url, json={"data": {}}, headers=h)).status_code == 400
    big = b'{"event":"messages.upsert","x":"' + b"a" * 1_000_001 + b'"}'
    assert (await client.post(number.url, content=big, headers=h)).status_code == 413


async def test_evento_no_pedido_queda_ignorado(
    owner_engine: AsyncEngine, client, factory, queue
) -> None:
    number = await new_number(owner_engine)
    payload = {"event": "chats.update", "instance": "saint-abc", "data": []}
    response = await client.post(number.url, json=payload, headers=number.headers)
    assert response.status_code == 200
    [event] = await events_of(number, factory)
    assert event.status == WebhookEventStatus.IGNORED
    assert queue.jobs == []  # lo ignorado no se encola


def test_external_id_por_tipo() -> None:
    assert extract_external_id("messages.upsert", {"key": {"id": "A"}}) == "A"
    assert extract_external_id("send.message", {"key": {"id": "B"}}) == "B"
    # Un mismo mensaje recibe varios avisos de estado: cada estado cuenta como uno distinto.
    assert extract_external_id("messages.update", {"keyId": "A", "status": "READ"}) == "A:READ"
    assert extract_external_id("connection.update", {"state": "open"}) is None
    assert extract_external_id("messages.upsert", "raro") is None
