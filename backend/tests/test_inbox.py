"""Bandeja de conversaciones del panel (bloque 5.4)."""

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.auth import get_redis
from app.api.webhooks import get_queue, get_session_factory
from app.core.config import get_settings
from app.main import create_app
from app.models import Conversation, HandoffEvent, Message
from app.models.enums import (
    ConversationMode,
    MembershipRole,
    MessageDirection,
    MessageStatus,
    SenderType,
)
from tests.factories import create_contact, create_conversation, create_instance
from tests.test_auth import SECRET, Person, bearer, create_user, login, new_tenant

OWNER, ADMIN, AGENT = MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.AGENT


@pytest.fixture(autouse=True)
def jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(get_settings(), "jwt_secret", SecretStr(SECRET))
    yield


class FakeQueue:
    default_queue_name = "test"

    def __init__(self) -> None:
        self.jobs: list[tuple] = []

    async def enqueue_job(self, *args, **kwargs):
        self.jobs.append(args)
        return object()


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
async def client(app_engine: AsyncEngine, queue: FakeQueue) -> AsyncIterator[httpx.AsyncClient]:
    factory = async_sessionmaker(app_engine, expire_on_commit=False)
    redis = Redis.from_url(get_settings().redis_url)
    app = create_app()
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_queue] = lambda: queue
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://panel.test") as c:
        yield c
    keys = [k async for k in redis.scan_iter("saint:login-fail:*")]
    if keys:
        await redis.delete(*keys)
    await redis.aclose()


async def headers_for(client: httpx.AsyncClient, person: Person) -> dict[str, str]:
    return bearer(await login(client, person.email))


async def chat(owner_engine: AsyncEngine, tenant, texts=("Hola", "¿Tienen pan?"), name="María"):
    """Una conversación con mensajes del cliente, el más nuevo al final."""
    async with AsyncSession(owner_engine, expire_on_commit=False) as s, s.begin():
        instance = await create_instance(s, tenant)
        contact = await create_contact(s, tenant)
        contact.display_name = name
        contact.phone_e164 = "+58412" + uuid.uuid4().hex[:7].translate(
            str.maketrans("abcdef", "123456")
        )
        conversation = await create_conversation(s, tenant, instance, contact)
        start = datetime.now(UTC) - timedelta(minutes=10)
        for i, text in enumerate(texts):
            s.add(
                Message(
                    tenant_id=tenant.id,
                    conversation_id=conversation.id,
                    channel_instance_id=instance.id,
                    direction=MessageDirection.INBOUND,
                    sender_type=SenderType.CONTACT,
                    text=text,
                    status=MessageStatus.RECEIVED,
                    created_at=start + timedelta(minutes=i),
                )
            )
        conversation.last_inbound_at = start + timedelta(minutes=len(texts))
        conversation.last_message_at = conversation.last_inbound_at
        return conversation


async def setup(owner_engine: AsyncEngine, role: MembershipRole = OWNER):
    tenant = await new_tenant(owner_engine, "Panaderia")
    person = await create_user(owner_engine, (tenant, role))
    return tenant, person


async def test_bandeja_y_mensajes(client, owner_engine) -> None:
    tenant, owner = await setup(owner_engine)
    older = await chat(owner_engine, tenant, ("Buenas",), name="Pedro")
    newer = await chat(owner_engine, tenant)
    async with AsyncSession(owner_engine) as s, s.begin():
        row = await s.get(Conversation, older.id)
        row.last_message_at = datetime.now(UTC) - timedelta(hours=1)
    headers = await headers_for(client, owner)

    inbox = (await client.get("/conversations", headers=headers)).json()
    assert [c["id"] for c in inbox] == [str(newer.id), str(older.id)]
    first = inbox[0]
    assert first["contact"]["name"] == "María"
    assert first["last_message"] == {"text": "¿Tienen pan?", "direction": "inbound"}
    assert first["waiting_reply"] is True and first["mode"] == "bot"

    page2 = await client.get(
        "/conversations",
        params={"limit": 1, "before": inbox[0]["last_message_at"]},
        headers=headers,
    )
    assert [c["id"] for c in page2.json()] == [str(older.id)]
    found = await client.get("/conversations", params={"q": "pedr"}, headers=headers)
    assert [c["id"] for c in found.json()] == [str(older.id)]

    messages = (await client.get(f"/conversations/{newer.id}/messages", headers=headers)).json()
    assert [m["text"] for m in messages] == ["Hola", "¿Tienen pan?"]  # el más viejo arriba
    earlier = await client.get(
        f"/conversations/{newer.id}/messages",
        params={"before": messages[1]["created_at"]},
        headers=headers,
    )
    assert [m["text"] for m in earlier.json()] == ["Hola"]


async def test_contestar_toma_la_conversacion(client, owner_engine, queue) -> None:
    tenant, agent = await setup(owner_engine, AGENT)
    conversation = await chat(owner_engine, tenant)
    headers = await headers_for(client, agent)

    response = await client.post(
        f"/conversations/{conversation.id}/messages",
        json={"text": "  Sí, ¡recién salido!  "},
        headers=headers,
    )
    assert response.status_code == 202
    message_id = response.json()["message_id"]
    assert queue.jobs and queue.jobs[0][1] == message_id  # se encoló el envío

    detail = (await client.get(f"/conversations/{conversation.id}", headers=headers)).json()
    assert detail["mode"] == "human"
    assert detail["assigned"]["user_id"] == str(agent.id)
    assert detail["waiting_reply"] is False
    messages = (
        await client.get(f"/conversations/{conversation.id}/messages", headers=headers)
    ).json()
    assert messages[-1]["text"] == "Sí, ¡recién salido!"
    assert messages[-1]["sender_type"] == "human_panel" and messages[-1]["sender_name"] == "Andre"
    assert messages[-1]["status"] == "queued"

    async with AsyncSession(owner_engine) as s:
        pending = await s.scalar(
            select(Message.id).where(
                Message.conversation_id == conversation.id,
                Message.direction == MessageDirection.INBOUND,
                Message.processed_at.is_(None),
            )
        )
        handoffs = list(
            await s.scalars(
                select(HandoffEvent.reason).where(HandoffEvent.conversation_id == conversation.id)
            )
        )
    assert pending is None  # el bot ya no contesta esos mensajes
    assert [h.value for h in handoffs] == ["panel"]


