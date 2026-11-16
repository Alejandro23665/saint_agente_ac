"""Procesar los avisos guardados en webhook_events (lo hace el worker de la cola ingest).

Cada tipo de aviso tiene su "manejador" (handler). Aquí está la maquinaria: tomar el
aviso sin que otro worker lo tome a la vez, llamar al manejador, marcar el resultado y
reintentar si falla. Los manejadores de mensajes están en message_ingest y el de
conexión en connection_events.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import SessionFactory, tenant_session
from app.models import WebhookEvent
from app.models.enums import WebhookEventStatus
from app.services import connection_events, message_ingest

logger = logging.getLogger(__name__)

# Después de tantos intentos fallidos el aviso queda en 'failed' para revisarlo a mano.
MAX_ATTEMPTS = 5
# El error se guarda recortado: basta para entender qué pasó.
MAX_ERROR_LENGTH = 2000

# Un manejador puede devolver el id de una conversación a la que el bot debe contestar.
Handler = Callable[[AsyncSession, WebhookEvent], Awaitable[uuid.UUID | None]]


async def _nothing_to_do(session: AsyncSession, event: WebhookEvent) -> None:
    """El QR ya lo muestra el script de registro; aquí no hay nada más que hacer."""


# Tipo de aviso -> función que lo procesa.
HANDLERS: dict[str, Handler] = {
    "connection.update": connection_events.handle_connection_update,
    "qrcode.updated": _nothing_to_do,
    "messages.upsert": message_ingest.handle_message_upsert,
    "send.message": message_ingest.handle_send_message,
    "messages.update": message_ingest.handle_message_update,
}


class Outcome(StrEnum):
    PROCESSED = "processed"
    NO_HANDLER = "no_handler"  # todavía no sabemos procesar este tipo: sigue pendiente
    SKIPPED = "skipped"  # ya no estaba pendiente u otro worker lo tiene
    RETRY = "retry"  # falló; se vuelve a intentar
    FAILED = "failed"  # falló demasiadas veces


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    attempts: int = 0
    error: str | None = None
    # Conversación con un mensaje nuevo del cliente: el worker programa la respuesta.
    reply_conversation_id: uuid.UUID | None = None
    # El tipo de aviso de Evolution (para saber qué avisar al panel).
    event_type: str | None = None


async def process_event(
    event_id: uuid.UUID,
    tenant_id: uuid.UUID,
    handlers: dict[str, Handler] | None = None,
    factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> Result:
    handlers = HANDLERS if handlers is None else handlers

    async with tenant_session(tenant_id, factory=factory) as session:
        # FOR UPDATE SKIP LOCKED: bloquea la fila mientras la procesamos; si otro worker
        # ya la tiene, en vez de esperar la salta. Así nunca se procesa dos veces a la vez.
        event = await session.scalar(
            select(WebhookEvent)
            .where(WebhookEvent.id == event_id)
            .where(WebhookEvent.status == WebhookEventStatus.PENDING)
            .with_for_update(skip_locked=True)
        )
        if event is None:
            return Result(Outcome.SKIPPED)

        handler = handlers.get(event.event_type)
        if handler is None:
            logger.info("Aviso %s (%s) sin manejador todavía", event.id, event.event_type)
            return Result(Outcome.NO_HANDLER, event.attempts)

        event.attempts += 1
        try:
            # Savepoint: si el manejador falla, solo se deshace lo que hizo él; el
            # contador de intentos y el error sí se guardan.
            async with session.begin_nested():
                reply_conversation_id = await handler(session, event)
        except Exception as exc:  # noqa: BLE001 - cualquier fallo cuenta como intento
            event.error = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]
            if event.attempts >= MAX_ATTEMPTS:
                event.status = WebhookEventStatus.FAILED
                logger.error("Aviso %s falló %s veces: %s", event.id, event.attempts, event.error)
                return Result(Outcome.FAILED, event.attempts, event.error)
            logger.warning("Aviso %s falló (intento %s): %s", event.id, event.attempts, exc)
            return Result(Outcome.RETRY, event.attempts, event.error)

        event.status = WebhookEventStatus.PROCESSED
        event.error = None
        event.processed_at = func.now()
        return Result(
            Outcome.PROCESSED,
            event.attempts,
            reply_conversation_id=reply_conversation_id,
            event_type=event.event_type,
        )


async def find_stuck_events(
    older_than_seconds: int = 120,
    limit: int = 100,
    handlers: dict[str, Handler] | None = None,
    factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Avisos que siguen pendientes tras un rato (por ejemplo, Redis estaba caído cuando
    llegaron y no se pudieron encolar). Solo de tipos que ya sabemos procesar."""
    handlers = HANDLERS if handlers is None else handlers
    if not handlers:
        return []
    async with factory() as session, session.begin():
        rows = await session.execute(
            text(
                "SELECT id, tenant_id FROM pending_webhook_events("
                ":types, make_interval(secs => :secs), :max_attempts, :limit)"
            ),
            {
                "types": list(handlers),
                "secs": older_than_seconds,
                "max_attempts": MAX_ATTEMPTS,
                "limit": limit,
            },
        )
        return [(row.id, row.tenant_id) for row in rows]
