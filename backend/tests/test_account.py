"""Mi cuenta: nombre, contraseña y cerrar todas las sesiones (bloque 6.9)."""

from collections.abc import AsyncIterator

import httpx
import pytest
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.auth import REFRESH_COOKIE, get_redis
from app.api.webhooks import get_session_factory
from app.core.auth_tokens import verify_password
from app.core.config import get_settings
from app.main import create_app
from app.models import RefreshToken, User
from app.models.enums import MembershipRole
from tests.test_auth import PASSWORD, SECRET, bearer, create_user, login, new_tenant

NEW_PASSWORD = "croissant-2026"


@pytest.fixture(autouse=True)
def jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "jwt_secret", SecretStr(SECRET))


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


def other_browser(client: httpx.AsyncClient) -> httpx.AsyncClient:
    """Otro navegador (otro frasco de cookies) hablando con la misma API."""
    return httpx.AsyncClient(transport=client._transport, base_url=client.base_url)


async def change(client, headers, current=PASSWORD, new=NEW_PASSWORD) -> httpx.Response:
    return await client.post(
        "/auth/password", json={"current_password": current, "new_password": new}, headers=headers
    )


# --- Contraseña ---


async def test_cambiar_la_contrasena_cierra_las_otras_sesiones(client, owner_engine) -> None:
    tenant = await new_tenant(owner_engine, "Panaderia")
    person = await create_user(owner_engine, (tenant, MembershipRole.OWNER))
    mine = await login(client, person.email)
    async with other_browser(client) as phone:
        assert (await login(phone, person.email)).status_code == 200

        response = await change(client, bearer(mine))
        assert response.status_code == 200, response.text
        assert response.json() == {"closed_sessions": 1}

        # El otro dispositivo ya no puede renovar; este navegador sí.
        assert (await phone.post("/auth/refresh")).status_code == 401
    # Que el otro dispositivo intente renovar no debe arrastrar a esta sesión.
    assert (await client.post("/auth/refresh")).status_code == 200

    # La contraseña vieja ya no sirve y la nueva sí.
    assert (await login(client, person.email, PASSWORD)).status_code == 401
    assert (await login(client, person.email, NEW_PASSWORD)).status_code == 200


async def test_la_contrasena_nueva_se_guarda_con_argon2(client, owner_engine) -> None:
    person = await create_user(owner_engine)
    response = await login(client, person.email)
    assert (await change(client, bearer(response))).status_code == 200
    async with AsyncSession(owner_engine) as s:
        stored = await s.scalar(select(User.password_hash).where(User.id == person.id))
    assert stored.startswith("$argon2") and NEW_PASSWORD not in stored
    assert verify_password(NEW_PASSWORD, stored)


async def test_contrasena_actual_incorrecta(client, owner_engine) -> None:
    person = await create_user(owner_engine)
    response = await login(client, person.email)
    wrong = await change(client, bearer(response), current="no-es-esta-1234")
    # 422 y no 401: el panel tomaría un 401 por un pase vencido y cerraría la sesión.
    assert wrong.status_code == 422
    assert wrong.json()["detail"] == "La contraseña actual no es correcta"
    assert (await login(client, person.email, PASSWORD)).status_code == 200  # no cambió
    assert (await client.post("/auth/refresh")).status_code == 200  # y la sesión sigue


async def test_contrasena_nueva_corta_o_igual(client, owner_engine) -> None:
    person = await create_user(owner_engine)
    headers = bearer(await login(client, person.email))
    short = await change(client, headers, new="corta")
    assert short.status_code == 422 and "al menos 10 caracteres" in short.json()["detail"]
    same = await change(client, headers, new=PASSWORD)
    assert same.status_code == 422 and "distinta" in same.json()["detail"]


async def test_equivocarse_mucho_bloquea_el_cambio(client, owner_engine) -> None:
    person = await create_user(owner_engine)
    headers = bearer(await login(client, person.email))
    for _ in range(5):
        assert (await change(client, headers, current="mala-contrasena")).status_code == 422
    blocked = await change(client, headers)  # aunque ahora la actual sea la buena
    assert blocked.status_code == 429
    assert 0 < int(blocked.headers["retry-after"]) <= 15 * 60


async def test_cambiar_la_contrasena_exige_sesion(client) -> None:
    response = await change(client, {})
    assert response.status_code == 401


async def test_usuario_desactivado_no_cambia_nada(client, owner_engine) -> None:
    person = await create_user(owner_engine)
    headers = bearer(await login(client, person.email))
    async with AsyncSession(owner_engine) as s, s.begin():
        await s.execute(update(User).where(User.id == person.id).values(is_active=False))
    assert (await change(client, headers)).status_code == 401


# --- Nombre ---


async def test_cambiar_el_nombre(client, owner_engine) -> None:
    tenant = await new_tenant(owner_engine, "Panaderia")
    person = await create_user(owner_engine, (tenant, MembershipRole.OWNER))
    headers = bearer(await login(client, person.email))

    response = await client.patch(
        "/auth/me", json={"full_name": "  Andre   Pérez  "}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["full_name"] == "Andre Pérez"  # espacios repetidos juntados
    me = await client.get("/auth/me", headers=headers)
    assert me.json()["user"]["full_name"] == "Andre Pérez"


async def test_nombre_vacio_o_demasiado_largo(client, owner_engine) -> None:
    person = await create_user(owner_engine)
    headers = bearer(await login(client, person.email))
    for name in ("   ", "x" * 121):
        response = await client.patch("/auth/me", json={"full_name": name}, headers=headers)
        assert response.status_code == 422, name
    assert (await client.patch("/auth/me", json={"full_name": "Ana"})).status_code == 401


# --- Cerrar todas las sesiones ---


async def test_cerrar_sesion_en_todos_los_dispositivos(client, owner_engine, app_engine) -> None:
    person = await create_user(owner_engine)
    mine = await login(client, person.email)
    async with other_browser(client) as phone:
        await login(phone, person.email)
        response = await client.post("/auth/logout-all", headers=bearer(mine))
        assert response.status_code == 204
        assert REFRESH_COOKIE not in client.cookies
        assert (await phone.post("/auth/refresh")).status_code == 401
    assert (await client.post("/auth/refresh")).status_code == 401

    async with AsyncSession(owner_engine) as s:
        open_sessions = list(
            await s.scalars(
                select(RefreshToken).where(
                    RefreshToken.user_id == person.id,
                    RefreshToken.revoked_at.is_(None),
                    RefreshToken.expires_at > func.now(),
                )
            )
        )
    assert open_sessions == []
    assert (await client.post("/auth/logout-all")).status_code == 401  # exige sesión