async def test_tomar_y_devolver_al_bot(client, owner_engine) -> None:
    tenant, owner = await setup(owner_engine)
    conversation = await chat(owner_engine, tenant)
    headers = await headers_for(client, owner)

    assert (
        await client.post(f"/conversations/{conversation.id}/take", headers=headers)
    ).status_code == 204
    mine = (await client.get("/conversations", params={"mine": True}, headers=headers)).json()
    assert [c["id"] for c in mine] == [str(conversation.id)]
    human = (await client.get("/conversations", params={"mode": "human"}, headers=headers)).json()
    assert len(human) == 1

    assert (
        await client.post(f"/conversations/{conversation.id}/release", headers=headers)
    ).status_code == 204
    detail = (await client.get(f"/conversations/{conversation.id}", headers=headers)).json()
    assert detail["mode"] == "bot" and detail["assigned"] is None
    async with AsyncSession(owner_engine) as s:
        rows = await s.execute(
            select(HandoffEvent.from_mode, HandoffEvent.to_mode, HandoffEvent.reason)
            .where(HandoffEvent.conversation_id == conversation.id)
            .order_by(HandoffEvent.created_at)
        )
        events = [(a.value, b.value, r.value) for a, b, r in rows]
    assert events == [("bot", "human", "panel"), ("human", "bot", "returned")]


async def test_asignar_solo_dueno_o_administrador(client, owner_engine) -> None:
    tenant, owner = await setup(owner_engine)
    agent = await create_user(owner_engine, (tenant, AGENT))
    outsider = await create_user(owner_engine, (await new_tenant(owner_engine, "Otro"), OWNER))
    conversation = await chat(owner_engine, tenant)
    owner_h, agent_h = await headers_for(client, owner), await headers_for(client, agent)
    url = f"/conversations/{conversation.id}/assign"

    assert (
        await client.post(url, json={"user_id": str(agent.id)}, headers=agent_h)
    ).status_code == 403
    assert (
        await client.post(url, json={"user_id": str(agent.id)}, headers=owner_h)
    ).status_code == 204
    detail = (await client.get(f"/conversations/{conversation.id}", headers=agent_h)).json()
    assert detail["assigned"]["user_id"] == str(agent.id)
    assert detail["mode"] == "bot"  # asignar no le quita la conversación al bot
    bad = await client.post(url, json={"user_id": str(outsider.id)}, headers=owner_h)
    assert bad.status_code == 422
    assert (await client.post(url, json={"user_id": None}, headers=owner_h)).status_code == 204


async def test_cerrar(client, owner_engine) -> None:
    tenant, owner = await setup(owner_engine)
    conversation = await chat(owner_engine, tenant)
    headers = await headers_for(client, owner)

    assert (
        await client.post(f"/conversations/{conversation.id}/close", headers=headers)
    ).status_code == 204
    assert (await client.get("/conversations", headers=headers)).json() == []
    closed = (
        await client.get("/conversations", params={"status": "closed"}, headers=headers)
    ).json()
    assert [c["id"] for c in closed] == [str(conversation.id)]
    reply = await client.post(
        f"/conversations/{conversation.id}/messages", json={"text": "hola"}, headers=headers
    )
    assert reply.status_code == 409
    assert (
        await client.post(f"/conversations/{conversation.id}/take", headers=headers)
    ).status_code == 409


async def test_mensajes_invalidos(client, owner_engine) -> None:
    tenant, owner = await setup(owner_engine)
    conversation = await chat(owner_engine, tenant)
    headers = await headers_for(client, owner)
    url = f"/conversations/{conversation.id}/messages"
    assert (await client.post(url, json={"text": "   "}, headers=headers)).status_code == 422
    assert (await client.post(url, json={"text": "x" * 5000}, headers=headers)).status_code == 422
    missing = await client.post(
        f"/conversations/{uuid.uuid4()}/messages", json={"text": "hola"}, headers=headers
    )
    assert missing.status_code == 404


async def test_un_negocio_no_ve_la_bandeja_de_otro(client, owner_engine) -> None:
    tenant_a, owner_a = await setup(owner_engine)
    tenant_b, _ = await setup(owner_engine)
    conversation_b = await chat(owner_engine, tenant_b)
    await chat(owner_engine, tenant_a)
    headers = await headers_for(client, owner_a)

    assert str(conversation_b.id) not in {
        c["id"] for c in (await client.get("/conversations", headers=headers)).json()
    }
    for method, path, body in (
        ("GET", f"/conversations/{conversation_b.id}", None),
        ("GET", f"/conversations/{conversation_b.id}/messages", None),
        ("POST", f"/conversations/{conversation_b.id}/messages", {"text": "hola"}),
        ("POST", f"/conversations/{conversation_b.id}/take", None),
        ("POST", f"/conversations/{conversation_b.id}/close", None),
    ):
        response = await client.request(method, path, json=body, headers=headers)
        assert response.status_code == 404, path
    async with AsyncSession(owner_engine) as s:
        row = await s.get(Conversation, conversation_b.id)
    assert row.mode is ConversationMode.BOT
