"""Inicio de sesión del panel: comprobar la contraseña, abrir, renovar y cerrar sesiones.

Una sesión es una fila de refresh_tokens. El pase de renovación que recibe el navegador
es un texto aleatorio; la base guarda solo su huella (SHA-256), así que con una copia de
la base nadie puede hacerse pasar por un usuario.

Cada renovación ROTA el pase: el viejo queda revocado y se entrega uno nuevo, con el
mismo vencimiento (la sesión dura 7 días como máximo desde que se escribió la contraseña).
Si alguien presenta un pase ya revocado, es señal de que se copió: se cierran todas las
sesiones de ese usuario.

users y refresh_tokens no tienen RLS (hay que encontrar al usuario antes de saber su
negocio). Los negocios a los que pertenece se leen con user_session, que sí aplica RLS.
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.auth_tokens import (
    MIN_PASSWORD_LENGTH,
    REFRESH_TOKEN_DAYS,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from app.core.security import hash_secret, new_secret
from app.db.session import SessionFactory, user_session
from app.models import RefreshToken, Tenant, TenantMembership, User
from app.models.enums import MembershipRole

# Freno a quien prueba contraseñas: 5 fallos seguidos con un correo, o 20 desde una misma
# dirección IP, bloquean los intentos durante 15 minutos.
MAX_FAILURES_PER_EMAIL = 5
MAX_FAILURES_PER_IP = 20
LOCK_SECONDS = 15 * 60
USER_AGENT_MAX = 300
NAME_MAX = 120


class InvalidCredentialsError(Exception):
    """Correo o contraseña incorrectos (no se dice cuál de los dos)."""


class TooManyAttemptsError(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("Demasiados intentos")
        self.retry_after = retry_after


class InvalidSessionError(Exception):
    """El pase de renovación no sirve: no existe, venció, se revocó o el usuario no puede entrar."""


class NotMemberError(Exception):
    """El usuario no pertenece a ese negocio (o el negocio está desactivado)."""


class WrongPasswordError(Exception):
    """La contraseña actual que escribió la persona no es la correcta."""


class InvalidAccountInputError(Exception):
    """Un dato de la cuenta no es válido (contraseña nueva muy corta, nombre vacío...).
    El mensaje se muestra tal cual."""


@dataclass(frozen=True)
class TenantAccess:
    id: uuid.UUID
    name: str
    slug: str
    role: MembershipRole


@dataclass(frozen=True)
class NewSession:
    user: User
    refresh_token: str  # el texto que va a la cookie; la base no lo guarda
    expires_at: datetime


class LoginLimiter:
    """Cuenta los fallos en Redis. Las claves vencen solas a los 15 minutos."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    @staticmethod
    def _keys(email: str, ip: str | None) -> list[str]:
        # Huella del correo: no dejamos correos legibles en Redis.
        email_key = hashlib.sha256(email.strip().lower().encode()).hexdigest()
        keys = [f"saint:login-fail:email:{email_key}"]
        if ip:
            keys.append(f"saint:login-fail:ip:{ip}")
        return keys

    async def check(self, email: str, ip: str | None) -> None:
        limits = (MAX_FAILURES_PER_EMAIL, MAX_FAILURES_PER_IP)
        for key, limit in zip(self._keys(email, ip), limits, strict=False):
            failures = int(await self._redis.get(key) or 0)
            if failures >= limit:
                ttl = await self._redis.ttl(key)
                raise TooManyAttemptsError(ttl if ttl > 0 else LOCK_SECONDS)

    async def failed(self, email: str, ip: str | None) -> None:
        for key in self._keys(email, ip):
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, LOCK_SECONDS, nx=True)
                await pipe.execute()

    async def succeeded(self, email: str) -> None:
        await self._redis.delete(*self._keys(email, None))


def _now() -> datetime:
    return datetime.now(UTC)


async def open_session(
    session: AsyncSession,
    user_id: uuid.UUID,
    expires_at: datetime,
    user_agent: str | None,
    ip: str | None,
) -> str:
    raw = new_secret()
    session.add(
        RefreshToken(
            user_id=user_id,
            token_hash=hash_secret(raw),
            expires_at=expires_at,
            user_agent=(user_agent or "")[:USER_AGENT_MAX] or None,
            ip=ip,
        )
    )
    return raw


async def login(
    email: str,
    password: str,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
    factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> NewSession:
    """Comprueba correo y contraseña y abre una sesión de 7 días."""
    async with factory() as session, session.begin():
        user = await session.scalar(
            select(User).where(User.email == email.strip()).with_for_update()
        )
        can_login = user is not None and user.is_active and user.deleted_at is None
        # Se verifica siempre (con un hash falso si no hay usuario): misma demora en todos
        # los casos, para no revelar qué correos existen.
        ok = verify_password(password, user.password_hash if user else None)
        if not (ok and can_login):
            raise InvalidCredentialsError
        if password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        user.last_login_at = func.now()
        expires_at = _now() + timedelta(days=REFRESH_TOKEN_DAYS)
        raw = await open_session(session, user.id, expires_at, user_agent, ip)
    return NewSession(user, raw, expires_at)


async def refresh(
    refresh_token: str,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
    factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> NewSession:
    """Cambia el pase de renovación por uno nuevo (el viejo deja de servir)."""
    reused_by: uuid.UUID | None = None
    async with factory() as session, session.begin():
        row = await session.scalar(
            select(RefreshToken)
            .where(RefreshToken.token_hash == hash_secret(refresh_token))
            .with_for_update()
        )
        if row is None:
            raise InvalidSessionError
        if row.revoked_at is not None:
            reused_by = row.user_id
        else:
            user = await session.get(User, row.user_id)
            if row.expires_at <= _now() or not user.is_active or user.deleted_at is not None:
                raise InvalidSessionError
            row.revoked_at = func.now()
            raw = await open_session(session, user.id, row.expires_at, user_agent, ip)
            return NewSession(user, raw, row.expires_at)

    # Pase ya usado: alguien lo copió. Se cierran todas las sesiones del usuario.
    await revoke_all(reused_by, factory=factory)
    raise InvalidSessionError


async def logout(
    refresh_token: str, *, factory: async_sessionmaker[AsyncSession] = SessionFactory
) -> None:
    async with factory() as session, session.begin():
        await session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.token_hash == hash_secret(refresh_token),
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=func.now())
        )


