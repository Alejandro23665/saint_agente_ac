"""Recibir un aviso (webhook) de Evolution y guardarlo tal cual en webhook_events.

Aquí NO se procesa el mensaje: solo se comprueba que el aviso es auténtico y se guarda.
Así respondemos a Evolution en milisegundos y nunca perdemos un aviso aunque el resto
del sistema esté lento o caído. El procesamiento llega en los bloques 3.5 y 3.6.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import verify_secret
from app.db.session import SessionFactory, tenant_session
from app.models import ChannelInstance, WebhookEvent
from app.models.enums import WebhookEventStatus

# Eventos que pedimos a Evolution (en el formato con que llegan en el campo "event").
HANDLED_EVENTS = frozenset(
    {"messages.upsert", "messages.update", "send.message", "connection.update", "qrcode.updated"}
)

# Campos que nunca guardamos aunque Evolution los mande (credenciales).
SENSITIVE_KEYS = ("apikey",)


class UnknownInstanceError(Exception):
    """El public_id de la URL no corresponde a ningún número activo."""


class InvalidSecretError(Exception):
    """Falta la cabecera secreta o no coincide."""


@dataclass(frozen=True)
class StoredEvent:
    event_id: uuid.UUID | None  # None si era un duplicado
    tenant_id: uuid.UUID
    event_type: str
    status: WebhookEventStatus
    duplicate: bool


async def resolve_instance(
    public_id: str, factory: async_sessionmaker[AsyncSession] = SessionFactory
) -> tuple[uuid.UUID, uuid.UUID]:
    """Busca el número por su public_id y devuelve (channel_instance_id, tenant_id).

    Todavía no sabemos de qué negocio es el aviso, así que no podemos abrir una sesión
    de negocio (RLS no nos dejaría ver nada). Por eso usamos la función de la base
    resolve_channel_instance, que solo responde esta pregunta concreta.
    """
    async with factory() as session, session.begin():
        row = (
            await session.execute(
                text("SELECT id, tenant_id FROM resolve_channel_instance(:public_id)"),
                {"public_id": public_id},
            )
        ).first()
    if row is None:
        raise UnknownInstanceError(public_id)
    return row.id, row.tenant_id


def extract_external_id(event_type: str, data: Any) -> str | None:
    """Identificador del evento según Evolution, para detectar reintentos repetidos.

    - messages.upsert y send.message: el id del mensaje de WhatsApp (data.key.id).
    - messages.update: el id del mensaje más el estado nuevo, porque un mismo mensaje
      recibe varios avisos (entregado, leído...) y todos son distintos.
    - connection.update y qrcode.updated: no traen id; se guardan todos.
    """
    if not isinstance(data, dict):
        return None
    if event_type in ("messages.upsert", "send.message"):
        key = data.get("key")
        message_id = key.get("id") if isinstance(key, dict) else None
        return str(message_id) if message_id else None
    if event_type == "messages.update":
        message_id = data.get("keyId")
        status = data.get("status")
        return f"{message_id}:{status}" if message_id else None
    return None


def clean_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k not in SENSITIVE_KEYS}


async def store_webhook_event(
    public_id: str,
    secret: str | None,
    payload: dict[str, Any],
    factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> StoredEvent:
    """Valida el secreto y guarda el aviso. Lanza UnknownInstanceError o InvalidSecretError."""
    instance_id, tenant_id = await resolve_instance(public_id, factory)
    event_type = str(payload.get("event", ""))
    external_id = extract_external_id(event_type, payload.get("data"))
    status = (
        WebhookEventStatus.PENDING if event_type in HANDLED_EVENTS else WebhookEventStatus.IGNORED
    )

    async with tenant_session(tenant_id, factory=factory) as session:
        secret_hash = await session.scalar(
            select(ChannelInstance.webhook_secret_hash).where(ChannelInstance.id == instance_id)
        )
        if not secret or not secret_hash or not verify_secret(secret, secret_hash):
            raise InvalidSecretError(public_id)

        # ON CONFLICT DO NOTHING: si Evolution reintenta un aviso que ya teníamos,
        # la restricción única lo frena sin error y no se guarda dos veces.
        event_id = await session.scalar(
            insert(WebhookEvent)
            .values(
                tenant_id=tenant_id,
                channel_instance_id=instance_id,
                event_type=event_type,
                external_id=external_id,
                payload=clean_payload(payload),
                status=status,
            )
            .on_conflict_do_nothing(
                index_elements=["channel_instance_id", "event_type", "external_id"]
            )
            .returning(WebhookEvent.id)
        )
        await session.execute(
            update(ChannelInstance)
            .where(ChannelInstance.id == instance_id)
            .values(last_event_at=func.now())
        )

    return StoredEvent(
        event_id=event_id,
        tenant_id=tenant_id,
        event_type=event_type,
        status=status,
        duplicate=event_id is None,
    )
