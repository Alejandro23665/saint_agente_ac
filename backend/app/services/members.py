"""El negocio activo, sus miembros y sus invitaciones (bloque 5.2).

Qué puede hacer cada rol:

| Acción                                   | owner | admin          | agent |
|------------------------------------------|-------|----------------|-------|
| Ver los datos del negocio y sus miembros | sí    | sí             | sí    |
| Editar nombre, país y zona horaria       | sí    | sí             | no    |
| Invitar, cambiar de rol o quitar         | a todos | solo agentes | no    |
| Ver y anular invitaciones                | todas | las de agentes | no    |
| Salir del negocio                        | sí    | sí             | sí    |

Reglas que no dependen del rol:
- Nadie cambia su propio rol (para no quedarse sin dueño por error).
- El negocio siempre conserva al menos un dueño.

El rol se lee de la base en cada petición, no del pase: si a alguien le quitan el acceso,
deja de entrar en ese mismo momento, aunque su pase de 15 minutos no haya vencido.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.auth_tokens import (
    MIN_PASSWORD_LENGTH,
    REFRESH_TOKEN_DAYS,
    hash_password,
    verify_password,
)
from app.core.config import get_settings
from app.core.security import hash_secret, new_secret
from app.db.session import SessionFactory, tenant_session
from app.models import Tenant, TenantInvitation, TenantMembership, User
from app.models.enums import MembershipRole
from app.services import audit
from app.services.auth import InvalidCredentialsError, NewSession, open_session

INVITATION_HOURS = 72
NAME_MAX = 120

Factory = async_sessionmaker[AsyncSession]


class PermissionDeniedError(Exception):
    """El rol de la persona no le permite hacer eso. El mensaje se muestra tal cual."""


class ConflictError(Exception):
    """La acción rompería una regla del negocio. El mensaje se muestra tal cual."""


class NotFoundError(Exception):
    """Ese miembro o esa invitación no existe en el negocio activo."""


class InvalidInvitationError(Exception):
    """El enlace no sirve: no existe, venció, se anuló o ya se usó."""


class InvalidInputError(Exception):
    """Un dato no es válido. El mensaje se muestra tal cual."""


@dataclass(frozen=True)
class Member:
    """Quién hace la petición, en qué negocio y con qué rol (leído de la base)."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: MembershipRole


@dataclass(frozen=True)
class TenantInfo:
    id: uuid.UUID
    name: str
    slug: str
    country_code: str
    timezone: str


@dataclass(frozen=True)
class MemberInfo:
    user_id: uuid.UUID
    email: str
    full_name: str
    role: MembershipRole
    joined_at: datetime
    last_login_at: datetime | None


@dataclass(frozen=True)
class InvitationInfo:
    id: uuid.UUID
    email: str
    role: MembershipRole
    expires_at: datetime
    created_at: datetime
    expired: bool


@dataclass(frozen=True)
class InvitationPreview:
    tenant_name: str
    email: str
    role: MembershipRole
    expires_at: datetime
    has_account: bool  # True: ya tiene usuario en Saint y entra con su contraseña de siempre


# --- Permisos ---


def can_edit_tenant(role: MembershipRole) -> bool:
    return role in (MembershipRole.OWNER, MembershipRole.ADMIN)


def can_manage(actor: MembershipRole, target: MembershipRole) -> bool:
    """¿Puede quien tiene el rol `actor` invitar, cambiar o quitar a alguien con `target`?"""
    if actor is MembershipRole.OWNER:
        return True
    if actor is MembershipRole.ADMIN:
        return target is MembershipRole.AGENT
    return False


def _require_manage(actor: Member, target: MembershipRole) -> None:
    if not can_manage(actor.role, target):
        if actor.role is MembershipRole.ADMIN:
            raise PermissionDeniedError("Un administrador solo puede gestionar agentes")
        raise PermissionDeniedError("Tu rol no te permite gestionar miembros")


def _now() -> datetime:
    return datetime.now(UTC)


def invitation_link(token: str) -> str:
    # El pase va después de '#': el navegador no lo manda a ningún servidor ni queda en
    # los registros; el panel lo lee y lo envía en el cuerpo de la petición.
    return f"{get_settings().panel_url.rstrip('/')}/invitacion#{token}"


def _clean_email(email: str) -> str:
    email = email.strip()
    local, at, domain = email.partition("@")
    if not (local and at and "." in domain) or " " in email or len(email) > 254:
        raise InvalidInputError("Ese correo no es válido")
    return email


# --- Quién hace la petición ---


