"""Worker de Saint: procesa los avisos de Evolution y envía los mensajes salientes.

Para arrancarlo (en otra terminal, junto a la API):
    uv run arq app.workers.ingest.WorkerSettings
"""

import logging
import uuid
from typing import Any

from arq import Retry, cron
from arq.worker import func
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.queue import (
    INGEST_QUEUE,
    PROCESS_WEBHOOK_EVENT,
    SEND_OUTBOUND_MESSAGE,
    enqueue_outbound,
    enqueue_webhook_event,
    redis_settings,
)
from app.db.session import build_engine
from app.integrations.evolution import EvolutionClient
from app.services.outbound import SendOutcome, find_queued_messages, send_queued_message
from app.services.webhook_processing import (
    MAX_ATTEMPTS,
    Outcome,
    find_stuck_events,
    process_event,
)

logger = logging.getLogger(__name__)

# Espera antes de reintentar: 10 s, 20 s, 40 s, 80 s...
RETRY_BASE_SECONDS = 10
# Intentos de envío de un mensaje antes de marcarlo como 'failed'.
MAX_SEND_TRIES = 5


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    ctx["engine"] = build_engine(settings.database_url)
    ctx["factory"] = async_sessionmaker(ctx["engine"], expire_on_commit=False)
    ctx["evolution"] = EvolutionClient.from_settings(settings)


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["evolution"].aclose()
    await ctx["engine"].dispose()


async def process_webhook_event(ctx: dict[str, Any], event_id: str, tenant_id: str) -> str:
    result = await process_event(uuid.UUID(event_id), uuid.UUID(tenant_id), factory=ctx["factory"])
    if result.outcome == Outcome.RETRY:
        raise Retry(defer=RETRY_BASE_SECONDS * 2 ** (result.attempts - 1))
    return result.outcome.value


async def send_outbound_message(ctx: dict[str, Any], message_id: str, tenant_id: str) -> str:
    result = await send_queued_message(
        uuid.UUID(message_id),
        uuid.UUID(tenant_id),
        ctx["evolution"],
        final_attempt=ctx.get("job_try", 1) >= MAX_SEND_TRIES,
        factory=ctx["factory"],
    )
    if result.outcome == SendOutcome.RETRY:
        raise Retry(defer=RETRY_BASE_SECONDS * 2 ** (ctx.get("job_try", 1) - 1))
    return result.outcome.value


async def requeue_stuck_events(ctx: dict[str, Any]) -> int:
    """Cada minuto: vuelve a encolar avisos y mensajes salientes que quedaron sin encolar."""
    queued = 0
    for event_id, tenant_id in await find_stuck_events(factory=ctx["factory"]):
        queued += await enqueue_webhook_event(ctx["redis"], event_id, tenant_id)
    for message_id, tenant_id in await find_queued_messages(factory=ctx["factory"]):
        queued += await enqueue_outbound(ctx["redis"], message_id, tenant_id)
    if queued:
        logger.info("Barrido: %s trabajos pendientes vueltos a encolar", queued)
    return queued


class WorkerSettings:
    functions = [
        func(process_webhook_event, name=PROCESS_WEBHOOK_EVENT),
        func(send_outbound_message, name=SEND_OUTBOUND_MESSAGE),
    ]
    cron_jobs = [cron(requeue_stuck_events, second=0, run_at_startup=True)]
    on_startup = startup
    on_shutdown = shutdown
    queue_name = INGEST_QUEUE
    redis_settings = redis_settings(get_settings().redis_url)
    # Un intento más que los nuestros: el último lo marca como 'failed' en la base.
    max_tries = MAX_ATTEMPTS + 1
    # El resultado se guarda poco: así el mismo aviso se puede volver a encolar pronto.
    keep_result = 60
    max_jobs = 10
