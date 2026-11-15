"""Registrar un número de WhatsApp de un negocio y mantener su estado al día.

Registrar = crear la instancia en Evolution apuntando su webhook a Saint, y guardar la
fila en channel_instances. Las dos cosas tienen que quedar juntas: si una falla, se
deshace la otra, para no dejar números "huérfanos" en Evolution ni filas sin instancia.
"""

import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.security import encrypt, hash_secret, new_secret
from app.db.session import SessionFactory, tenant_session
from app.integrations.evolution import EvolutionClient, EvolutionError, QrCode, WebhookConfig
from app.models import ChannelInstance
from app.models.enums import Channel, ChannelProvider, InstanceStatus

logger = logging.getLogger(__name__)

# Cabecera en la que Evolution nos manda el secreto del webhook en cada evento.
WEBHOOK_SECRET_HEADER = "X-Saint-Webhook-Secret"

# Estado de Evolution -> estado en Saint.
_STATUS_FROM_EVOLUTION = {
    "open": InstanceStatus.OPEN,
    "connecting": InstanceStatus.CONNECTING,
    "close": InstanceStatus.CLOSE,
    "refused": InstanceStatus.CLOSE,  # se agotaron los QR sin escanear
}
# Motivo de desconexión de WhatsApp (DisconnectReason de Baileys) que significa bloqueo.
BANNED_REASON = 403


def apply_connection_state(
    instance: ChannelInstance,
    state: str,
    *,
    at: datetime,
    status_reason: int | None = None,
    phone_number: str | None = None,
) -> InstanceStatus | None:
    """Traduce el estado de Evolution al nuestro y lo guarda en la fila.

    Lo usan el script (preguntando a Evolution) y el worker (con connection.update).
    Devuelve el estado nuevo, o None si el aviso es más viejo que el estado actual o
    el estado no se conoce.
    """
    status = _STATUS_FROM_EVOLUTION.get(state)
    if status is None:
        return None
    if instance.status_changed_at is not None and at < instance.status_changed_at:
        return None  # aviso atrasado: ya tenemos un estado más reciente
    if status == InstanceStatus.CLOSE and status_reason == BANNED_REASON:
        status = InstanceStatus.BANNED
    if status == InstanceStatus.CONNECTING and instance.status == InstanceStatus.QR_PENDING:
        status = InstanceStatus.QR_PENDING  # sigue esperando que escaneen el QR
    instance.status = status
    instance.status_changed_at = at
    if status == InstanceStatus.OPEN:
        instance.display_identifier = phone_number or instance.display_identifier
        instance.last_connected_at = at
    return status


@dataclass(frozen=True)
class RegisteredNumber:
    channel_instance_id: uuid.UUID
    public_id: str
    evolution_instance_name: str
    webhook_url: str
    qr: QrCode | None


def webhook_url_for(public_id: str, settings: Settings | None = None) -> str:
    base = (settings or get_settings()).public_webhook_base_url.rstrip("/")
    return f"{base}/webhooks/evolution/{public_id}"


async def register_whatsapp_number(
    evo: EvolutionClient,
    tenant_id: uuid.UUID,
    *,
    groups_enabled: bool = False,
    debounce_seconds: int = 15,
    settings: Settings | None = None,
    factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> RegisteredNumber:
    settings = settings or get_settings()

    # public_id va en la URL del webhook: aleatorio (144 bits), no se puede adivinar ni
    # revela el id interno. El nombre en Evolution es corto y no dice de qué negocio es.
    public_id = secrets.token_urlsafe(18)
    instance_name = f"saint-{secrets.token_hex(6)}"
    webhook_secret = new_secret()
    url = webhook_url_for(public_id, settings)

    # 1. Primero Evolution: si falla, no hay nada que deshacer en la base.
    created = await evo.create_instance(
        instance_name,
        groups_enabled=groups_enabled,
        webhook=WebhookConfig(url=url, headers={WEBHOOK_SECRET_HEADER: webhook_secret}),
    )

    # 2. Después la base. Si falla, se borra la instancia recién creada en Evolution.
    try:
        async with tenant_session(tenant_id, factory=factory) as session:
            instance = ChannelInstance(
                tenant_id=tenant_id,
                public_id=public_id,
                channel=Channel.WHATSAPP,
                provider=ChannelProvider.EVOLUTION,
                evolution_instance_name=created.name,
                status=InstanceStatus.QR_PENDING if created.qr else InstanceStatus.CREATED,
                instance_token_encrypted=encrypt(created.token),
                webhook_secret_hash=hash_secret(webhook_secret),
                groups_enabled=groups_enabled,
                debounce_seconds=debounce_seconds,
            )
            session.add(instance)
            await session.flush()
            instance_id = instance.id
    except Exception:
        logger.exception("No se pudo guardar el número; se borra %s de Evolution", created.name)
        try:
            await evo.delete_instance(created.name)
        except EvolutionError:
            logger.exception("Tampoco se pudo borrar %s de Evolution", created.name)
        raise

    # El secreto en claro solo existió en memoria y en Evolution; aquí se olvida.
    return RegisteredNumber(
        channel_instance_id=instance_id,
        public_id=public_id,
        evolution_instance_name=created.name,
        webhook_url=url,
        qr=created.qr,
    )


async def refresh_connection_status(
    evo: EvolutionClient,
    tenant_id: uuid.UUID,
    channel_instance_id: uuid.UUID,
    *,
    factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> InstanceStatus:
    """Pregunta a Evolution el estado del número y lo guarda.

    Al conectarse también guarda el teléfono visible (display_identifier). Con la API y
    el worker en marcha esto llega solo con connection.update; el script lo usa mientras
    espera el escaneo del QR.
    """
    async with tenant_session(tenant_id, factory=factory) as session:
        instance = await session.scalar(
            select(ChannelInstance).where(ChannelInstance.id == channel_instance_id)
        )
        if instance is None or instance.evolution_instance_name is None:
            raise LookupError("El número no existe en este negocio")

        state = await evo.connection_state(instance.evolution_instance_name)
        phone = None
        if state == "open":
            phone = (await evo.fetch_instance(instance.evolution_instance_name)).phone_number
        status = apply_connection_state(instance, state, at=datetime.now(UTC), phone_number=phone)
        return status or instance.status
