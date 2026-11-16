"""El agente del negocio y su horario, desde el panel (bloque 5.5).

- Todos ven las instrucciones del agente, su historial y el horario.
- Dueño y administrador cambian las instrucciones, vuelven a una versión anterior y
  cambian el horario.

Cada cambio de instrucciones crea una versión nueva en agent_versions (con quién la hizo).
Así siempre se puede ver qué decía el bot antes y volver atrás. El bot usa la versión
nueva desde su siguiente respuesta.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import SessionFactory, tenant_session
from app.models import Agent, AgentVersion, Tenant, User
from app.services import audit, hours
from app.services.agents import DEFAULT_PROMPT, ensure_default_agent
from app.services.members import (
    InvalidInputError,
    Member,
    NotFoundError,
    PermissionDeniedError,
    can_edit_tenant,
)

Factory = async_sessionmaker[AsyncSession]

MIN_INSTRUCTIONS = 20
MAX_INSTRUCTIONS = 8000
NAME_MAX = 80


@dataclass(frozen=True)
class AgentInfo:
    id: uuid.UUID
    name: str
    instructions: str
    model: str
    version: int
    is_active: bool
    updated_at: datetime


@dataclass(frozen=True)
class VersionInfo:
    version: int
    instructions: str
    created_at: datetime
    created_by_name: str | None
    is_current: bool


@dataclass(frozen=True)
class HoursInfo:
    timezone: str
    days: dict[str, list[list[str]]]  # vacío: no configuró horario (siempre abierto)
    after_hours_message: str | None
    open_now: bool


def _info(agent: Agent) -> AgentInfo:
    return AgentInfo(
        agent.id,
        agent.name,
        agent.system_prompt,
        agent.model,
        agent.current_version,
        agent.is_active,
        agent.updated_at,
    )


def _require_edit(member: Member) -> None:
    if not can_edit_tenant(member.role):
        raise PermissionDeniedError("Tu rol no te permite cambiar el agente ni el horario")


async def _snapshot(session: AsyncSession, agent: Agent, created_by: uuid.UUID | None) -> None:
    session.add(
        AgentVersion(
            agent_id=agent.id,
            version=agent.current_version,
            system_prompt=agent.system_prompt,
            model=agent.model,
            temperature=agent.temperature,
            tools=agent.tools,
            created_by=created_by,
        )
    )


async def _ensure_first_version(session: AsyncSession, agent: Agent) -> None:
    """La versión con la que se creó el agente también queda en el historial."""
    exists = await session.scalar(
        select(AgentVersion.id).where(
            AgentVersion.agent_id == agent.id, AgentVersion.version == agent.current_version
        )
    )
    if exists is None:
        await _snapshot(session, agent, None)
        await session.flush()


async def get_agent(member: Member, *, factory: Factory = SessionFactory) -> AgentInfo:
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        agent = await ensure_default_agent(session, member.tenant_id)
        await session.refresh(agent)
        return _info(agent)


async def update_agent(
    member: Member,
    *,
    name: str | None = None,
    instructions: str | None = None,
    ip: str | None = None,
    factory: Factory = SessionFactory,
) -> AgentInfo:
    _require_edit(member)
    if name is not None:
        name = " ".join(name.split())
        if not name or len(name) > NAME_MAX:
            raise InvalidInputError(f"El nombre debe tener entre 1 y {NAME_MAX} caracteres")
    if instructions is not None:
        instructions = instructions.strip()
        if not MIN_INSTRUCTIONS <= len(instructions) <= MAX_INSTRUCTIONS:
            raise InvalidInputError(
                f"Las instrucciones deben tener entre {MIN_INSTRUCTIONS} y "
                f"{MAX_INSTRUCTIONS} caracteres"
            )
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        agent = await ensure_default_agent(session, member.tenant_id)
        agent = await session.get_one(Agent, agent.id, with_for_update=True)
        if name is not None and name != agent.name:
            audit.record(
                session,
                tenant_id=member.tenant_id,
                actor_user_id=member.user_id,
                action="agent.rename",
                entity_type="agent",
                entity_id=agent.id,
                diff={"name": [agent.name, name]},
                ip=ip,
            )
            agent.name = name
        if instructions is not None and instructions != agent.system_prompt:
            await _set_instructions(session, member, agent, instructions, ip)
        await session.flush()
        await session.refresh(agent)
        return _info(agent)


async def _set_instructions(
    session: AsyncSession, member: Member, agent: Agent, instructions: str, ip: str | None
) -> None:
    await _ensure_first_version(session, agent)
    previous = agent.current_version
    agent.system_prompt = instructions
    agent.current_version = previous + 1
    await _snapshot(session, agent, member.user_id)
    audit.record(
        session,
        tenant_id=member.tenant_id,
        actor_user_id=member.user_id,
        action="agent.update",
        entity_type="agent",
        entity_id=agent.id,
        diff={"version": [previous, agent.current_version]},
        ip=ip,
    )


async def list_versions(member: Member, *, factory: Factory = SessionFactory) -> list[VersionInfo]:
    """El historial de instrucciones, la más nueva primero."""
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        agent = await ensure_default_agent(session, member.tenant_id)
        await _ensure_first_version(session, agent)
        rows = await session.execute(
            select(
                AgentVersion.version,
                AgentVersion.system_prompt,
                AgentVersion.created_at,
                User.full_name,
            )
            .outerjoin(User, User.id == AgentVersion.created_by)
            .where(AgentVersion.agent_id == agent.id)
            .order_by(AgentVersion.version.desc())
        )
        return [
            VersionInfo(v, text, at, who, v == agent.current_version) for v, text, at, who in rows
        ]


async def restore_version(
    member: Member, version: int, *, ip: str | None = None, factory: Factory = SessionFactory
) -> AgentInfo:
    """Vuelve a las instrucciones de una versión anterior (como una versión nueva)."""
    _require_edit(member)
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        agent = await ensure_default_agent(session, member.tenant_id)
        agent = await session.get_one(Agent, agent.id, with_for_update=True)
        await _ensure_first_version(session, agent)
        old = await session.scalar(
            select(AgentVersion.system_prompt).where(
                AgentVersion.agent_id == agent.id, AgentVersion.version == version
            )
        )
        if old is None:
            raise NotFoundError
        if old != agent.system_prompt:
            await _set_instructions(session, member, agent, old, ip)
        await session.flush()
        await session.refresh(agent)
        return _info(agent)


def default_instructions(business: str) -> str:
    """Las instrucciones con las que nace el agente, para el botón "restablecer"."""
    return DEFAULT_PROMPT.format(business=business)


# --- Horario ---


def _hours_info(tenant: Tenant, now: datetime) -> HoursInfo:
    data = tenant.business_hours or {}
    return HoursInfo(
        tenant.timezone,
        data.get("days") or {},
        data.get("after_hours_message"),
        hours.is_open(data, tenant.timezone, now),
    )


async def get_hours(
    member: Member, *, now: datetime | None = None, factory: Factory = SessionFactory
) -> HoursInfo:
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        tenant = await session.get_one(Tenant, member.tenant_id)
        return _hours_info(tenant, now or datetime.now().astimezone())


async def set_hours(
    member: Member,
    days: dict[str, list[list[str]]],
    after_hours_message: str | None,
    *,
    ip: str | None = None,
    factory: Factory = SessionFactory,
) -> HoursInfo:
    """Guarda el horario. Con days vacío se borra (el negocio queda abierto siempre)."""
    _require_edit(member)
    clean = hours.validate(days, after_hours_message)
    if not days:
        clean["days"] = {}  # sin horario: abierto siempre (no "cerrado todos los días")
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        tenant = await session.get_one(Tenant, member.tenant_id, with_for_update=True)
        if tenant.business_hours != clean:
            audit.record(
                session,
                tenant_id=member.tenant_id,
                actor_user_id=member.user_id,
                action="tenant.hours",
                entity_type="tenant",
                entity_id=member.tenant_id,
                diff={"business_hours": [tenant.business_hours, clean]},
                ip=ip,
            )
            tenant.business_hours = clean
        await session.flush()
        return _hours_info(tenant, datetime.now().astimezone())
