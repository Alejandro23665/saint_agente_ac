"""Inicio de sesión del panel (bloque 5.1): contraseñas, pases JWT, sesiones y freno."""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.auth import REFRESH_COOKIE, get_redis
from app.api.webhooks import get_session_factory
from app.core import auth_tokens
from app.core.auth_tokens import (
    InvalidTokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.core.config import get_settings
from app.main import create_app
from app.models import RefreshToken, Tenant, TenantMembership, User
from app.models.enums import MembershipRole
from tests.factories import create_tenant

SECRET = "s" * 40
PASSWORD = "pan-dulce-2026"


@pytest.fixture(autouse=True)
def jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "jwt_secret", SecretStr(SECRET))


@pytest.fixture
def factory(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(app_engine, expire_on_commit=False)


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    client = Redis.from_url(get_settings().redis_url)

    # Solo se borran los contadores de intentos (nunca toda la base: puede tener la cola).
    async def clear() -> None:
        keys = [k async for k in client.scan_iter("saint:login-fail:*")]
        if keys:
            await client.delete(*keys)

    await clear()
    yield client
    await clear()
    await client.aclose()


@pytest.fixture
async def client(factory, redis) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_redis] = lambda: redis
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://panel.test") as c:
        yield c


@dataclass
class Person:
    id: uuid.UUID
    email: str


async def create_user(
    owner_engine: AsyncEngine, *tenants: tuple[Tenant, MembershipRole], active: bool = True
) -> Person:
    email = f"user-{uuid.uuid4().hex[:8]}@saint.test"
    async with AsyncSession(owner_engine) as s, s.begin():
        user = User(
            email=email,
            full_name="Andre",
            password_hash=hash_password(PASSWORD),
            is_active=active,
        )
        s.add(user)
        await s.flush()
        for tenant, role in tenants:
            s.add(TenantMembership(tenant_id=tenant.id, user_id=user.id, role=role))
        return Person(user.id, email)


async def new_tenant(owner_engine: AsyncEngine, name: str) -> Tenant:
    async with AsyncSession(owner_engine, expire_on_commit=False) as s, s.begin():
        return await create_tenant(s, name)


async def login(client: httpx.AsyncClient, email: str, password: str = PASSWORD, **extra):
    return await client.post("/auth/login", json={"email": email, "password": password, **extra})


def bearer(response: httpx.Response) -> dict[str, str]:
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# --- Contraseñas y pases ---


def test_contrasena_con_argon2() -> None:
    hashed = hash_password(PASSWORD)
    assert hashed.startswith("$argon2") and PASSWORD not in hashed
    assert verify_password(PASSWORD, hashed)
    assert not verify_password("otra-cosa", hashed)
    assert not verify_password(PASSWORD, None)  # usuario que no existe
    assert not verify_password(PASSWORD, "no-es-un-hash")


def test_pase_de_acceso() -> None:
    user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    token, expires_at = create_access_token(user_id, tenant_id, "owner")
    claims = decode_access_token(token)
    assert (claims.user_id, claims.tenant_id, claims.role) == (user_id, tenant_id, "owner")
    assert timedelta(minutes=14) < expires_at - datetime.now(UTC) <= timedelta(minutes=15)


def test_pase_vencido_alterado_o_con_otra_clave() -> None:
    old, _ = create_access_token(uuid.uuid4(), now=datetime.now(UTC) - timedelta(minutes=16))
    good, _ = create_access_token(uuid.uuid4())
    other, _ = create_access_token(uuid.uuid4(), secret="o" * 40)
    tampered = good[:-2] + ("AA" if not good.endswith("AA") else "BB")
    for token in (old, other, tampered, "no-es-un-jwt"):
        with pytest.raises(InvalidTokenError):
            decode_access_token(token)


def test_sin_clave_jwt_no_arranca(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "jwt_secret", SecretStr("corta"))
    with pytest.raises(ValueError, match="JWT_SECRET"):
        auth_tokens.create_access_token(uuid.uuid4())


# --- Entrar ---


async def test_entrar_con_un_negocio_lo_elige_solo(client, owner_engine) -> None:
    tenant = await new_tenant(owner_engine, "Panaderia")
    person = await create_user(owner_engine, (tenant, MembershipRole.OWNER))

    response = await login(client, person.email.upper())  # el correo no distingue mayúsculas
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == person.email
    assert body["active_tenant"]["id"] == str(tenant.id)
    assert body["active_tenant"]["role"] == "owner"
    claims = decode_access_token(body["access_token"])
    assert (claims.user_id, claims.tenant_id) == (person.id, tenant.id)

    cookie = response.headers["set-cookie"]
    assert REFRESH_COOKIE in cookie and "HttpOnly" in cookie and "Path=/auth" in cookie
    assert "SameSite=strict" in cookie and "expires=" in cookie.lower()

    me = await client.get("/auth/me", headers=bearer(response))
    assert me.status_code == 200 and me.json()["active_tenant"]["name"] == tenant.name


async def test_varios_negocios_hay_que_elegir(client, owner_engine) -> None:
    a = await new_tenant(owner_engine, "Bodega")
    b = await new_tenant(owner_engine, "Ferreteria")
    other = await new_tenant(owner_engine, "Ajeno")
    person = await create_user(owner_engine, (a, MembershipRole.OWNER), (b, MembershipRole.AGENT))

    response = await login(client, person.email)
    body = response.json()
    assert body["active_tenant"] is None
    assert [t["name"] for t in body["tenants"]] == [a.name, b.name]

    chosen = await client.post(
        "/auth/select-tenant", json={"tenant_id": str(b.id)}, headers=bearer(response)
    )
    assert chosen.status_code == 200
    assert decode_access_token(chosen.json()["access_token"]).role == "agent"

    denied = await client.post(
        "/auth/select-tenant", json={"tenant_id": str(other.id)}, headers=bearer(response)
    )
    assert denied.status_code == 403