async def revoke_all(
    user_id: uuid.UUID, *, factory: async_sessionmaker[AsyncSession] = SessionFactory
) -> None:
    """Cierra todas las sesiones abiertas de un usuario (en todos sus dispositivos)."""
    async with factory() as session, session.begin():
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=func.now())
        )


async def end_sessions(
    user_id: uuid.UUID,
    *,
    keep_refresh_token: str | None = None,
    factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> int:
    """Cierra a propósito las sesiones abiertas de un usuario (salvo keep_refresh_token).

    Se hace VENCIÉNDOLAS, no marcándolas como revocadas: un pase revocado que se vuelve a
    presentar se toma por robado y cierra todas las sesiones (ver refresh). Aquí el cierre es
    voluntario: cuando ese dispositivo intente renovar, solo recibe "sesión terminada" y no
    arrastra las sesiones nuevas. Devuelve cuántas cerró.
    """
    async with factory() as session, session.begin():
        return await _end_sessions(session, user_id, keep_refresh_token)


async def _end_sessions(
    session: AsyncSession, user_id: uuid.UUID, keep_refresh_token: str | None
) -> int:
    stmt = update(RefreshToken).where(
        RefreshToken.user_id == user_id,
        RefreshToken.revoked_at.is_(None),
        RefreshToken.expires_at > func.now(),
    )
    if keep_refresh_token:
        stmt = stmt.where(RefreshToken.token_hash != hash_secret(keep_refresh_token))
    result = await session.execute(stmt.values(expires_at=func.now()))
    return result.rowcount


async def change_password(
    user_id: uuid.UUID,
    current_password: str,
    new_password: str,
    *,
    keep_refresh_token: str | None = None,
    factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> int:
    """Cambia la contraseña y cierra las demás sesiones. Devuelve cuántas cerró.

    La sesión desde la que se cambió (keep_refresh_token) sigue abierta: así quien cambia su
    contraseña no tiene que volver a entrar, pero cualquier otro dispositivo (por ejemplo,
    uno que se perdió o donde alguien más tenía acceso) deja de servir.
    """
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise InvalidAccountInputError(
            f"La contraseña nueva debe tener al menos {MIN_PASSWORD_LENGTH} caracteres"
        )
    async with factory() as session, session.begin():
        user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None or not user.is_active or user.deleted_at is not None:
            raise InvalidSessionError
        if not verify_password(current_password, user.password_hash):
            raise WrongPasswordError
        if new_password == current_password:
            raise InvalidAccountInputError("La contraseña nueva debe ser distinta de la actual")
        user.password_hash = hash_password(new_password)
        return await _end_sessions(session, user_id, keep_refresh_token)


async def update_profile(
    user_id: uuid.UUID,
    full_name: str,
    *,
    factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> User:
    """Cambia el nombre con el que la persona aparece ante su equipo."""
    name = " ".join(full_name.split())
    if not name or len(name) > NAME_MAX:
        raise InvalidAccountInputError(f"El nombre debe tener entre 1 y {NAME_MAX} caracteres")
    async with factory() as session, session.begin():
        user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None or not user.is_active or user.deleted_at is not None:
            raise InvalidSessionError
        user.full_name = name
    return user


async def tenants_for(
    user_id: uuid.UUID, *, factory: async_sessionmaker[AsyncSession] = SessionFactory
) -> list[TenantAccess]:
    """Los negocios activos del usuario, con su rol en cada uno (ordenados por nombre)."""
    async with user_session(user_id, factory=factory) as session:
        rows = await session.execute(
            select(Tenant.id, Tenant.name, Tenant.slug, TenantMembership.role)
            .join(TenantMembership, TenantMembership.tenant_id == Tenant.id)
            .where(
                TenantMembership.user_id == user_id,
                Tenant.is_active.is_(True),
                Tenant.deleted_at.is_(None),
            )
            .order_by(Tenant.name)
        )
        return [TenantAccess(*row) for row in rows]


async def access_to(
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> TenantAccess:
    for tenant in await tenants_for(user_id, factory=factory):
        if tenant.id == tenant_id:
            return tenant
    raise NotMemberError


async def get_active_user(
    user_id: uuid.UUID, *, factory: async_sessionmaker[AsyncSession] = SessionFactory
) -> User | None:
    async with factory() as session:
        user = await session.get(User, user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        return None
    return user
