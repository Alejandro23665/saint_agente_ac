"""Avisos en tiempo real para el panel (bloque 5.6)."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.api.auth import get_redis
from app.api.events import event_stream
from app.api.webhooks import get_queue, get_session_factory
from app.core import events
from app.core.config import get_settings
from app.main import create_app
from app.models.enums import MembershipRole
from app.services.members import Member
from app.services.webhook_processing import Outcome, Result
from app.workers.ingest import _notify_panel
from tests.test_auth import SECRET, bearer, create_user, login, new_tenant
from tests.test_inbox import FakeQueue, chat

OWNER = MembershipRole.OWNER
LATER = datetime.now(UTC) + timedelta(hours=1)


@pytest.fixture(autouse=True)
def jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(get_settings(), "jwt_secret", SecretStr(SECRET))
    yield


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    r = Redis.from_url(get_settings().redis_url)
    yield r
    keys = [k async for k in r.scan_iter("saint:login-fail:*")]
    if keys:
        await r.delete(*keys)
    await r.aclose()


@pytest.fixture
def factory(app_engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(app_engine, expire_on_commit=False)


@pytest.fixture
async def client(factory, redis: Redis) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_queue] = FakeQueue
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://panel.test") as c:
        yield c


async def member_of(owner_engine: AsyncEngine) -> Member:
    tenant = await new_tenant(owner_engine, "Panaderia")
    person = await create_user(owner_engine, (tenant, OWNER))
    return Member(person.id, tenant.id, OWNER)


async def never() -> bool:
    return False


def parse(chunk: str) -> tuple[str, dict]:
    lines = dict(line.split(": ", 1) for line in chunk.strip().splitlines())
    return lines["event"], json.loads(lines["data"])


async def first_message(pubsub) -> dict | None:
    """El primer aviso publicado (la confirmación de suscripción se salta)."""
    for _ in range(20):
        message = await pubsub.get_message(timeout=0.1)
        if message is not None:
            return json.loads(message["data"])
    return None


async def next_chunk(stream: AsyncIterator[str]) -> str:
    return await asyncio.wait_for(anext(stream), timeout=5)


async def test_llegan_los_avisos_del_negocio_y_no_los_de_otro(owner_engine, redis, factory) -> None:
    member = await member_of(owner_engine)
    other = await member_of(owner_engine)
    stream = event_stream(member, LATER, redis, factory, never, poll_seconds=0.05)
    try:
        assert parse(await next_chunk(stream)) == ("ready", {"type": "ready"})
        conversation_id = uuid.uuid4()
        await events.publish(redis, other.tenant_id, events.INBOX_CHANGED)
        await events.publish(
            redis, member.tenant_id, events.CONVERSATION_CHANGED, conversation_id=conversation_id
        )
        event_type, data = parse(await next_chunk(stream))
        # El primero que llega es el propio: el del otro negocio nunca aparece.
        assert event_type == "conversation.changed"
        assert data == {"type": "conversation.changed", "conversation_id": str(conversation_id)}
    finally:
        await stream.aclose()


async def test_ping_y_corte_cuando_se_pierde_el_acceso(owner_engine, redis, factory) -> None:
    member = await member_of(owner_engine)
    stream = event_stream(member, LATER, redis, factory, never, ping_seconds=0.1, poll_seconds=0.05)
    try:
        await next_chunk(stream)  # ready
        assert await next_chunk(stream) == ": ping\n\n"
        async with owner_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM tenant_memberships WHERE user_id = :u AND tenant_id = :t"),
                {"u": member.user_id, "t": member.tenant_id},
            )
        assert parse(await next_chunk(stream))[0] == "session.expired"
        with pytest.raises(StopAsyncIteration):
            await next_chunk(stream)
    finally:
        await stream.aclose()


async def test_corte_cuando_vence_el_pase(owner_engine, redis, factory) -> None:
    member = await member_of(owner_engine)
    expired = datetime.now(UTC) - timedelta(seconds=1)
    stream = event_stream(member, expired, redis, factory, never, ping_seconds=0.1)
    try:
        await next_chunk(stream)  # ready
        assert parse(await next_chunk(stream))[0] == "session.expired"
    finally:
        await stream.aclose()


async def test_sin_pase_no_hay_avisos(client) -> None:
    assert (await client.get("/events")).status_code == 401


async def test_redis_caido_no_rompe_la_accion() -> None:
    broken = Redis.from_url("redis://127.0.0.1:1", socket_connect_timeout=0.2)
    await events.publish(broken, uuid.uuid4(), events.INBOX_CHANGED)  # no lanza
    await broken.aclose()


async def test_tomar_una_conversacion_avisa_al_panel(client, owner_engine, redis) -> None:
    tenant = await new_tenant(owner_engine, "Panaderia")
    person = await create_user(owner_engine, (tenant, OWNER))
    conversation = await chat(owner_engine, tenant)
    headers = bearer(await login(client, person.email))
    pubsub = redis.pubsub(ignore_subscribe_messages=True)
    await pubsub.subscribe(events.channel(tenant.id))
    try:
        response = await client.post(f"/conversations/{conversation.id}/take", headers=headers)
        assert response.status_code == 204
        assert await first_message(pubsub) == {
            "type": "conversation.changed",
            "conversation_id": str(conversation.id),
        }
    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (Result(Outcome.PROCESSED, event_type="connection.update"), "numbers.changed"),
        (
            Result(
                Outcome.PROCESSED,
                event_type="messages.upsert",
                reply_conversation_id=uuid.UUID(int=7),
            ),
            "conversation.changed",
        ),
        (Result(Outcome.PROCESSED, event_type="messages.update"), "inbox.changed"),
    ],
)
async def test_el_worker_avisa_segun_el_tipo(redis, result, expected) -> None:
    tenant_id = uuid.uuid4()
    pubsub = redis.pubsub(ignore_subscribe_messages=True)
    await pubsub.subscribe(events.channel(tenant_id))
    try:
        await _notify_panel({"redis": redis}, tenant_id, result)
        assert (await first_message(pubsub))["type"] == expected
    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()
