"""Negocio, miembros, roles e invitaciones (bloque 5.2)."""

from collections.abc import AsyncIterator

import httpx
import pytest
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.auth import get_redis
from app.api.webhooks import get_session_factory
from app.core.config import get_settings
from app.main import create_app
from app.models import AuditLog, Tenant, TenantInvitation, User
from app.models.enums import MembershipRole
from tests.test_auth import PASSWORD, SECRET, Person, bearer, create_user, login, new_tenant

OWNER, ADMIN, AGENT = MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.AGENT
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
    # Solo los contadores de intentos (nunca toda la base: puede tener la cola).
    keys = [k async for k in redis.scan_iter("saint:login-fail:*")]
    if keys:
        await redis.delete(*keys)
    await redis.aclose()


async def headers_for(client: httpx.AsyncClient, person: Person) -> dict[str, str]:
    """Entra con una persona que tiene un solo negocio (queda elegido solo)."""
    response = await login(client, person.email)
    assert response.status_code == 200, response.text
    assert response.json()["active_tenant"] is not None
    return bearer(response)


async def invite(client, headers, email: str, role: str = "agent") -> httpx.Response:
    return await client.post(
        "/tenant/invitations", json={"email": email, "role": role}, headers=headers
    )


def token_of(response: httpx.Response) -> str:
    link = response.json()["link"]
    assert "/invitacion#" in link
    return link.split("#", 1)[1]


async def audit_actions(owner_engine: AsyncEngine, tenant_id) -> list[str]:
    async with AsyncSession(owner_engine) as s:
        rows = await s.scalars(
            select(AuditLog.action)
            .where(AuditLog.tenant_id == tenant_id)
            .order_by(AuditLog.created_at)
        )
        return list(rows)


# --- Datos del negocio ---


async def test_dueno_ve_y_edita_el_negocio(client, owner_engine) -> None:
    tenant = await new_tenant(owner_engine, "Panaderia")
    owner = await create_user(owner_engine, (tenant, OWNER))
    headers = await headers_for(client, owner)

    response = await client.get("/tenant", headers=headers)
    assert response.status_code == 200
    assert response.json()["my_role"] == "owner"
    assert response.json()["can_edit"] and response.json()["can_manage_members"]

    response = await client.patch(
        "/tenant",
        json={
            "name": "  Panadería   La Esquina ",
            "timezone": "America/Bogota",
            "country_code": "co",
        },
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["name"], body["timezone"], body["country_code"]) == (
        "Panadería La Esquina",
        "America/Bogota",
        "CO",
    )
    assert body["slug"] == tenant.slug  # el slug no cambia
    assert await audit_actions(owner_engine, tenant.id) == ["tenant.update"]


async def test_datos_del_negocio_invalidos(client, owner_engine) -> None:
    tenant = await new_tenant(owner_engine, "Panaderia")
    headers = await headers_for(client, await create_user(owner_engine, (tenant, OWNER)))
    for body in ({"timezone": "Marte/Olympus"}, {"country_code": "VEN"}, {"name": "   "}):
        response = await client.patch("/tenant", json=body, headers=headers)
        assert response.status_code == 422, body


async def test_agente_ve_pero_no_edita(client, owner_engine) -> None:
    tenant = await new_tenant(owner_engine, "Panaderia")
    await create_user(owner_engine, (tenant, OWNER))
    agent = await create_user(owner_engine, (tenant, AGENT))
    headers = await headers_for(client, agent)

    detail = (await client.get("/tenant", headers=headers)).json()
    assert not detail["can_edit"] and not detail["can_manage_members"]
    assert len((await client.get("/tenant/members", headers=headers)).json()) == 2
    assert (await client.patch("/tenant", json={"name": "X"}, headers=headers)).status_code == 403
    assert (await invite(client, headers, "nueva@saint.test")).status_code == 403
    assert (await client.get("/tenant/invitations", headers=headers)).status_code == 403


async def test_sin_negocio_elegido_no_entra(client, owner_engine) -> None:
    a, b = await new_tenant(owner_engine, "Uno"), await new_tenant(owner_engine, "Dos")
    person = await create_user(owner_engine, (a, OWNER), (b, OWNER))
    headers = bearer(await login(client, person.email))  # dos negocios: ninguno elegido
    assert (await client.get("/tenant", headers=headers)).status_code == 403
    assert (await client.get("/tenant")).status_code == 401


