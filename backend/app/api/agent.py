"""Rutas del agente de IA y del horario del negocio activo.

- GET   /agent                           instrucciones actuales del bot
- PATCH /agent                           cambiar nombre o instrucciones (versión nueva)
- GET   /agent/default-instructions      las instrucciones de fábrica (para restablecer)
- GET   /agent/versions                  historial de instrucciones
- POST  /agent/versions/{n}/restore      volver a una versión anterior
- GET   /hours                           horario de atención y si está abierto ahora
- PUT   /hours                           guardar el horario

Qué puede hacer cada rol: ver services/agent_settings.py.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.auth import Factory, client_ip, current_member
from app.services import agent_settings, members
from app.services.agent_settings import MAX_INSTRUCTIONS
from app.services.members import Member

router = APIRouter(tags=["agente y horario"])

CurrentMember = Annotated[Member, Depends(current_member)]


class AgentOut(BaseModel):
    name: str
    instructions: str
    model: str
    version: int
    updated_at: datetime
    can_edit: bool


class AgentUpdateIn(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    instructions: str | None = Field(default=None, max_length=MAX_INSTRUCTIONS + 200)


class DefaultInstructionsOut(BaseModel):
    instructions: str


class VersionOut(BaseModel):
    version: int
    instructions: str
    created_at: datetime
    created_by: str | None  # None: la versión de fábrica
    is_current: bool


class HoursIn(BaseModel):
    # {"mon": [["08:00", "18:00"]], ...}; vacío = sin horario (abierto siempre)
    days: dict[str, list[list[str]]] = Field(default_factory=dict)
    after_hours_message: str | None = Field(default=None, max_length=1000)


class HoursOut(HoursIn):
    timezone: str
    open_now: bool
    can_edit: bool


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, members.PermissionDeniedError):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    if isinstance(exc, members.InvalidInputError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    return HTTPException(status.HTTP_404_NOT_FOUND, "Esa versión no existe")


ERRORS = (members.PermissionDeniedError, members.InvalidInputError, members.NotFoundError)


def _agent_out(member: Member, agent: agent_settings.AgentInfo) -> AgentOut:
    return AgentOut(
        name=agent.name,
        instructions=agent.instructions,
        model=agent.model,
        version=agent.version,
        updated_at=agent.updated_at,
        can_edit=members.can_edit_tenant(member.role),
    )


def _hours_out(member: Member, info: agent_settings.HoursInfo) -> HoursOut:
    return HoursOut(
        days=info.days,
        after_hours_message=info.after_hours_message,
        timezone=info.timezone,
        open_now=info.open_now,
        can_edit=members.can_edit_tenant(member.role),
    )


@router.get("/agent", response_model=AgentOut)
async def get_agent(member: CurrentMember, factory: Factory) -> AgentOut:
    return _agent_out(member, await agent_settings.get_agent(member, factory=factory))


@router.patch("/agent", response_model=AgentOut)
async def update_agent(
    body: AgentUpdateIn, member: CurrentMember, factory: Factory, request: Request
) -> AgentOut:
    try:
        agent = await agent_settings.update_agent(
            member,
            name=body.name,
            instructions=body.instructions,
            ip=client_ip(request),
            factory=factory,
        )
    except ERRORS as exc:
        raise _http_error(exc) from exc
    return _agent_out(member, agent)


@router.get("/agent/default-instructions", response_model=DefaultInstructionsOut)
async def default_instructions(member: CurrentMember, factory: Factory) -> DefaultInstructionsOut:
    tenant = await members.get_tenant(member, factory=factory)
    return DefaultInstructionsOut(instructions=agent_settings.default_instructions(tenant.name))


@router.get("/agent/versions", response_model=list[VersionOut])
async def list_versions(member: CurrentMember, factory: Factory) -> list[VersionOut]:
    return [
        VersionOut(
            version=v.version,
            instructions=v.instructions,
            created_at=v.created_at,
            created_by=v.created_by_name,
            is_current=v.is_current,
        )
        for v in await agent_settings.list_versions(member, factory=factory)
    ]


@router.post("/agent/versions/{version}/restore", response_model=AgentOut)
async def restore_version(
    version: int, member: CurrentMember, factory: Factory, request: Request
) -> AgentOut:
    try:
        agent = await agent_settings.restore_version(
            member, version, ip=client_ip(request), factory=factory
        )
    except ERRORS as exc:
        raise _http_error(exc) from exc
    return _agent_out(member, agent)


@router.get("/hours", response_model=HoursOut)
async def get_hours(member: CurrentMember, factory: Factory) -> HoursOut:
    return _hours_out(member, await agent_settings.get_hours(member, factory=factory))


@router.put("/hours", response_model=HoursOut)
async def set_hours(
    body: HoursIn, member: CurrentMember, factory: Factory, request: Request
) -> HoursOut:
    try:
        info = await agent_settings.set_hours(
            member, body.days, body.after_hours_message, ip=client_ip(request), factory=factory
        )
    except ERRORS as exc:
        raise _http_error(exc) from exc
    return _hours_out(member, info)
