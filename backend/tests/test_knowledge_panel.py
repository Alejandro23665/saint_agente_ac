"""Conocimiento del negocio desde el panel (bloque 5.7). OpenRouter se simula."""

import json
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.auth import get_redis
from app.api.webhooks import get_queue, get_session_factory
from app.core import events
from app.core.config import get_settings
from app.core.queue import EMBED_KNOWLEDGE_SOURCE
from app.integrations.embeddings import EmbeddingError
from app.main import create_app
from app.models import AuditLog, KnowledgeChunk
from app.models.enums import MembershipRole
from app.services.knowledge import MISSING_KEY
from app.services.knowledge_panel import MAX_PANEL_CHARS
from app.workers import ingest
from tests.test_auth import SECRET, Person, bearer, create_user, login, new_tenant
from tests.test_inbox import FakeQueue
from tests.test_knowledge import HORARIOS, FakeEmbedder

OWNER, ADMIN, AGENT = MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.AGENT


@pytest.fixture(autouse=True)
def jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(get_settings(), "jwt_secret", SecretStr(SECRET))
    yield


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
def factory(app_engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(app_engine, expire_on_commit=False)


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    r = Redis.from_url(get_settings().redis_url)
    yield r
    keys = [k async for k in r.scan_iter("saint:login-fail:*")]
    if keys:
        await r.delete(*keys)
    await r.aclose()


@pytest.fixture
async def client(factory, redis, queue) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_queue] = lambda: queue
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://panel.test") as c:
        yield c


async def headers_for(client: httpx.AsyncClient, person: Person) -> dict[str, str]:
    return bearer(await login(client, person.email))


async def setup(owner_engine: AsyncEngine, role: MembershipRole = OWNER):
    tenant = await new_tenant(owner_engine, "Panaderia")
    return tenant, await create_user(owner_engine, (tenant, role))


async def run_worker(queue: FakeQueue, factory, redis, embedder) -> list[str]:
    """Hace lo que haría el worker con los trabajos encolados (y vacía la cola)."""
    ctx = {"factory": factory, "redis": redis, "embedder": embedder}
    results = []
    for name, source_id, tenant_id in queue.jobs:
        assert name == EMBED_KNOWLEDGE_SOURCE
        results.append(await ingest.embed_knowledge_source(ctx, source_id, tenant_id))
    queue.jobs.clear()
    return results


async def test_subir_texto_y_queda_listo(client, owner_engine, queue, factory, redis) -> None:
    tenant, owner = await setup(owner_engine)
    headers = await headers_for(client, owner)
    pubsub = redis.pubsub(ignore_subscribe_messages=True)
    await pubsub.subscribe(events.channel(tenant.id))
    try:
        response = await client.post(
            "/knowledge", json={"title": "  Horarios ", "text": HORARIOS}, headers=headers
        )
        assert response.status_code == 202
        body = response.json()
        assert (body["title"], body["status"], body["chunk_count"]) == ("Horarios", "processing", 1)
        assert body["created_by"] == "Andre" and body["already_loaded"] is False
        assert len(queue.jobs) == 1
        # Aviso al panel de que hay un documento nuevo.
        message = None
        for _ in range(20):
            message = await pubsub.get_message(timeout=0.1)
            if message:
                break
        assert json.loads(message["data"]) == {
            "type": "knowledge.changed",
            "source_id": body["id"],
        }
    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()

    assert await run_worker(queue, factory, redis, FakeEmbedder()) == ["ready"]
    detail = (await client.get(f"/knowledge/{body['id']}", headers=headers)).json()
    assert detail["status"] == "ready" and detail["error"] is None
    assert detail["characters"] == len(HORARIOS)
    assert [(c["index"], c["content"], c["has_embedding"]) for c in detail["chunks"]] == [
        (0, HORARIOS, True)
    ]
    listed = (await client.get("/knowledge", headers=headers)).json()
    assert [s["title"] for s in listed] == ["Horarios"]

    # El mismo texto otra vez: no se hace nada.
    again = await client.post(
        "/knowledge", json={"title": "Horarios", "text": HORARIOS}, headers=headers
    )
    assert again.json()["already_loaded"] is True and queue.jobs == []


async def test_sin_clave_falla_y_se_reintenta(client, owner_engine, queue, factory, redis) -> None:
    _, owner = await setup(owner_engine)
    headers = await headers_for(client, owner)
    source_id = (
        await client.post(
            "/knowledge", json={"title": "Horarios", "text": HORARIOS}, headers=headers
        )
    ).json()["id"]

    assert await run_worker(queue, factory, redis, None) == ["failed"]
    failed = (await client.get(f"/knowledge/{source_id}", headers=headers)).json()
    assert (failed["status"], failed["error"]) == ("failed", MISSING_KEY)
    assert failed["chunks"][0]["has_embedding"] is False  # el texto se guarda igual

    # OpenRouter caído: también queda failed, con el error.
    retry = await client.post(f"/knowledge/{source_id}/retry", headers=headers)
    assert retry.status_code == 202 and retry.json()["status"] == "processing"
    down = FakeEmbedder(fail=EmbeddingError("OpenRouter respondió 503"))
    assert await run_worker(queue, factory, redis, down) == ["failed"]

    await client.post(f"/knowledge/{source_id}/retry", headers=headers)
    assert await run_worker(queue, factory, redis, FakeEmbedder()) == ["ready"]
    assert (await client.get(f"/knowledge/{source_id}", headers=headers)).json()["status"] == (
        "ready"
    )
    # Ya listo: no hay nada que reintentar.
    assert (await client.post(f"/knowledge/{source_id}/retry", headers=headers)).status_code == 409