async def test_datos_incorrectos_mismo_mensaje(client, owner_engine) -> None:
    person = await create_user(owner_engine)
    inactive = await create_user(owner_engine, active=False)
    for email, password in (
        (person.email, "contraseña-mala"),
        ("nadie@saint.test", PASSWORD),
        (inactive.email, PASSWORD),
    ):
        response = await login(client, email, password)
        assert response.status_code == 401
        assert response.json()["detail"] == "Correo o contraseña incorrectos"
        assert "set-cookie" not in response.headers


async def test_cinco_fallos_bloquean_15_minutos(client, owner_engine) -> None:
    person = await create_user(owner_engine)
    for _ in range(5):
        assert (await login(client, person.email, "mala")).status_code == 401
    blocked = await login(client, person.email)  # aunque ahora la contraseña sea buena
    assert blocked.status_code == 429
    assert 0 < int(blocked.headers["retry-after"]) <= 15 * 60


async def test_un_acierto_limpia_los_fallos(client, owner_engine) -> None:
    person = await create_user(owner_engine)
    for _ in range(4):
        await login(client, person.email, "mala")
    assert (await login(client, person.email)).status_code == 200
    for _ in range(4):
        await login(client, person.email, "mala")
    assert (await login(client, person.email)).status_code == 200


async def test_sin_recordar_la_cookie_es_de_sesion(client, owner_engine) -> None:
    person = await create_user(owner_engine)
    response = await login(client, person.email, remember=False)
    cookie = response.headers["set-cookie"]
    assert "expires=" not in cookie.lower() and "max-age" not in cookie.lower()
    assert f"{REFRESH_COOKIE}=0." in cookie


# --- Renovar y salir ---


async def test_renovar_rota_el_pase(client, owner_engine, factory) -> None:
    tenant = await new_tenant(owner_engine, "Panaderia")
    person = await create_user(owner_engine, (tenant, MembershipRole.ADMIN))
    await login(client, person.email)
    first_cookie = client.cookies[REFRESH_COOKIE]

    renewed = await client.post("/auth/refresh")
    assert renewed.status_code == 200
    assert renewed.json()["active_tenant"]["id"] == str(tenant.id)
    assert client.cookies[REFRESH_COOKIE] != first_cookie

    async with factory() as s:
        sessions = list(
            await s.scalars(select(RefreshToken).where(RefreshToken.user_id == person.id))
        )
    assert len(sessions) == 2
    assert sum(t.revoked_at is None for t in sessions) == 1
    # Mismo vencimiento: la sesión no se alarga renovando.
    assert len({t.expires_at for t in sessions}) == 1
    # La base guarda la huella, nunca el pase.
    assert all(first_cookie.split(".", 1)[1] != t.token_hash for t in sessions)


async def test_pase_reutilizado_cierra_todas_las_sesiones(client, owner_engine) -> None:
    person = await create_user(owner_engine)
    await login(client, person.email)
    stolen = client.cookies[REFRESH_COOKIE]
    assert (await client.post("/auth/refresh")).status_code == 200  # el dueño renueva

    # Alguien usa la copia vieja: se rechaza y se cierran también las sesiones del dueño.
    async with httpx.AsyncClient(transport=client._transport, base_url=client.base_url) as thief:
        thief.cookies.set(REFRESH_COOKIE, stolen)
        assert (await thief.post("/auth/refresh")).status_code == 401
    assert (await client.post("/auth/refresh")).status_code == 401


async def test_sesion_vencida(client, owner_engine) -> None:
    person = await create_user(owner_engine)
    await login(client, person.email)
    async with AsyncSession(owner_engine) as s, s.begin():
        await s.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == person.id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    response = await client.post("/auth/refresh")
    assert response.status_code == 401
    assert "Max-Age=0" in response.headers["set-cookie"]  # se borra la cookie


async def test_salir(client, owner_engine) -> None:
    person = await create_user(owner_engine)
    await login(client, person.email)
    response = await client.post("/auth/logout")
    assert response.status_code == 204
    assert REFRESH_COOKIE not in client.cookies
    assert (await client.post("/auth/refresh")).status_code == 401


async def test_rutas_protegidas_sin_pase(client) -> None:
    assert (await client.get("/auth/me")).status_code == 401
    bad = await client.get("/auth/me", headers={"Authorization": "Bearer xyz"})
    assert bad.status_code == 401
    assert (await client.post("/auth/refresh")).status_code == 401


async def test_usuario_desactivado_pierde_acceso(client, owner_engine) -> None:
    person = await create_user(owner_engine)
    response = await login(client, person.email)
    async with AsyncSession(owner_engine) as s, s.begin():
        await s.execute(update(User).where(User.id == person.id).values(is_active=False))
    assert (await client.get("/auth/me", headers=bearer(response))).status_code == 401
    assert (await client.post("/auth/refresh")).status_code == 401


async def test_cors_para_el_panel(client) -> None:
    response = await client.options(
        "/auth/login",
        headers={"Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "POST"},
    )
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    assert response.headers["access-control-allow-credentials"] == "true"
    other = await client.options(
        "/auth/login",
        headers={"Origin": "https://malo.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in other.headers
