"""Worker de Saint: procesa los avisos de Evolution, contesta y envía los mensajes.

Para arrancarlo (en otra terminal, junto a la API):
    uv run arq app.workers.ingest.WorkerSettings
"""

import logging
import uuid
from typing import Any

from arq import Retry, cron
from arq.worker import func
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import events
from app.core.config import get_settings
from app.core.queue import (
    EMBED_KNOWLEDGE_SOURCE,
    INGEST_QUEUE,
    PROCESS_WEBHOOK_EVENT,
    REPLY_TO_CONVERSATION,
    SEND_OUTBOUND_MESSAGE,
    enqueue_outbound,
    enqueue_reply,
    enqueue_webhook_event,
    redis_settings,
)
from app.db.session import build_engine
from app.integrations.embeddings import OpenRouterEmbedder
from app.integrations.evolution import EvolutionClient
from app.integrations.llm import AnthropicLLM
from app.services.bot_reply import (
    DEBOUNCE_SECONDS,
    ReplyOutcome,
    answer_conversation,
    find_conversations_awaiting_reply,
)
from app.services.knowledge import embed_source
from app.services.outbound import SendOutcome, find_queued_messages, send_queued_message
from app.services.webhook_processing import (
    MAX_ATTEMPTS,
    Outcome,
    Result,
    find_stuck_events,
    process_event,
)

logger = logging.getLogger(__name__)

# Espera antes de reintentar: 10 s, 20 s, 40 s, 80 s...
RETRY_BASE_SECONDS = 10
# Intentos de envío de un mensaje antes de marcarlo como 'failed'.
MAX_SEND_TRIES = 5
# Cada vez que el cliente sigue escribiendo, el trabajo de respuesta se aplaza (Retry).
# Con la espera máxima de 2 min nunca llega a tantos; es solo un tope de seguridad.
MAX_REPLY_TRIES = 200


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    ctx["engine"] = build_engine(settings.database_url)
    ctx["factory"] = async_sessionmaker(ctx["engine"], expire_on_commit=False)
    ctx["evolution"] = EvolutionClient.from_settings(settings)
    # Sin ANTHROPIC_API_KEY queda en None y el bot contesta con un eco.
    ctx["llm"] = AnthropicLLM.from_settings(settings)
    if ctx["llm"] is None:
        logger.warning("Sin ANTHROPIC_API_KEY: el bot contestará con un eco, sin IA")
    # Sin OPENROUTER_API_KEY queda en None: el conocimiento subido queda 'failed' con el
    # aviso, y se reintenta desde el panel cuando la clave esté.
    ctx["embedder"] = OpenRouterEmbedder.from_settings(settings)


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["evolution"].aclose()
    if ctx.get("llm") is not None:
        await ctx["llm"].aclose()
    if ctx.get("embedder") is not None:
        await ctx["embedder"].aclose()
    await ctx["engine"].dispose()


async def process_webhook_event(ctx: dict[str, Any], event_id: str, tenant_id: str) -> str:
    result = await process_event(uuid.UUID(event_id), uuid.UUID(tenant_id), factory=ctx["factory"])
    if result.outcome == Outcome.RETRY:
        raise Retry(defer=RETRY_BASE_SECONDS * 2 ** (result.attempts - 1))
    if result.outcome == Outcome.PROCESSED:
        await _notify_panel(ctx, uuid.UUID(tenant_id), result)
    if result.reply_conversation_id is not None:
        # Mensaje nuevo del cliente: la respuesta sale dentro de 15 s si no escribe más.
        # Si ya había una respuesta programada para este chat, no se crea otra.
        await enqueue_reply(
            ctx["redis"], result.reply_conversation_id, uuid.UUID(tenant_id), DEBOUNCE_SECONDS
        )
    return result.outcome.value


