"""Agente del negocio y horario desde el panel (bloque 5.5)."""

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.auth import get_redis
from app.api.webhooks import get_session_factory
from app.core.config import get_settings
from app.main import create_app
from app.models import Agent, AuditLog
from app.models.enums import MembershipRole
from app.services import hours
from app.services.members import InvalidInputError
from tests.test_auth import SECRET, Person, bearer, create_user, login, new_tenant

OWNER, ADMIN, AGENT = MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.AGENT
NEW_PROMPT = "Eres el asistente de la Panadería. Ofrece siempre el pan del día con cariño."


@pytest.fixture(autouse=True)
def jwt_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(get_settings(), "jwt_secret", SecretStr(SECRET))
    yield


@pytest.fixture
async def client(app_engine: AsyncEngine) -> AsyncIterator[httpx.AsyncClient]:
    factory = async_sessionmaker(app_engine, expire_on_commit=False)
    redis = Redis.from_url(get_settings().redis_url)
    app = create_app()
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_redis] = lambda: redis
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://panel.test") as c:
        yield c
    keys = [k async for k in redis.scan_iter("saint:login-fail:*")]
    if keys:
        await redis.delete(*keys)
    await redis.aclose()


async def headers_for(client: httpx.AsyncClient, person: Person) -> dict[str, str]:
    return bearer(await login(client, person.email))


async def setup(owner_engine: AsyncEngine, role: MembershipRole = OWNER):
    tenant = await new_tenant(owner_engine, "Panaderia")
    return tenant, await create_user(owner_engine, (tenant, role))


# --- Agente ---


async def test_ver_el_agente_lo_crea_si_falta(client, owner_engine) -> None:
    tenant, owner = await setup(owner_engine)
    headers = await headers_for(client, owner)
    agent = (await client.get("/agent", headers=headers)).json()
    assert agent["version"] == 1 and agent["can_edit"] is True
    assert tenant.name in agent["instructions"]
    assert agent["model"] == "claude-haiku-5-5"
    default = (await client.get("/agent/default-instructions", headers=headers)).json()
    assert default["instructions"] == agent["instructions"]