# --- Invitaciones ---


async def test_invitar_a_alguien_nuevo(client, owner_engine) -> None:
    tenant = await new_tenant(owner_engine, "Panaderia")
    owner = await create_user(owner_engine, (tenant, OWNER))
    headers = await headers_for(client, owner)

    created = await invite(client, headers, "Ana@Saint.test")
    assert created.status_code == 201
    token = token_of(created)
    # La base guarda la huella, nunca el enlace.
    async with AsyncSession(owner_engine) as s:
        stored = await s.scalar(select(TenantInvitation.token_hash))
    assert token not in str(stored)
    listed = (await client.get("/tenant/invitations", headers=headers)).json()
    assert [i["email"] for i in listed] == ["Ana@Saint.test"]

    preview = await client.post("/auth/invitations/preview", json={"token": token})
    assert preview.status_code == 200
    assert preview.json()["tenant_name"] == tenant.name
    assert preview.json()["role"] == "agent" and not preview.json()["has_account"]

    # En otro navegador (sin la cookie del dueño).
    transport = client._transport
    async with httpx.AsyncClient(transport=transport, base_url="http://panel.test") as other:
        missing_name = await other.post(
            "/auth/invitations/accept", json={"token": token, "password": NEW_PASSWORD}
        )
        assert missing_name.status_code == 422
        short = await other.post(
            "/auth/invitations/accept",
            json={"token": token, "password": "corta", "full_name": "Ana"},
        )
        assert short.status_code == 422
        accepted = await other.post(
            "/auth/invitations/accept",
            json={"token": token, "password": NEW_PASSWORD, "full_name": "Ana Pérez"},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["active_tenant"]["role"] == "agent"
        assert "saint_refresh" in accepted.headers["set-cookie"]
        me = await other.get("/tenant", headers=bearer(accepted))
        assert me.json()["my_role"] == "agent"

    # Ya entra con su correo y su contraseña, y el enlace no sirve dos veces.
    assert (await login(client, "ana@saint.test", NEW_PASSWORD)).status_code == 200
    again = await client.post(
        "/auth/invitations/accept",
        json={"token": token, "password": NEW_PASSWORD, "full_name": "Ana"},
    )
    assert again.status_code == 404
    roles = {
        m["email"]: m["role"] for m in (await client.get("/tenant/members", headers=headers)).json()
    }
    assert roles["Ana@Saint.test"] == "agent"
    assert (await client.get("/tenant/invitations", headers=headers)).json() == []
    assert await audit_actions(owner_engine, tenant.id) == [
        "invitation.create",
        "invitation.accept",
    ]


async def test_invitar_a_quien_ya_tiene_cuenta(client, owner_engine) -> None:
    tenant, other_tenant = (
        await new_tenant(owner_engine, "Uno"),
        await new_tenant(owner_engine, "Dos"),
    )
    headers = await headers_for(client, await create_user(owner_engine, (tenant, OWNER)))
    existing = await create_user(owner_engine, (other_tenant, AGENT))

    token = token_of(await invite(client, headers, existing.email, "admin"))
    preview = await client.post("/auth/invitations/preview", json={"token": token})
    assert preview.json()["has_account"]

    wrong = await client.post(
        "/auth/invitations/accept", json={"token": token, "password": "no-es-la-clave"}
    )
    assert wrong.status_code == 401
    ok = await client.post(
        "/auth/invitations/accept",
        json={"token": token, "password": PASSWORD, "full_name": "Otro nombre"},
    )
    assert ok.status_code == 200
    # Ahora tiene dos negocios, y el de la invitación queda elegido.
    assert len(ok.json()["tenants"]) == 2
    assert ok.json()["active_tenant"]["id"] == str(tenant.id)
    async with AsyncSession(owner_engine) as s:
        user = await s.get(User, existing.id)
    assert user.full_name == "Andre"  # su nombre y su contraseña no cambian


async def test_no_se_invita_a_un_miembro(client, owner_engine) -> None:
    tenant = await new_tenant(owner_engine, "Panaderia")
    headers = await headers_for(client, await create_user(owner_engine, (tenant, OWNER)))
    agent = await create_user(owner_engine, (tenant, AGENT))
    assert (await invite(client, headers, agent.email.upper())).status_code == 409
    assert (await invite(client, headers, "no-es-correo")).status_code == 422


async def test_invitar_otra_vez_anula_el_enlace_anterior(client, owner_engine) -> None:
    tenant = await new_tenant(owner_engine, "Panaderia")
    headers = await headers_for(client, await create_user(owner_engine, (tenant, OWNER)))
    old = token_of(await invite(client, headers, "luis@saint.test"))
    new = token_of(await invite(client, headers, "luis@saint.test", "admin"))
    assert (await client.post("/auth/invitations/preview", json={"token": old})).status_code == 404
    preview = await client.post("/auth/invitations/preview", json={"token": new})
    assert preview.json()["role"] == "admin"
    assert len((await client.get("/tenant/invitations", headers=headers)).json()) == 1


async def test_invitacion_anulada_o_vencida(client, owner_engine) -> None:
    tenant = await new_tenant(owner_engine, "Panaderia")
    headers = await headers_for(client, await create_user(owner_engine, (tenant, OWNER)))

    revoked = await invite(client, headers, "uno@saint.test")
    response = await client.delete(f"/tenant/invitations/{revoked.json()['id']}", headers=headers)
    assert response.status_code == 204
    preview = await client.post("/auth/invitations/preview", json={"token": token_of(revoked)})
    assert preview.status_code == 404

    expired = await invite(client, headers, "dos@saint.test")
    async with AsyncSession(owner_engine) as s, s.begin():
        await s.execute(
            update(TenantInvitation)
            .where(TenantInvitation.id == expired.json()["id"])
            .values(expires_at=TenantInvitation.created_at)
        )
    preview = await client.post("/auth/invitations/preview", json={"token": token_of(expired)})
    assert preview.status_code == 404
    listed = (await client.get("/tenant/invitations", headers=headers)).json()
    assert [(i["email"], i["expired"]) for i in listed] == [("dos@saint.test", True)]
    bogus = await client.post("/auth/invitations/preview", json={"token": "x" * 43})
    assert bogus.status_code == 404


async def test_negocio_desactivado_invalida_la_invitacion(client, owner_engine) -> None:
    tenant = await new_tenant(owner_engine, "Panaderia")
    headers = await headers_for(client, await create_user(owner_engine, (tenant, OWNER)))
    token = token_of(await invite(client, headers, "tres@saint.test"))
    async with AsyncSession(owner_engine) as s, s.begin():
        await s.execute(update(Tenant).where(Tenant.id == tenant.id).values(is_active=False))
    assert (
        await client.post("/auth/invitations/preview", json={"token": token})
    ).status_code == 404
    # Y los miembros dejan de entrar a ese negocio.
    assert (await client.get("/tenant", headers=headers)).status_code == 403


# --- Roles ---


async def test_administrador_solo_gestiona_agentes(client, owner_engine) -> None:
    tenant = await new_tenant(owner_engine, "Panaderia")
    owner = await create_user(owner_engine, (tenant, OWNER))
    admin = await create_user(owner_engine, (tenant, ADMIN))
    agent = await create_user(owner_engine, (tenant, AGENT))
    headers = await headers_for(client, admin)

    assert (await invite(client, headers, "agente@saint.test", "agent")).status_code == 201
    assert (await invite(client, headers, "admin@saint.test", "admin")).status_code == 403
    assert (
        await client.patch("/tenant", json={"name": "Nuevo"}, headers=headers)
    ).status_code == 200
    # Puede quitar a un agente, pero no subirlo a administrador ni tocar al dueño.
    promote = await client.patch(
        f"/tenant/members/{agent.id}", json={"role": "admin"}, headers=headers
    )
    assert promote.status_code == 403
    assert (await client.delete(f"/tenant/members/{owner.id}", headers=headers)).status_code == 403
    assert (await client.delete(f"/tenant/members/{agent.id}", headers=headers)).status_code == 204


async def test_dueno_cambia_roles_y_queda_auditado(client, owner_engine) -> None:
    tenant = await new_tenant(owner_engine, "Panaderia")
    owner = await create_user(owner_engine, (tenant, OWNER))
    agent = await create_user(owner_engine, (tenant, AGENT))
    headers = await headers_for(client, owner)

    response = await client.patch(
        f"/tenant/members/{agent.id}", json={"role": "admin"}, headers=headers
    )
    assert response.status_code == 204
    roles = {
        m["user_id"]: m["role"]
        for m in (await client.get("/tenant/members", headers=headers)).json()
    }
    assert roles[str(agent.id)] == "admin"
    assert await audit_actions(owner_engine, tenant.id) == ["member.role_change"]
    # Nadie cambia su propio rol.
    mine = await client.patch(
        f"/tenant/members/{owner.id}", json={"role": "agent"}, headers=headers
    )
    assert mine.status_code == 403
    missing = await client.patch(
        f"/tenant/members/{tenant.id}", json={"role": "agent"}, headers=headers
    )
    assert missing.status_code == 404


async def test_siempre_queda_un_dueno(client, owner_engine) -> None:
    tenant = await new_tenant(owner_engine, "Panaderia")
    first = await create_user(owner_engine, (tenant, OWNER))
    headers = await headers_for(client, first)
    # Un solo dueño: no puede salir.
    leave = await client.delete(f"/tenant/members/{first.id}", headers=headers)
    assert leave.status_code == 409

    second = await create_user(owner_engine, (tenant, OWNER))
    other_headers = await headers_for(client, second)
    # Con dos dueños, uno puede bajar al otro y luego ese ya no puede bajar al último.
    response = await client.patch(
        f"/tenant/members/{first.id}", json={"role": "admin"}, headers=other_headers
    )
    assert response.status_code == 204
    assert (
        await client.delete(f"/tenant/members/{second.id}", headers=other_headers)
    ).status_code == 409


async def test_quitar_el_acceso_vale_al_instante(client, owner_engine) -> None:
    tenant = await new_tenant(owner_engine, "Panaderia")
    owner = await create_user(owner_engine, (tenant, OWNER))
    agent = await create_user(owner_engine, (tenant, AGENT))
    owner_headers = await headers_for(client, owner)
    agent_headers = await headers_for(client, agent)
    assert (await client.get("/tenant", headers=agent_headers)).status_code == 200

    await client.delete(f"/tenant/members/{agent.id}", headers=owner_headers)
    # Su pase de 15 minutos todavía no vence, pero ya no entra a ese negocio.
    response = await client.get("/tenant", headers=agent_headers)
    assert response.status_code == 403
    assert response.json()["detail"] == "Ya no tienes acceso a este negocio"


async def test_un_agente_puede_salir(client, owner_engine) -> None:
    tenant = await new_tenant(owner_engine, "Panaderia")
    await create_user(owner_engine, (tenant, OWNER))
    agent = await create_user(owner_engine, (tenant, AGENT))
    headers = await headers_for(client, agent)
    assert (await client.delete(f"/tenant/members/{agent.id}", headers=headers)).status_code == 204
    assert await audit_actions(owner_engine, tenant.id) == ["member.leave"]


# --- Separación entre negocios ---


async def test_un_negocio_no_toca_a_otro(client, owner_engine) -> None:
    a, b = await new_tenant(owner_engine, "Uno"), await new_tenant(owner_engine, "Dos")
    owner_a = await create_user(owner_engine, (a, OWNER))
    owner_b = await create_user(owner_engine, (b, OWNER))
    agent_b = await create_user(owner_engine, (b, AGENT))
    headers_a = await headers_for(client, owner_a)
    headers_b = await headers_for(client, owner_b)
    invitation_b = (await invite(client, headers_b, "de-b@saint.test")).json()

    emails = {m["email"] for m in (await client.get("/tenant/members", headers=headers_a)).json()}
    assert emails == {owner_a.email}
    assert (await client.get("/tenant/invitations", headers=headers_a)).json() == []
    # Las filas de B no existen para A: 404, no 403.
    assert (
        await client.delete(f"/tenant/invitations/{invitation_b['id']}", headers=headers_a)
    ).status_code == 404
    assert (
        await client.delete(f"/tenant/members/{agent_b.id}", headers=headers_a)
    ).status_code == 404
    assert (
        await client.patch(
            f"/tenant/members/{agent_b.id}", json={"role": "admin"}, headers=headers_a
        )
    ).status_code == 404