async def _notify_panel(ctx: dict[str, Any], tenant_id: uuid.UUID, result: Result) -> None:
    """Avisa al panel de lo que acaba de cambiar (ya guardado en la base de datos)."""
    if result.event_type == "connection.update":
        await events.publish(ctx["redis"], tenant_id, events.NUMBERS_CHANGED)
    elif result.reply_conversation_id is not None:
        await events.publish(
            ctx["redis"],
            tenant_id,
            events.CONVERSATION_CHANGED,
            conversation_id=result.reply_conversation_id,
        )
    else:
        # Por ejemplo, un mensaje que pasó a entregado o leído.
        await events.publish(ctx["redis"], tenant_id, events.INBOX_CHANGED)


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
    if result.outcome in (SendOutcome.SENT, SendOutcome.FAILED, SendOutcome.UNKNOWN):
        # El mensaje cambió de estado (enviado o fallido): el panel lo muestra.
        await events.publish(ctx["redis"], uuid.UUID(tenant_id), events.INBOX_CHANGED)
    return result.outcome.value


async def reply_to_conversation(ctx: dict[str, Any], conversation_id: str, tenant_id: str) -> str:
    result = await answer_conversation(
        uuid.UUID(conversation_id),
        uuid.UUID(tenant_id),
        llm=ctx.get("llm"),
        factory=ctx["factory"],
    )
    if result.outcome in (ReplyOutcome.WAIT, ReplyOutcome.ERROR):
        # El cliente escribió hace menos de 15 s (o la IA falló un momento): volver a
        # mirar cuando toque.
        raise Retry(defer=result.wait_seconds)
    if result.outcome == ReplyOutcome.REPLIED:
        await enqueue_outbound(ctx["redis"], result.message_id, uuid.UUID(tenant_id))
        await events.publish(
            ctx["redis"],
            uuid.UUID(tenant_id),
            events.CONVERSATION_CHANGED,
            conversation_id=conversation_id,
        )
    return result.outcome.value


async def embed_knowledge_source(ctx: dict[str, Any], source_id: str, tenant_id: str) -> str:
    """Pide los embeddings de un documento subido desde el panel (bloque 5.7)."""
    result = await embed_source(
        uuid.UUID(tenant_id),
        uuid.UUID(source_id),
        embedder=ctx.get("embedder"),
        factory=ctx["factory"],
    )
    if result is None:
        return "skipped"  # ya estaba listo o lo borraron
    await events.publish(
        ctx["redis"], uuid.UUID(tenant_id), events.KNOWLEDGE_CHANGED, source_id=source_id
    )
    return result.status.value


async def requeue_stuck_events(ctx: dict[str, Any]) -> int:
    """Cada minuto: vuelve a encolar avisos, respuestas y envíos que quedaron sin encolar."""
    queued = 0
    for event_id, tenant_id in await find_stuck_events(factory=ctx["factory"]):
        queued += await enqueue_webhook_event(ctx["redis"], event_id, tenant_id)
    for message_id, tenant_id in await find_queued_messages(factory=ctx["factory"]):
        queued += await enqueue_outbound(ctx["redis"], message_id, tenant_id)
    for conversation_id, tenant_id in await find_conversations_awaiting_reply(
        factory=ctx["factory"]
    ):
        queued += await enqueue_reply(ctx["redis"], conversation_id, tenant_id, 0)
    if queued:
        logger.info("Barrido: %s trabajos pendientes vueltos a encolar", queued)
    return queued


class WorkerSettings:
    functions = [
        func(process_webhook_event, name=PROCESS_WEBHOOK_EVENT),
        func(send_outbound_message, name=SEND_OUTBOUND_MESSAGE),
        # keep_result=0: al terminar se borra, y el próximo mensaje del cliente puede
        # programar otra respuesta enseguida (con el mismo id de trabajo).
        func(
            reply_to_conversation,
            name=REPLY_TO_CONVERSATION,
            max_tries=MAX_REPLY_TRIES,
            keep_result=0,
        ),
        # Un intento: OpenRouter ya se reintenta por dentro; si falla, queda 'failed' y
        # se reintenta desde el panel.
        func(embed_knowledge_source, name=EMBED_KNOWLEDGE_SOURCE, max_tries=1),
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
