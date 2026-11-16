"""Aislamiento entre negocios con Row-Level Security.

Todas estas pruebas usan la conexión de la aplicación (rol saint_app), que es la que
usará la API en producción.
"""

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.session import tenant_session, user_session
from app.models import Agent, AgentVersion, AuditLog, Contact, Tenant, TenantMembership, User
from app.models.enums import AgentKind, Channel, MembershipRole
from tests.factories import create_contact, create_instance, create_tenant


@dataclass
class Scenario:
    tenant_a: uuid.UUID
    tenant_b: uuid.UUID
    public_id_a: str
    user_ab: uuid.UUID  # miembro de A y de B
    agent_b: uuid.UUID


@pytest.fixture(scope="module")
async def scenario(owner_engine: AsyncEngine) -> Scenario:
    """Dos negocios con datos, creados por el dueño (sin RLS) y guardados de verdad."""
    async with AsyncSession(owner_engine, expire_on_commit=False) as s, s.begin():
        a = await create_tenant(s, "Panaderia")
        b = await create_tenant(s, "Ferreteria")
        instance_a = await create_instance(s, a)
        await create_instance(s, b)
        for tenant in (a, b, a):
            await create_contact(s, tenant)
        user = User(email=f"{uuid.uuid4().hex[:6]}@saint.com", password_hash="x", full_name="U")
        s.add(user)
        await s.flush()
        s.add_all(
            [
                TenantMembership(tenant_id=a.id, user_id=user.id, role=MembershipRole.OWNER),
                TenantMembership(tenant_id=b.id, user_id=user.id, role=MembershipRole.AGENT),
            ]
        )
        agent_b = Agent(
            tenant_id=b.id, key="faq", name="FAQ", kind=AgentKind.SPECIALIST, system_prompt="..."
        )
        s.add(agent_b)
        await s.flush()
        s.add(
            AgentVersion(
                agent_id=agent_b.id, version=1, system_prompt="...", model="m", temperature=0.3
            )
        )
        s.add(AuditLog(tenant_id=a.id, action="tenant.create", entity_type="tenant"))
    return Scenario(a.id, b.id, instance_a.public_id, user.id, agent_b.id)


@pytest.fixture
def factory(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(app_engine, expire_on_commit=False)


async def test_cada_negocio_ve_solo_sus_contactos(scenario: Scenario, factory) -> None:
    async with tenant_session(scenario.tenant_a, factory=factory) as s:
        tenants = set(await s.scalars(select(Contact.tenant_id)))
        assert tenants == {scenario.tenant_a}
    async with tenant_session(scenario.tenant_b, factory=factory) as s:
        assert len((await s.scalars(select(Contact))).all()) == 1


async def test_no_se_puede_escribir_en_otro_negocio(scenario: Scenario, factory) -> None:
    with pytest.raises(ProgrammingError, match="row-level security"):
        async with tenant_session(scenario.tenant_a, factory=factory) as s:
            s.add(Contact(tenant_id=scenario.tenant_b, channel=Channel.WHATSAPP, external_id="x"))


async def test_no_se_puede_mover_una_fila_a_otro_negocio(scenario: Scenario, factory) -> None:
    async with tenant_session(scenario.tenant_a, factory=factory) as s:
        result = await s.execute(
            text("UPDATE contacts SET display_name = 'otro' WHERE tenant_id = :b"),
            {"b": scenario.tenant_b},
        )
        # Las filas de B ni siquiera se ven: 0 filas actualizadas.
        assert result.rowcount == 0
    with pytest.raises(ProgrammingError, match="row-level security"):
        async with tenant_session(scenario.tenant_a, factory=factory) as s:
            await s.execute(
                text("UPDATE contacts SET tenant_id = :b WHERE tenant_id = :a"),
                {"a": scenario.tenant_a, "b": scenario.tenant_b},
            )


async def test_sin_negocio_activo_la_consulta_falla(scenario: Scenario, factory) -> None:
    async with factory() as s:
        with pytest.raises(DBAPIError, match="app.tenant_id no está definido"):
            await s.execute(select(Contact))


async def test_el_negocio_activo_no_pasa_a_la_siguiente_transaccion(
    scenario: Scenario, factory
) -> None:
    # pool_size=1: la siguiente sesión reutiliza exactamente la misma conexión.
    async with tenant_session(scenario.tenant_a, factory=factory) as s:
        await s.execute(select(Contact))
    async with factory() as s:
        with pytest.raises(DBAPIError, match="app.tenant_id no está definido"):
            await s.execute(select(Contact))


async def test_webhook_encuentra_su_instancia_sin_negocio_activo(
    scenario: Scenario, factory
) -> None:
    async with factory() as s:
        row = (
            await s.execute(
                text("SELECT id, tenant_id FROM resolve_channel_instance(:p)"),
                {"p": scenario.public_id_a},
            )
        ).one()
        assert row.tenant_id == scenario.tenant_a
        missing = await s.execute(text("SELECT * FROM resolve_channel_instance('no-existe')"))
        assert missing.first() is None


async def test_versiones_de_agente_siguen_al_negocio(scenario: Scenario, factory) -> None:
    async with tenant_session(scenario.tenant_a, factory=factory) as s:
        assert (await s.scalars(select(AgentVersion))).all() == []
    async with tenant_session(scenario.tenant_b, factory=factory) as s:
        versions = (await s.scalars(select(AgentVersion))).all()
        assert [v.agent_id for v in versions] == [scenario.agent_b]


async def test_negocios_visibles(scenario: Scenario, factory) -> None:
    # Con negocio activo: solo ese negocio.
    async with tenant_session(scenario.tenant_a, factory=factory) as s:
        assert set(await s.scalars(select(Tenant.id))) == {scenario.tenant_a}
    # Pantalla de elegir negocio: todos aquellos de los que el usuario es miembro.
    async with user_session(scenario.user_ab, factory=factory) as s:
        assert set(await s.scalars(select(Tenant.id))) == {scenario.tenant_a, scenario.tenant_b}
    # Un usuario sin membresías no ve ninguno.
    async with user_session(uuid.uuid4(), factory=factory) as s:
        assert (await s.scalars(select(Tenant.id))).all() == []


async def test_auditoria_no_se_edita_ni_borra(scenario: Scenario, factory) -> None:
    for sql in ("UPDATE audit_logs SET action = 'x'", "DELETE FROM audit_logs"):
        with pytest.raises(ProgrammingError, match="permission denied"):
            async with tenant_session(scenario.tenant_a, factory=factory) as s:
                await s.execute(text(sql))


async def test_la_app_no_puede_cambiar_la_estructura(scenario: Scenario, factory) -> None:
    for sql in ("DROP TABLE contacts", "CREATE TABLE intrusa (id int)"):
        with pytest.raises(ProgrammingError):
            async with factory() as s, s.begin():
                await s.execute(text(sql))