async def test_cambiar_instrucciones_crea_versiones(client, owner_engine) -> None:
    tenant, owner = await setup(owner_engine)
    headers = await headers_for(client, owner)
    original = (await client.get("/agent", headers=headers)).json()["instructions"]

    response = await client.patch(
        "/agent", json={"instructions": f"  {NEW_PROMPT}  ", "name": "Panita"}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["version"] == 2 and response.json()["name"] == "Panita"
    # El bot lee la versión nueva de la tabla agents.
    async with AsyncSession(owner_engine) as s:
        prompt = await s.scalar(select(Agent.system_prompt).where(Agent.tenant_id == tenant.id))
    assert prompt == NEW_PROMPT

    # Guardar lo mismo no crea otra versión.
    same = await client.patch("/agent", json={"instructions": NEW_PROMPT}, headers=headers)
    assert same.json()["version"] == 2

    versions = (await client.get("/agent/versions", headers=headers)).json()
    assert [(v["version"], v["is_current"]) for v in versions] == [(2, True), (1, False)]
    assert versions[0]["created_by"] == "Andre" and versions[1]["created_by"] is None
    assert versions[1]["instructions"] == original

    restored = await client.post("/agent/versions/1/restore", headers=headers)
    assert restored.status_code == 200
    assert restored.json()["version"] == 3 and restored.json()["instructions"] == original
    assert (await client.post("/agent/versions/9/restore", headers=headers)).status_code == 404

    async with AsyncSession(owner_engine) as s:
        actions = list(
            await s.scalars(
                select(AuditLog.action)
                .where(AuditLog.tenant_id == tenant.id)
                .order_by(AuditLog.created_at)
            )
        )
    assert actions == ["agent.rename", "agent.update", "agent.update"]


async def test_instrucciones_invalidas(client, owner_engine) -> None:
    _, owner = await setup(owner_engine)
    headers = await headers_for(client, owner)
    for body in ({"instructions": "corto"}, {"instructions": "x" * 8001}, {"name": "  "}):
        assert (await client.patch("/agent", json=body, headers=headers)).status_code == 422


async def test_el_agente_ve_pero_no_cambia(client, owner_engine) -> None:
    tenant, _ = await setup(owner_engine)
    agent = await create_user(owner_engine, (tenant, AGENT))
    headers = await headers_for(client, agent)
    assert (await client.get("/agent", headers=headers)).json()["can_edit"] is False
    assert (await client.get("/agent/versions", headers=headers)).status_code == 200
    patch = await client.patch("/agent", json={"instructions": NEW_PROMPT}, headers=headers)
    assert patch.status_code == 403
    assert (await client.post("/agent/versions/1/restore", headers=headers)).status_code == 403
    put = await client.put("/hours", json={"days": {"mon": [["08:00", "12:00"]]}}, headers=headers)
    assert put.status_code == 403


async def test_un_negocio_no_ve_el_agente_de_otro(client, owner_engine) -> None:
    _, owner_a = await setup(owner_engine)
    _, owner_b = await setup(owner_engine)
    headers_a, headers_b = await headers_for(client, owner_a), await headers_for(client, owner_b)
    await client.patch("/agent", json={"instructions": NEW_PROMPT}, headers=headers_b)
    assert (await client.get("/agent", headers=headers_a)).json()["instructions"] != NEW_PROMPT
    assert len((await client.get("/agent/versions", headers=headers_a)).json()) == 1


# --- Horario ---


async def test_guardar_y_leer_el_horario(client, owner_engine) -> None:
    _, owner = await setup(owner_engine)
    headers = await headers_for(client, owner)
    assert (await client.get("/hours", headers=headers)).json()["days"] == {}  # siempre abierto

    body = {
        "days": {"mon": [["14:00", "18:00"], ["08:00", "12:00"]], "sat": [["09:00", "13:00"]]},
        "after_hours_message": "  Ya cerramos.   Mañana te respondemos. ",
    }
    response = await client.put("/hours", json=body, headers=headers)
    assert response.status_code == 200
    saved = response.json()
    assert saved["days"]["mon"] == [["08:00", "12:00"], ["14:00", "18:00"]]  # ordenado
    assert saved["days"]["sun"] == []
    assert saved["after_hours_message"] == "Ya cerramos. Mañana te respondemos."
    assert saved["timezone"] == "America/Caracas"
    assert (await client.get("/hours", headers=headers)).json()["days"] == saved["days"]

    cleared = await client.put("/hours", json={"days": {}}, headers=headers)
    assert cleared.json()["days"] == {} and cleared.json()["open_now"] is True


@pytest.mark.parametrize(
    "days",
    [
        {"lunes": [["08:00", "12:00"]]},
        {"mon": [["12:00", "08:00"]]},
        {"mon": [["08:00", "12:00"], ["11:00", "13:00"]]},
        {"mon": [["8", "12:00"]]},
        {"mon": [["08:00", "25:00"]]},
        {"mon": [["08:00"]]},
        {"mon": [["01:00", "02:00"], ["03:00", "04:00"], ["05:00", "06:00"], ["07:00", "08:00"]]},
    ],
)
def test_horarios_invalidos(days) -> None:
    with pytest.raises(InvalidInputError):
        hours.validate(days, None)


def test_esta_abierto() -> None:
    data = hours.validate({"mon": [["08:00", "12:00"], ["14:00", "24:00"]]}, None)
    tz = "America/Caracas"  # UTC-4
    monday = datetime(2026, 11, 16, tzinfo=UTC)  # lunes
    assert hours.is_open(data, tz, monday.replace(hour=12)) is True  # 08:00 en Caracas
    assert hours.is_open(data, tz, monday.replace(hour=16, minute=30)) is False  # 12:30
    assert hours.is_open(data, tz, monday.replace(hour=18)) is True  # 14:00
    assert hours.is_open(data, tz, monday.replace(hour=11, minute=59)) is False  # 07:59
    # 23:30 del lunes en Caracas = 03:30 UTC del martes.
    assert hours.is_open(data, tz, datetime(2026, 11, 17, 3, 30, tzinfo=UTC)) is True
    assert hours.is_open(data, tz, datetime(2026, 11, 17, 13, 0, tzinfo=UTC)) is False  # martes
    assert hours.is_open({}, tz, monday) is True  # sin horario