async def test_mismo_titulo_reemplaza_cuando_el_nuevo_esta_listo(
    client, owner_engine, queue, factory, redis
) -> None:
    _, owner = await setup(owner_engine)
    headers = await headers_for(client, owner)
    await client.post("/knowledge", json={"title": "Horarios", "text": HORARIOS}, headers=headers)
    await run_worker(queue, factory, redis, FakeEmbedder())
    nuevo = "Desde diciembre abrimos también los domingos hasta las 18:00."
    await client.post("/knowledge", json={"title": "Horarios", "text": nuevo}, headers=headers)
    # Mientras el nuevo se procesa, el viejo sigue ahí (el bot lo sigue usando).
    assert len((await client.get("/knowledge", headers=headers)).json()) == 2
    await run_worker(queue, factory, redis, FakeEmbedder())
    [only] = (await client.get("/knowledge", headers=headers)).json()
    assert only["status"] == "ready" and only["characters"] == len(nuevo)


async def test_borrar(client, owner_engine, queue, factory, redis) -> None:
    tenant, owner = await setup(owner_engine)
    headers = await headers_for(client, owner)
    source_id = (
        await client.post(
            "/knowledge", json={"title": "Horarios", "text": HORARIOS}, headers=headers
        )
    ).json()["id"]
    await run_worker(queue, factory, redis, FakeEmbedder())
    assert (await client.delete(f"/knowledge/{source_id}", headers=headers)).status_code == 204
    assert (await client.get(f"/knowledge/{source_id}", headers=headers)).status_code == 404
    assert (await client.delete(f"/knowledge/{source_id}", headers=headers)).status_code == 404
    async with AsyncSession(owner_engine) as s:
        assert (
            await s.scalar(
                select(func.count())
                .select_from(KnowledgeChunk)
                .where(KnowledgeChunk.tenant_id == tenant.id)
            )
            == 0
        )
        actions = list(
            await s.scalars(
                select(AuditLog.action)
                .where(AuditLog.tenant_id == tenant.id)
                .order_by(AuditLog.created_at)
            )
        )
    assert actions == ["knowledge.upload", "knowledge.delete"]
    # Si lo borran mientras el worker trabaja, el worker no hace nada.
    await client.post("/knowledge", json={"title": "Otro", "text": HORARIOS}, headers=headers)
    other_id = (await client.get("/knowledge", headers=headers)).json()[0]["id"]
    await client.delete(f"/knowledge/{other_id}", headers=headers)
    assert await run_worker(queue, factory, redis, FakeEmbedder()) == ["skipped"]


async def test_el_agente_ve_pero_no_cambia(client, owner_engine, queue, factory, redis) -> None:
    tenant, owner = await setup(owner_engine)
    agent = await create_user(owner_engine, (tenant, AGENT))
    owner_headers = await headers_for(client, owner)
    source_id = (
        await client.post(
            "/knowledge", json={"title": "Horarios", "text": HORARIOS}, headers=owner_headers
        )
    ).json()["id"]
    await run_worker(queue, factory, redis, None)

    headers = await headers_for(client, agent)
    listed = (await client.get("/knowledge", headers=headers)).json()
    assert listed[0]["can_edit"] is False
    assert (await client.get(f"/knowledge/{source_id}", headers=headers)).status_code == 200
    upload = await client.post(
        "/knowledge", json={"title": "Precios", "text": "Pan: 1 $"}, headers=headers
    )
    assert upload.status_code == 403
    assert (await client.post(f"/knowledge/{source_id}/retry", headers=headers)).status_code == 403
    assert (await client.delete(f"/knowledge/{source_id}", headers=headers)).status_code == 403
    assert queue.jobs == []


async def test_el_administrador_puede_subir(client, owner_engine) -> None:
    _, admin = await setup(owner_engine, ADMIN)
    headers = await headers_for(client, admin)
    response = await client.post(
        "/knowledge", json={"title": "Precios", "text": "Pan: 1 $"}, headers=headers
    )
    assert response.status_code == 202


async def test_textos_invalidos(client, owner_engine) -> None:
    _, owner = await setup(owner_engine)
    headers = await headers_for(client, owner)
    too_long = "palabra " * (MAX_PANEL_CHARS // 8 + 10)
    for body in (
        {"title": "   ", "text": HORARIOS},
        {"title": "Horarios", "text": "  \n\n  "},
        {"title": "x" * 300, "text": HORARIOS},
        {"title": "Horarios", "text": too_long},
    ):
        assert (await client.post("/knowledge", json=body, headers=headers)).status_code == 422


async def test_un_negocio_no_ve_el_conocimiento_de_otro(client, owner_engine) -> None:
    _, owner_a = await setup(owner_engine)
    _, owner_b = await setup(owner_engine)
    headers_a, headers_b = await headers_for(client, owner_a), await headers_for(client, owner_b)
    source_id = (
        await client.post(
            "/knowledge", json={"title": "Horarios", "text": HORARIOS}, headers=headers_b
        )
    ).json()["id"]
    assert (await client.get("/knowledge", headers=headers_a)).json() == []
    assert (await client.get(f"/knowledge/{source_id}", headers=headers_a)).status_code == 404
    assert (await client.delete(f"/knowledge/{source_id}", headers=headers_a)).status_code == 404
    # El mismo texto en otro negocio es otro documento.
    other = await client.post(
        "/knowledge", json={"title": "Horarios", "text": HORARIOS}, headers=headers_a
    )
    assert other.json()["already_loaded"] is False and other.json()["id"] != source_id
