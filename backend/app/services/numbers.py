"""Los números de WhatsApp de un negocio, desde el panel (bloque 5.3).

Qué puede hacer cada rol:

| Acción                                        | owner | admin | agent |
|-----------------------------------------------|-------|-------|-------|
| Ver los números y su estado                   | sí    | sí    | sí    |
| Conectar un número nuevo (QR)                 | sí    | sí    | no    |
| Pedir otro QR, desconectar, grupos sí/no      | sí    | sí    | no    |
| Borrar un número                              | sí    | no    | no    |

Conectar reutiliza register_whatsapp_number (Fase 3): Evolution y la base quedan de
acuerdo o no queda nada. Borrar es un borrado lógico: las conversaciones y mensajes del
número se conservan, pero Evolution olvida la instancia y Saint deja de recibir avisos.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import SessionFactory, tenant_session
from app.integrations.evolution import EvolutionClient, EvolutionError, QrCode
from app.models import ChannelInstance
from app.models.enums import InstanceStatus, MembershipRole
from app.services import audit
from app.services.channel_registration import (
    apply_connection_state,
    register_whatsapp_number,
)
from app.services.members import (
    ConflictError,
    InvalidInputError,
    Member,
    NotFoundError,
    PermissionDeniedError,
    can_edit_tenant,
)

logger = logging.getLogger(__name__)

Factory = async_sessionmaker[AsyncSession]

# Tope de números por negocio, para que un error en el panel no llene Evolution.
MAX_NUMBERS_PER_TENANT = 5


@dataclass(frozen=True)
class NumberInfo:
    id: uuid.UUID
    phone: str | None  # +58..., cuando ya se vinculó
    status: InstanceStatus
    groups_enabled: bool
    last_connected_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class NumberWithQr:
    number: NumberInfo
    qr: QrCode | None  # None si ya está conectado


def _info(row: ChannelInstance) -> NumberInfo:
    return NumberInfo(
        row.id,
        row.display_identifier,
        row.status,
        row.groups_enabled,
        row.last_connected_at,
        row.created_at,
    )


def _require_manage(member: Member) -> None:
    if not can_edit_tenant(member.role):
        raise PermissionDeniedError("Tu rol no te permite gestionar los números")


def _clean_phone(phone: str | None) -> str | None:
    """Para el código de vinculación: solo dígitos, con código de país (ej. 584121234567)."""
    if phone is None:
        return None
    digits = "".join(c for c in phone if c.isdigit())
    if not 8 <= len(digits) <= 15:
        raise InvalidInputError("Escribe el número con código de país, ej. +58 412 1234567")
    return digits


async def _get(session: AsyncSession, number_id: uuid.UUID, *, lock: bool = False):
    query = select(ChannelInstance).where(
        ChannelInstance.id == number_id, ChannelInstance.deleted_at.is_(None)
    )
    row = await session.scalar(query.with_for_update() if lock else query)
    if row is None or row.evolution_instance_name is None:
        raise NotFoundError
    return row


async def list_numbers(member: Member, *, factory: Factory = SessionFactory) -> list[NumberInfo]:
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        rows = await session.scalars(
            select(ChannelInstance)
            .where(ChannelInstance.deleted_at.is_(None))
            .order_by(ChannelInstance.created_at)
        )
        return [_info(r) for r in rows]


async def connect_number(
    member: Member,
    evo: EvolutionClient,
    *,
    groups_enabled: bool = False,
    ip: str | None = None,
    factory: Factory = SessionFactory,
) -> NumberWithQr:
    """Crea el número en Evolution y devuelve el primer QR para escanear."""
    _require_manage(member)
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        count = await session.scalar(
            select(func.count())
            .select_from(ChannelInstance)
            .where(ChannelInstance.deleted_at.is_(None))
        )
    if count >= MAX_NUMBERS_PER_TENANT:
        raise ConflictError(f"Un negocio puede tener hasta {MAX_NUMBERS_PER_TENANT} números")

    registered = await register_whatsapp_number(
        evo, member.tenant_id, groups_enabled=groups_enabled, factory=factory
    )
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        row = await _get(session, registered.channel_instance_id)
        audit.record(
            session,
            tenant_id=member.tenant_id,
            actor_user_id=member.user_id,
            action="number.connect",
            entity_type="channel_instance",
            entity_id=row.id,
            diff={"groups_enabled": groups_enabled},
            ip=ip,
        )
        return NumberWithQr(_info(row), registered.qr)


async def refresh_number(
    member: Member,
    evo: EvolutionClient,
    number_id: uuid.UUID,
    *,
    factory: Factory = SessionFactory,
) -> NumberInfo:
    """Pregunta a Evolution el estado real y lo guarda (por si se perdió algún aviso)."""
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        row = await _get(session, number_id, lock=True)
        state = await evo.connection_state(row.evolution_instance_name)
        phone = None
        if state == "open":
            phone = (await evo.fetch_instance(row.evolution_instance_name)).phone_number
        apply_connection_state(row, state, at=datetime.now(UTC), phone_number=phone)
        await session.flush()
        return _info(row)


async def new_qr(
    member: Member,
    evo: EvolutionClient,
    number_id: uuid.UUID,
    *,
    phone: str | None = None,
    factory: Factory = SessionFactory,
) -> NumberWithQr:
    """Otro QR (el anterior vence en unos segundos). Con phone, también un código de
    8 letras para vincular escribiéndolo en el teléfono, sin cámara."""
    _require_manage(member)
    digits = _clean_phone(phone)
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        row = await _get(session, number_id, lock=True)
        if row.status is InstanceStatus.OPEN:
            raise ConflictError("Ese número ya está conectado")
        qr = await evo.get_qr(row.evolution_instance_name, phone_number=digits)
        if qr is None:
            # Evolution dice que ya está conectado: se actualiza el estado.
            phone_number = (await evo.fetch_instance(row.evolution_instance_name)).phone_number
            apply_connection_state(row, "open", at=datetime.now(UTC), phone_number=phone_number)
        else:
            row.status = InstanceStatus.QR_PENDING
            row.status_changed_at = datetime.now(UTC)
        await session.flush()
        return NumberWithQr(_info(row), qr)


async def set_groups(
    member: Member,
    evo: EvolutionClient,
    number_id: uuid.UUID,
    groups_enabled: bool,
    *,
    ip: str | None = None,
    factory: Factory = SessionFactory,
) -> NumberInfo:
    _require_manage(member)
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        row = await _get(session, number_id, lock=True)
        if row.groups_enabled != groups_enabled:
            # Primero Evolution: si falla, la base no cambia y siguen de acuerdo.
            await evo.set_groups(row.evolution_instance_name, groups_enabled=groups_enabled)
            audit.record(
                session,
                tenant_id=member.tenant_id,
                actor_user_id=member.user_id,
                action="number.groups",
                entity_type="channel_instance",
                entity_id=row.id,
                diff={"groups_enabled": [row.groups_enabled, groups_enabled]},
                ip=ip,
            )
            row.groups_enabled = groups_enabled
        await session.flush()
        return _info(row)


async def disconnect_number(
    member: Member,
    evo: EvolutionClient,
    number_id: uuid.UUID,
    *,
    ip: str | None = None,
    factory: Factory = SessionFactory,
) -> NumberInfo:
    """Desvincula el teléfono. El número queda creado y se puede volver a conectar con QR."""
    _require_manage(member)
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        row = await _get(session, number_id, lock=True)
        try:
            await evo.logout(row.evolution_instance_name)
        except EvolutionError as exc:
            # Evolution responde 400 si el teléfono ya no estaba vinculado: da igual.
            if exc.status_code != 400:
                raise
        row.status = InstanceStatus.CLOSE
        row.status_changed_at = datetime.now(UTC)
        audit.record(
            session,
            tenant_id=member.tenant_id,
            actor_user_id=member.user_id,
            action="number.disconnect",
            entity_type="channel_instance",
            entity_id=row.id,
            ip=ip,
        )
        await session.flush()
        return _info(row)


async def delete_number(
    member: Member,
    evo: EvolutionClient,
    number_id: uuid.UUID,
    *,
    ip: str | None = None,
    factory: Factory = SessionFactory,
) -> None:
    """Borra el número de Evolution y lo marca como borrado en Saint (solo el dueño)."""
    if member.role is not MembershipRole.OWNER:
        raise PermissionDeniedError("Solo el dueño puede borrar un número")
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        row = await _get(session, number_id, lock=True)
        try:
            await evo.delete_instance(row.evolution_instance_name)
        except EvolutionError as exc:
            if exc.status_code != 404:  # 404: Evolution ya no la tenía
                raise
        row.deleted_at = func.now()
        row.status = InstanceStatus.CLOSE
        row.status_changed_at = datetime.now(UTC)
        # El token ya no sirve para nada: se borra en vez de guardarlo cifrado.
        row.instance_token_encrypted = None
        audit.record(
            session,
            tenant_id=member.tenant_id,
            actor_user_id=member.user_id,
            action="number.delete",
            entity_type="channel_instance",
            entity_id=row.id,
            diff={"phone": row.display_identifier},
            ip=ip,
        )