async def get_member(
    user_id: uuid.UUID, tenant_id: uuid.UUID, *, factory: Factory = SessionFactory
) -> Member | None:
    """El rol actual de la persona en el negocio, o None si ya no tiene acceso."""
    async with tenant_session(tenant_id, user_id, factory=factory) as session:
        role = await session.scalar(
            select(TenantMembership.role)
            .join(Tenant, Tenant.id == TenantMembership.tenant_id)
            .join(User, User.id == TenantMembership.user_id)
            .where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.user_id == user_id,
                Tenant.is_active.is_(True),
                Tenant.deleted_at.is_(None),
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
    return Member(user_id, tenant_id, role) if role is not None else None


# --- Datos del negocio ---


def _tenant_info(tenant: Tenant) -> TenantInfo:
    return TenantInfo(tenant.id, tenant.name, tenant.slug, tenant.country_code, tenant.timezone)


async def get_tenant(member: Member, *, factory: Factory = SessionFactory) -> TenantInfo:
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        return _tenant_info(await session.get_one(Tenant, member.tenant_id))


async def update_tenant(
    member: Member,
    *,
    name: str | None = None,
    country_code: str | None = None,
    timezone: str | None = None,
    ip: str | None = None,
    factory: Factory = SessionFactory,
) -> TenantInfo:
    """Cambia nombre, país o zona horaria. El slug no cambia: lo usan los scripts."""
    if not can_edit_tenant(member.role):
        raise PermissionDeniedError("Tu rol no te permite editar el negocio")
    changes: dict[str, str] = {}
    if name is not None:
        name = " ".join(name.split())
        if not name or len(name) > NAME_MAX:
            raise InvalidInputError(f"El nombre debe tener entre 1 y {NAME_MAX} caracteres")
        changes["name"] = name
    if country_code is not None:
        country_code = country_code.strip().upper()
        if len(country_code) != 2 or not country_code.isascii() or not country_code.isalpha():
            raise InvalidInputError("El país va con su código de 2 letras, ej. VE")
        changes["country_code"] = country_code
    if timezone is not None:
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise InvalidInputError("Esa zona horaria no existe, ej. America/Caracas") from exc
        changes["timezone"] = timezone

    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        tenant = await session.get_one(Tenant, member.tenant_id, with_for_update=True)
        diff = {
            field: [getattr(tenant, field), value]
            for field, value in changes.items()
            if getattr(tenant, field) != value
        }
        for field, (_, value) in diff.items():
            setattr(tenant, field, value)
        if diff:
            audit.record(
                session,
                tenant_id=member.tenant_id,
                actor_user_id=member.user_id,
                action="tenant.update",
                entity_type="tenant",
                entity_id=member.tenant_id,
                diff=diff,
                ip=ip,
            )
        await session.flush()
        return _tenant_info(tenant)


# --- Miembros ---


async def list_members(member: Member, *, factory: Factory = SessionFactory) -> list[MemberInfo]:
    """Todos los miembros activos: dueños primero, luego administradores y agentes."""
    order = {MembershipRole.OWNER: 0, MembershipRole.ADMIN: 1, MembershipRole.AGENT: 2}
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        rows = await session.execute(
            select(
                User.id,
                User.email,
                User.full_name,
                TenantMembership.role,
                TenantMembership.created_at,
                User.last_login_at,
            )
            .join(TenantMembership, TenantMembership.user_id == User.id)
            .where(
                TenantMembership.tenant_id == member.tenant_id,
                User.deleted_at.is_(None),
            )
        )
        members = [MemberInfo(*row) for row in rows]
    return sorted(members, key=lambda m: (order[m.role], m.full_name.lower()))


async def _lock_membership(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> TenantMembership:
    row = await session.scalar(
        select(TenantMembership)
        .where(TenantMembership.tenant_id == tenant_id, TenantMembership.user_id == user_id)
        .with_for_update()
    )
    if row is None:
        raise NotFoundError
    return row


async def _owner_count(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    # FOR UPDATE bloquea las filas de los dueños: si dos dueños se quitan a la vez, el
    # segundo espera al primero y ve que ya queda uno solo.
    owners = await session.scalars(
        select(TenantMembership.id)
        .where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.role == MembershipRole.OWNER,
        )
        .with_for_update()
    )
    return len(owners.all())


LAST_OWNER = "El negocio necesita al menos un dueño. Nombra a otro dueño primero."


async def change_role(
    member: Member,
    user_id: uuid.UUID,
    new_role: MembershipRole,
    *,
    ip: str | None = None,
    factory: Factory = SessionFactory,
) -> None:
    if user_id == member.user_id:
        raise PermissionDeniedError("No puedes cambiar tu propio rol")
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        target = await _lock_membership(session, member.tenant_id, user_id)
        _require_manage(member, target.role)
        _require_manage(member, new_role)
        if target.role == new_role:
            return
        if (
            target.role is MembershipRole.OWNER
            and await _owner_count(session, member.tenant_id) <= 1
        ):
            raise ConflictError(LAST_OWNER)
        audit.record(
            session,
            tenant_id=member.tenant_id,
            actor_user_id=member.user_id,
            action="member.role_change",
            entity_type="user",
            entity_id=user_id,
            diff={"role": [target.role.value, new_role.value]},
            ip=ip,
        )
        target.role = new_role


async def remove_member(
    member: Member,
    user_id: uuid.UUID,
    *,
    ip: str | None = None,
    factory: Factory = SessionFactory,
) -> None:
    """Quita el acceso al negocio. Con user_id propio, la persona sale del negocio."""
    leaving = user_id == member.user_id
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        target = await _lock_membership(session, member.tenant_id, user_id)
        if not leaving:
            _require_manage(member, target.role)
        if (
            target.role is MembershipRole.OWNER
            and await _owner_count(session, member.tenant_id) <= 1
        ):
            raise ConflictError(LAST_OWNER)
        audit.record(
            session,
            tenant_id=member.tenant_id,
            actor_user_id=member.user_id,
            action="member.leave" if leaving else "member.remove",
            entity_type="user",
            entity_id=user_id,
            diff={"role": [target.role.value, None]},
            ip=ip,
        )
        await session.delete(target)


# --- Invitaciones (lado del negocio) ---


def _invitation_info(inv: TenantInvitation, now: datetime) -> InvitationInfo:
    return InvitationInfo(
        inv.id, inv.email, inv.role, inv.expires_at, inv.created_at, inv.expires_at <= now
    )


async def create_invitation(
    member: Member,
    email: str,
    role: MembershipRole,
    *,
    ip: str | None = None,
    factory: Factory = SessionFactory,
) -> tuple[InvitationInfo, str]:
    """Crea la invitación y devuelve el enlace. El enlace se muestra UNA vez: no se guarda."""
    _require_manage(member, role)
    email = _clean_email(email)
    raw = new_secret()
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        already = await session.scalar(
            select(TenantMembership.id)
            .join(User, User.id == TenantMembership.user_id)
            .where(TenantMembership.tenant_id == member.tenant_id, User.email == email)
        )
        if already is not None:
            raise ConflictError("Esa persona ya es miembro del negocio")
        # Si ya había una invitación abierta para ese correo, se anula: vale solo la nueva.
        previous = await session.scalar(
            select(TenantInvitation)
            .where(
                TenantInvitation.tenant_id == member.tenant_id,
                TenantInvitation.email == email,
                TenantInvitation.accepted_at.is_(None),
                TenantInvitation.revoked_at.is_(None),
            )
            .with_for_update()
        )
        if previous is not None:
            _require_manage(member, previous.role)
            previous.revoked_at = func.now()
            await session.flush()
        invitation = TenantInvitation(
            tenant_id=member.tenant_id,
            email=email,
            role=role,
            token_hash=hash_secret(raw),
            invited_by_user_id=member.user_id,
            expires_at=_now() + timedelta(hours=INVITATION_HOURS),
        )
        session.add(invitation)
        await session.flush()
        audit.record(
            session,
            tenant_id=member.tenant_id,
            actor_user_id=member.user_id,
            action="invitation.create",
            entity_type="invitation",
            entity_id=invitation.id,
            diff={"email": email, "role": role.value},
            ip=ip,
        )
        await session.refresh(invitation)
        return _invitation_info(invitation, _now()), raw


async def list_invitations(
    member: Member, *, factory: Factory = SessionFactory
) -> list[InvitationInfo]:
    """Las invitaciones sin usar ni anular que esta persona puede gestionar (incluye vencidas)."""
    if not can_edit_tenant(member.role):
        raise PermissionDeniedError("Tu rol no te permite ver las invitaciones")
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        rows = await session.scalars(
            select(TenantInvitation)
            .where(
                TenantInvitation.tenant_id == member.tenant_id,
                TenantInvitation.accepted_at.is_(None),
                TenantInvitation.revoked_at.is_(None),
            )
            .order_by(TenantInvitation.created_at.desc())
        )
        now = _now()
        return [_invitation_info(i, now) for i in rows if can_manage(member.role, i.role)]


async def revoke_invitation(
    member: Member,
    invitation_id: uuid.UUID,
    *,
    ip: str | None = None,
    factory: Factory = SessionFactory,
) -> None:
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        invitation = await session.scalar(
            select(TenantInvitation)
            .where(
                TenantInvitation.id == invitation_id,
                TenantInvitation.accepted_at.is_(None),
                TenantInvitation.revoked_at.is_(None),
            )
            .with_for_update()
        )
        if invitation is None:
            raise NotFoundError
        _require_manage(member, invitation.role)
        invitation.revoked_at = func.now()
        audit.record(
            session,
            tenant_id=member.tenant_id,
            actor_user_id=member.user_id,
            action="invitation.revoke",
            entity_type="invitation",
            entity_id=invitation.id,
            ip=ip,
        )


# --- Invitaciones (lado de la persona invitada, todavía sin sesión) ---


async def _resolve(token: str, factory: Factory) -> tuple[uuid.UUID, uuid.UUID]:
    """(id, negocio) de la invitación abierta de ese enlace, sin tener negocio activo."""
    async with factory() as session:
        row = (
            await session.execute(
                text("SELECT id, tenant_id FROM open_invitation(:h)"), {"h": hash_secret(token)}
            )
        ).first()
    if row is None:
        raise InvalidInvitationError
    return row.id, row.tenant_id


async def preview_invitation(token: str, *, factory: Factory = SessionFactory) -> InvitationPreview:
    """Lo que ve la persona al abrir el enlace, antes de aceptar."""
    invitation_id, tenant_id = await _resolve(token, factory)
    async with tenant_session(tenant_id, factory=factory) as session:
        invitation = await session.get_one(TenantInvitation, invitation_id)
        tenant = await session.get_one(Tenant, tenant_id)
        has_account = await session.scalar(
            select(User.id).where(User.email == invitation.email, User.deleted_at.is_(None))
        )
        return InvitationPreview(
            tenant.name,
            invitation.email,
            invitation.role,
            invitation.expires_at,
            has_account is not None,
        )


async def accept_invitation(
    token: str,
    password: str,
    full_name: str | None = None,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
    factory: Factory = SessionFactory,
) -> tuple[NewSession, uuid.UUID]:
    """Acepta la invitación y abre la sesión. Devuelve la sesión y el negocio.

    - Sin cuenta en Saint: se crea con el nombre y la contraseña que la persona escribe.
    - Con cuenta: debe escribir su contraseña de siempre (así se comprueba que es ella).
    """
    invitation_id, tenant_id = await _resolve(token, factory)
    async with tenant_session(tenant_id, factory=factory) as session:
        invitation = await session.get_one(TenantInvitation, invitation_id, with_for_update=True)
        # Se vuelve a mirar con la fila bloqueada: si dos personas usan el enlace a la vez,
        # solo la primera entra.
        if (
            invitation.accepted_at is not None
            or invitation.revoked_at is not None
            or invitation.expires_at <= _now()
        ):
            raise InvalidInvitationError
        user = await session.scalar(
            select(User).where(User.email == invitation.email).with_for_update()
        )
        if user is not None:
            ok = verify_password(password, user.password_hash)
            if not (ok and user.is_active and user.deleted_at is None):
                raise InvalidCredentialsError
        else:
            name = " ".join((full_name or "").split())
            if not name or len(name) > NAME_MAX:
                raise InvalidInputError("Escribe tu nombre")
            if len(password) < MIN_PASSWORD_LENGTH:
                raise InvalidInputError(
                    f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres"
                )
            user = User(
                email=invitation.email, full_name=name, password_hash=hash_password(password)
            )
            session.add(user)
            await session.flush()

        await session.execute(
            insert(TenantMembership)
            .values(tenant_id=tenant_id, user_id=user.id, role=invitation.role)
            .on_conflict_do_nothing(index_elements=["tenant_id", "user_id"])
        )
        invitation.accepted_at = func.now()
        invitation.accepted_by_user_id = user.id
        audit.record(
            session,
            tenant_id=tenant_id,
            actor_user_id=user.id,
            action="invitation.accept",
            entity_type="invitation",
            entity_id=invitation.id,
            diff={"role": invitation.role.value},
            ip=ip,
        )
        user.last_login_at = func.now()
        expires_at = _now() + timedelta(days=REFRESH_TOKEN_DAYS)
        raw = await open_session(session, user.id, expires_at, user_agent, ip)
        await session.flush()
        await session.refresh(user)
    return NewSession(user, raw, expires_at), tenant_id
