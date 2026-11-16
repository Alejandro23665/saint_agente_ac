"""Números de WhatsApp desde el panel (bloque 5.3), con Evolution simulado."""

import json
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.auth import get_redis
from app.api.numbers import get_evolution
from app.api.webhooks import get_session_factory
from app.core.config import get_settings
from app.integrations.evolution import EvolutionClient
from app.main import create_app
from app.models import AuditLog, ChannelInstance
from app.models.enums import MembershipRole
from app.services.numbers import MAX_NUMBERS_PER_TENANT
from tests.test_auth import SECRET, Person, bearer, create_user, login, new_tenant

OWNER, ADMIN, AGENT = MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.AGENT
QR_PNG = "data:image/png;base64,iVBORw0KGgo="


@pytest.fixture(autouse=True)
def settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    monkeypatch.setattr(get_settings(), "jwt_secret", SecretStr(SECRET))
    yield
    get_settings.cache_clear()


class FakeEvolution:
    """Responde como Evolution v2.3.7 y recuerda lo que le pidieron."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.state = "connecting"
        self.down = False
        self.logout_status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("Evolution apagado")
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.calls.append((request.method, path, body))
        if path == "/instance/create":
            return httpx.Response(
                201,
                json={
                    "instance": {"instanceName": body["instanceName"], "status": "connecting"},
                    "qrcode": {"code": "2@x", "base64": QR_PNG, "count": 1},
                },
            )
        if path.startswith("/instance/connectionState/"):
            return httpx.Response(
                200, json={"instance": {"instanceName": "x", "state": self.state}}
            )
        if path == "/instance/fetchInstances":
            return httpx.Response(
                200, json=[{"name": "x", "ownerJid": "584121234567@s.whatsapp.net"}]
            )
        if path.startswith("/instance/connect/"):
            if self.state == "open":
                return httpx.Response(
                    200, json={"instance": {"instanceName": "x", "state": "open"}}
                )
            code = "ABCD1234" if request.url.params.get("number") else None
            return httpx.Response(200, json={"code": "2@y", "base64": QR_PNG, "pairingCode": code})
        if path.startswith("/settings/set/"):
            return httpx.Response(201, json={"settings": body})
        if path.startswith("/instance/logout/"):
            return httpx.Response(self.logout_status, json={"status": "SUCCESS"})
        if path.startswith("/instance/delete/"):
            return httpx.Response(200, json={"status": "SUCCESS"})
        return httpx.Response(404, json={"response": {"message": ["no simulado"]}})


@pytest.fixture
def evolution() -> FakeEvolution:
    return FakeEvolution()


@pytest.fixture
async def client(
    app_engine: AsyncEngine, evolution: FakeEvolution
) -> AsyncIterator[httpx.AsyncClient]:
    factory = async_sessionmaker(app_engine, expire_on_commit=False)
    redis = Redis.from_url(get_settings().redis_url)

    async def fake_evolution() -> AsyncIterator[EvolutionClient]:
        async with EvolutionClient(
            "http://evolution:8080", "k", transport=httpx.MockTransport(evolution)
        ) as evo:
            yield evo

    app = create_app()
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_evolution] = fake_evolution
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://panel.test") as c:
        yield c
    keys = [k async for k in redis.scan_iter("saint:login-fail:*")]
    if keys:
        await redis.delete(*keys)
    await redis.aclose()


async def headers_for(client: httpx.AsyncClient, person: Person) -> dict[str, str]:
    return bearer(await login(client, person.email))


async def business(owner_engine: AsyncEngine, role: MembershipRole = OWNER):
    tenant = await new_tenant(owner_engine, "Panaderia")
    person = await create_user(owner_engine, (tenant, role))
    return tenant, person


async def actions(owner_engine: AsyncEngine, tenant_id) -> list[str]:
    async with AsyncSession(owner_engine) as s:
        rows = await s.scalars(
            select(AuditLog.action)
            .where(AuditLog.tenant_id == tenant_id)
            .order_by(AuditLog.created_at)
        )
        return list(rows)


async def test_conectar_un_numero_con_qr(client, owner_engine, evolution) -> None:
    tenant, owner = await business(owner_engine)
    headers = await headers_for(client, owner)

    response = await client.post("/numbers", json={}, headers=headers)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["qr"]["image"] == QR_PNG
    assert body["number"]["status"] == "qr_pending" and body["number"]["phone"] is None
    _, path, sent = evolution.calls[0]
    assert path == "/instance/create" and sent["groupsIgnore"] is True

    listed = (await client.get("/numbers", headers=headers)).json()
    assert [n["id"] for n in listed] == [body["number"]["id"]]
    # Nada secreto sale por la API.
    assert "token" not in response.text and "public_id" not in response.text
    assert await actions(owner_engine, tenant.id) == ["number.connect"]


async def test_al_escanear_el_estado_se_actualiza(client, owner_engine, evolution) -> None:
    _, owner = await business(owner_engine)
    headers = await headers_for(client, owner)
    number_id = (await client.post("/numbers", json={}, headers=headers)).json()["number"]["id"]

    evolution.state = "open"
    response = await client.get(f"/numbers/{number_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "open"
    assert response.json()["phone"] == "+584121234567"
    assert response.json()["last_connected_at"] is not None
    # Ya conectado: no se pide otro QR.
    assert (
        await client.post(f"/numbers/{number_id}/qr", json={}, headers=headers)
    ).status_code == 409


async def test_otro_qr_y_codigo_de_vinculacion(client, owner_engine, evolution) -> None:
    _, owner = await business(owner_engine)
    headers = await headers_for(client, owner)
    number_id = (await client.post("/numbers", json={}, headers=headers)).json()["number"]["id"]

    plain = await client.post(f"/numbers/{number_id}/qr", json={}, headers=headers)
    assert plain.status_code == 200
    assert plain.json()["qr"] == {"image": QR_PNG, "pairing_code": None}

    coded = await client.post(
        f"/numbers/{number_id}/qr", json={"phone": "+58 412-123 4567"}, headers=headers
    )
    assert coded.json()["qr"]["pairing_code"] == "ABCD1234"
    assert evolution.calls[-1][1].startswith("/instance/connect/")
    bad = await client.post(f"/numbers/{number_id}/qr", json={"phone": "123"}, headers=headers)
    assert bad.status_code == 422


async def test_grupos_desconectar_y_borrar(client, owner_engine, evolution) -> None:
    tenant, owner = await business(owner_engine)
    headers = await headers_for(client, owner)
    number_id = (await client.post("/numbers", json={}, headers=headers)).json()["number"]["id"]

    groups = await client.patch(
        f"/numbers/{number_id}", json={"groups_enabled": True}, headers=headers
    )
    assert groups.status_code == 200 and groups.json()["groups_enabled"] is True
    method, path, sent = evolution.calls[-1]
    assert path.startswith("/settings/set/") and sent["groupsIgnore"] is False

    evolution.logout_status = 400  # el teléfono ya no estaba vinculado: no es un error
    off = await client.post(f"/numbers/{number_id}/disconnect", headers=headers)
    assert off.status_code == 200 and off.json()["status"] == "close"

    assert (await client.delete(f"/numbers/{number_id}", headers=headers)).status_code == 204
    assert evolution.calls[-1][1].startswith("/instance/delete/")
    assert (await client.get("/numbers", headers=headers)).json() == []
    assert (await client.get(f"/numbers/{number_id}", headers=headers)).status_code == 404
    # La fila se conserva (borrado lógico) sin el token.
    async with AsyncSession(owner_engine) as s:
        row = await s.get(ChannelInstance, number_id)
    assert row.deleted_at is not None and row.instance_token_encrypted is None
    assert await actions(owner_engine, tenant.id) == [
        "number.connect",
        "number.groups",
        "number.disconnect",
        "number.delete",
    ]


async def test_permisos_por_rol(client, owner_engine) -> None:
    tenant, owner = await business(owner_engine)
    admin = await create_user(owner_engine, (tenant, ADMIN))
    agent = await create_user(owner_engine, (tenant, AGENT))
    owner_h, admin_h, agent_h = [await headers_for(client, p) for p in (owner, admin, agent)]
    number_id = (await client.post("/numbers", json={}, headers=admin_h)).json()["number"]["id"]

    # El agente ve, pero no toca.
    assert len((await client.get("/numbers", headers=agent_h)).json()) == 1
    assert (await client.post("/numbers", json={}, headers=agent_h)).status_code == 403
    assert (
        await client.post(f"/numbers/{number_id}/qr", json={}, headers=agent_h)
    ).status_code == 403
    assert (
        await client.post(f"/numbers/{number_id}/disconnect", headers=agent_h)
    ).status_code == 403
    # El administrador gestiona, pero solo el dueño borra.
    assert (await client.delete(f"/numbers/{number_id}", headers=admin_h)).status_code == 403
    assert (await client.delete(f"/numbers/{number_id}", headers=owner_h)).status_code == 204


async def test_tope_de_numeros(client, owner_engine) -> None:
    _, owner = await business(owner_engine)
    headers = await headers_for(client, owner)
    for _ in range(MAX_NUMBERS_PER_TENANT):
        assert (await client.post("/numbers", json={}, headers=headers)).status_code == 201
    response = await client.post("/numbers", json={}, headers=headers)
    assert response.status_code == 409


async def test_evolution_apagado(client, owner_engine, evolution) -> None:
    _, owner = await business(owner_engine)
    headers = await headers_for(client, owner)
    evolution.down = True
    response = await client.post("/numbers", json={}, headers=headers)
    assert response.status_code == 502
    assert "WhatsApp" in response.json()["detail"]
    assert (await client.get("/numbers", headers=headers)).json() == []  # no quedó nada a medias


async def test_un_negocio_no_ve_los_numeros_de_otro(client, owner_engine) -> None:
    _, owner_a = await business(owner_engine)
    _, owner_b = await business(owner_engine)
    headers_a = await headers_for(client, owner_a)
    headers_b = await headers_for(client, owner_b)
    number_b = (await client.post("/numbers", json={}, headers=headers_b)).json()["number"]["id"]

    assert (await client.get("/numbers", headers=headers_a)).json() == []
    for method, path in (
        ("GET", f"/numbers/{number_b}"),
        ("POST", f"/numbers/{number_b}/disconnect"),
        ("DELETE", f"/numbers/{number_b}"),
    ):
        assert (await client.request(method, path, headers=headers_a)).status_code == 404
