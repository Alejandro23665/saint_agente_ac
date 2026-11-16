"""Rutas del negocio activo, sus miembros y sus invitaciones: /tenant/...

El negocio activo es el que la persona eligió al entrar (va en su pase de acceso).

- GET    /tenant                          datos del negocio y mi rol
- PATCH  /tenant                          cambiar nombre, país o zona horaria
- GET    /tenant/members                  quiénes tienen acceso y con qué rol
- PATCH  /tenant/members/{user_id}        cambiar el rol de alguien
- DELETE /tenant/members/{user_id}        quitarle el acceso (con mi propio id: salir)
- GET    /tenant/invitations              invitaciones sin usar
- POST   /tenant/invitations              invitar un correo con un rol -> enlace
- DELETE /tenant/invitations/{id}         anular una invitación

Qué puede hacer cada rol: ver services/members.py.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.auth import Factory, client_ip, current_member
from app.models.enums import MembershipRole
from app.services import members
from app.services.members import Member

router = APIRouter(prefix="/tenant", tags=["negocio y miembros"])

CurrentMember = Annotated[Member, Depends(current_member)]


# --- Lo que recibe y devuelve la API ---


class TenantDetailOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    country_code: str
    timezone: str
    my_role: str
    # Lo que el panel puede mostrar u ocultar según el rol (la API lo vuelve a comprobar).
    can_edit: bool
    can_manage_members: bool


class TenantUpdateIn(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    country_code: str | None = Field(default=None, max_length=5)
    timezone: str | None = Field(default=None, max_length=64)


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: str
    joined_at: datetime
    last_login_at: datetime | None
    is_me: bool


class RoleIn(BaseModel):
    role: MembershipRole


class InvitationIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    role: MembershipRole = MembershipRole.AGENT


class InvitationOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    expires_at: datetime
    created_at: datetime
    expired: bool


class NewInvitationOut(InvitationOut):
    # El enlace se muestra solo esta vez: Saint guarda su huella, no el enlace.
    link: str


# --- Ayudantes ---


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, members.PermissionDeniedError):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    if isinstance(exc, members.ConflictError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, members.InvalidInputError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    return HTTPException(status.HTTP_404_NOT_FOUND, "No existe en este negocio")


SERVICE_ERRORS = (
    members.PermissionDeniedError,
    members.ConflictError,
    members.InvalidInputError,
    members.NotFoundError,
)


def _detail(member: Member, tenant: members.TenantInfo) -> TenantDetailOut:
    return TenantDetailOut(
        id=tenant.id,
        name=tenant.name,
        slug=tenant.slug,
        country_code=tenant.country_code,
        timezone=tenant.timezone,
        my_role=member.role.value,
        can_edit=members.can_edit_tenant(member.role),
        can_manage_members=members.can_manage(member.role, MembershipRole.AGENT),
    )


def _invitation_out(inv: members.InvitationInfo) -> dict:
    return {
        "id": inv.id,
        "email": inv.email,
        "role": inv.role.value,
        "expires_at": inv.expires_at,
        "created_at": inv.created_at,
        "expired": inv.expired,
    }


# --- Rutas ---


@router.get("", response_model=TenantDetailOut)
async def get_tenant(member: CurrentMember, factory: Factory) -> TenantDetailOut:
    return _detail(member, await members.get_tenant(member, factory=factory))


@router.patch("", response_model=TenantDetailOut)
async def update_tenant(
    body: TenantUpdateIn, member: CurrentMember, factory: Factory, request: Request
) -> TenantDetailOut:
    try:
        tenant = await members.update_tenant(
            member,
            name=body.name,
            country_code=body.country_code,
            timezone=body.timezone,
            ip=client_ip(request),
            factory=factory,
        )
    except SERVICE_ERRORS as exc:
        raise _http_error(exc) from exc
    return _detail(member, tenant)


@router.get("/members", response_model=list[MemberOut])
async def list_members(member: CurrentMember, factory: Factory) -> list[MemberOut]:
    return [
        MemberOut(
            user_id=m.user_id,
            email=m.email,
            full_name=m.full_name,
            role=m.role.value,
            joined_at=m.joined_at,
            last_login_at=m.last_login_at,
            is_me=m.user_id == member.user_id,
        )
        for m in await members.list_members(member, factory=factory)
    ]


@router.patch("/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def change_role(
    user_id: uuid.UUID, body: RoleIn, member: CurrentMember, factory: Factory, request: Request
) -> Response:
    try:
        await members.change_role(
            member, user_id, body.role, ip=client_ip(request), factory=factory
        )
    except SERVICE_ERRORS as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    user_id: uuid.UUID, member: CurrentMember, factory: Factory, request: Request
) -> Response:
    try:
        await members.remove_member(member, user_id, ip=client_ip(request), factory=factory)
    except SERVICE_ERRORS as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/invitations", response_model=list[InvitationOut])
async def list_invitations(member: CurrentMember, factory: Factory) -> list[InvitationOut]:
    try:
        rows = await members.list_invitations(member, factory=factory)
    except SERVICE_ERRORS as exc:
        raise _http_error(exc) from exc
    return [InvitationOut(**_invitation_out(i)) for i in rows]


@router.post("/invitations", response_model=NewInvitationOut, status_code=status.HTTP_201_CREATED)
async def create_invitation(
    body: InvitationIn, member: CurrentMember, factory: Factory, request: Request
) -> NewInvitationOut:
    try:
        invitation, token = await members.create_invitation(
            member, body.email, body.role, ip=client_ip(request), factory=factory
        )
    except SERVICE_ERRORS as exc:
        raise _http_error(exc) from exc
    return NewInvitationOut(**_invitation_out(invitation), link=members.invitation_link(token))


@router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(
    invitation_id: uuid.UUID, member: CurrentMember, factory: Factory, request: Request
) -> Response:
    try:
        await members.revoke_invitation(
            member, invitation_id, ip=client_ip(request), factory=factory
        )
    except SERVICE_ERRORS as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
