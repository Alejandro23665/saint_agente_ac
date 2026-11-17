"""Responder a un cliente: esperar a que termine de escribir y contestar.

Los clientes de WhatsApp suelen mandar varios mensajes seguidos ("Hola" / "¿tienen
pan?" / "¿a qué hora abren?"). Si el bot contestara cada uno por separado, respondería
tres veces y a medias. Por eso espera (debounce): cuando llega un mensaje se programa
una respuesta para dentro de 15 s; si antes llega otro, la espera vuelve a empezar.
Pasados 15 s sin mensajes nuevos, el bot lee todos los pendientes juntos y contesta una
sola vez.

La respuesta la escribe el modelo de IA (bloque 4.3) con el agente del negocio
(services/agents). Si mientras la IA escribe el cliente manda otro mensaje, esa
respuesta se descarta (llm_runs.status = discarded) y se vuelve a esperar, para
contestar todo junto. Sin clave de Anthropic el bot responde con un eco (bloque 4.2).

Los mensajes pendientes son los entrantes del cliente con processed_at vacío. Al
contestar se marcan como atendidos.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.session import SessionFactory, tenant_session
from app.integrations.llm import LLMClient, LLMError, LLMReply
from app.models import Contact, Conversation, LLMRun, Message
from app.models.enums import (
    ContentType,
    ConversationMode,
    ConversationStatus,
    LLMRunStatus,
    MessageDirection,
    SenderType,
)
from app.services import agents, usage
from app.services.outbound import queue_text

logger = logging.getLogger(__name__)

# Segundos sin mensajes nuevos antes de contestar.
DEBOUNCE_SECONDS = 15
# Si el cliente no para de escribir, se contesta igual pasado este tiempo.
MAX_WAIT_SECONDS = 120
# Mensajes más viejos que esto no se contestan (por ejemplo, tras días apagado).
MAX_AGE = timedelta(hours=1)
# Si la IA falla un momento (Anthropic saturado, sin internet), se reintenta en 30 s.
ERROR_RETRY_SECONDS = 30
MAX_ERROR_LENGTH = 500

# Cómo se muestra en el eco lo que no es texto.
PLACEHOLDERS: dict[ContentType, str] = {
    ContentType.AUDIO: "[nota de voz]",
    ContentType.IMAGE: "[foto]",
    ContentType.VIDEO: "[video]",
    ContentType.DOCUMENT: "[documento]",
    ContentType.STICKER: "[sticker]",
    ContentType.LOCATION: "[ubicación]",
}


class ReplyOutcome(StrEnum):
    REPLIED = "replied"  # se encoló una respuesta
    WAIT = "wait"  # el cliente sigue escribiendo: volver a mirar en wait_seconds
    ERROR = "error"  # la IA falló un momento: volver a intentar en wait_seconds
    FAILED = "failed"  # la IA no puede contestar (por ejemplo, clave inválida)
    NOTHING = "nothing"  # no había mensajes pendientes
    SKIPPED = "skipped"  # otro worker está contestando esta conversación
    NOT_BOT = "not_bot"  # la conversación la atiende una persona, o no se contesta


@dataclass(frozen=True)
class ReplyResult:
    outcome: ReplyOutcome
    message_id: uuid.UUID | None = None  # la respuesta encolada (enviarla tras el commit)
    wait_seconds: float = 0
    error: str | None = None


def echo_text(messages: list[Message]) -> str:
    """Eco del bloque 4.2: repite lo que dijo el cliente, en un solo mensaje."""
    lines = []
    for message in messages:
        placeholder = PLACEHOLDERS.get(message.content_type)
        if message.text and placeholder:
            lines.append(f"{placeholder} {message.text}")
        else:
            lines.append(message.text or placeholder or "[mensaje]")
    return "Recibí:\n" + "\n".join(lines)


def allowed_phones() -> set[str]:
    """Teléfonos a los que el bot puede contestar (BOT_TEST_PHONES). Vacío = a todos."""
    raw = get_settings().bot_test_phones
    return {"+" + "".join(ch for ch in p if ch.isdigit()) for p in raw.split(",") if p.strip()}


async def answer_conversation(
    conversation_id: uuid.UUID,
    tenant_id: uuid.UUID,
    *,
    llm: LLMClient | None = None,
    debounce_seconds: float = DEBOUNCE_SECONDS,
    max_wait_seconds: float = MAX_WAIT_SECONDS,
    factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> ReplyResult:
    """Si el cliente ya terminó de escribir, encola una respuesta. Lo llama el worker.

    Con llm la respuesta la escribe la IA; sin llm (no hay clave) es un eco.
    """
    # 1) ¿Hay que contestar ya? Se mira con la conversación bloqueada, y se confirma
    #    ANTES de llamar a la IA: no se tiene una transacción abierta mientras responde.
    async with tenant_session(tenant_id, factory=factory) as session:
        check = await _ready_to_answer(session, conversation_id, debounce_seconds, max_wait_seconds)
        if isinstance(check, ReplyResult):
            return check
        pending_ids, fresh = check
        if llm is None:
            reply_id = await queue_text(
                session, conversation_id, echo_text(fresh), sender_type=SenderType.BOT
            )
            await _mark_processed(session, pending_ids)
            return ReplyResult(ReplyOutcome.REPLIED, reply_id)
        conversation = await session.get(Conversation, conversation_id)
        agent = await agents.get_agent(session, conversation)
        agent_id, agent_version = agent.id, agent.current_version
        model, system = agent.model, agent.system_prompt
        history = await agents.build_history(session, conversation_id)
        # Lo que salga del negocio después de este momento hace inútil la respuesta.
        snapshot_at = await session.scalar(select(func.clock_timestamp()))
        if not history or history[-1]["role"] != "user":
            # Alguien del negocio ya contestó después de estos mensajes.
            await _mark_processed(session, pending_ids)
            return ReplyResult(ReplyOutcome.NOTHING)

    # 2) La IA escribe la respuesta (unos segundos).
    reply: LLMReply | None = None
    try:
        reply = await llm.reply(model=model, system=system, messages=history)
        failure = None
        if not reply.text:
            failure = LLMError(f"Respuesta vacía (stop_reason={reply.stop_reason})", permanent=True)
    except LLMError as exc:
        failure = exc

    # 3) Guardar. Si mientras tanto el cliente escribió otra cosa, esta respuesta ya no
    #    sirve: se descarta y se vuelve a esperar los 15 s para contestar todo junto.
    async with tenant_session(tenant_id, factory=factory) as session:
        conversation, contact = (
            await session.execute(
                select(Conversation, Contact)
                .join(Contact, Contact.id == Conversation.contact_id)
                .where(Conversation.id == conversation_id)
                .with_for_update(of=Conversation)
            )
        ).one()
        # ¿Alguien del negocio contestó mientras la IA escribía (desde su teléfono)?
        answered = await session.scalar(
            select(func.count())
            .select_from(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.direction == MessageDirection.OUTBOUND,
                Message.created_at > snapshot_at,
            )
        )
        newer = await session.scalar(
            select(func.count())
            .select_from(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.direction == MessageDirection.INBOUND,
                Message.processed_at.is_(None),
                Message.id.not_in(pending_ids),
            )
        )
        run = LLMRun(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            agent_version=agent_version,
            model=reply.model if reply else model,
            input_tokens=reply.input_tokens if reply else 0,
            output_tokens=reply.output_tokens if reply else 0,
            cached_tokens=reply.cached_tokens if reply else 0,
            latency_ms=reply.latency_ms if reply else None,
            status=LLMRunStatus.OK,
        )
        session.add(run)
        # Los tokens se pagan aunque la respuesta se descarte: se cuentan todos.
        await usage.record(
            session,
            tenant_id,
            llm_input_tokens=run.input_tokens,
            llm_output_tokens=run.output_tokens,
        )

        if not _bot_answers(conversation, contact) or answered:
            # La conversación pasó a una persona o ya le contestaron: no se envía.
            run.status = LLMRunStatus.DISCARDED
            await _mark_processed(session, pending_ids)
            return ReplyResult(ReplyOutcome.NOT_BOT if not answered else ReplyOutcome.NOTHING)

        if newer:
            run.status = LLMRunStatus.DISCARDED
            logger.info("Conversación %s: respuesta descartada, llegaron mensajes", conversation_id)
            return ReplyResult(ReplyOutcome.WAIT, wait_seconds=debounce_seconds)

        if failure is not None:
            run.status = LLMRunStatus.ERROR
            run.error = str(failure)[:MAX_ERROR_LENGTH]
            logger.error("Conversación %s: la IA falló: %s", conversation_id, failure)
            if failure.permanent:
                # Reintentar no lo arregla (clave mal puesta, etc.): no se contesta.
                await _mark_processed(session, pending_ids)
                return ReplyResult(ReplyOutcome.FAILED, error=str(failure))
            return ReplyResult(
                ReplyOutcome.ERROR, error=str(failure), wait_seconds=ERROR_RETRY_SECONDS
            )

        await session.flush()
        reply_id = await queue_text(
            session, conversation_id, reply.text, sender_type=SenderType.BOT
        )
        await session.execute(
            update(Message).where(Message.id == reply_id).values(llm_run_id=run.id)
        )
        await _mark_processed(session, pending_ids)
        logger.info(
            "Conversación %s: %s mensajes contestados por la IA (%s tokens)",
            conversation_id,
            len(pending_ids),
            reply.input_tokens + reply.output_tokens,
        )
        return ReplyResult(ReplyOutcome.REPLIED, reply_id)


async def _ready_to_answer(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    debounce_seconds: float,
    max_wait_seconds: float,
) -> ReplyResult | tuple[list[uuid.UUID], list[Message]]:
    """Devuelve (ids pendientes, mensajes a contestar) o el motivo para no contestar ya."""
    # La conversación bloqueada: dos workers nunca contestan a la vez el mismo chat.
    row = (
        await session.execute(
            select(Conversation, Contact)
            .join(Contact, Contact.id == Conversation.contact_id)
            .where(Conversation.id == conversation_id)
            .with_for_update(of=Conversation, skip_locked=True)
        )
    ).first()
    if row is None:
        return ReplyResult(ReplyOutcome.SKIPPED)
    conversation, contact = row

    pending = list(
        await session.scalars(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.direction == MessageDirection.INBOUND,
                Message.processed_at.is_(None),
            )
            .order_by(Message.created_at, Message.id)
        )
    )
    if not pending:
        return ReplyResult(ReplyOutcome.NOTHING)
    pending_ids = [m.id for m in pending]

    if not _bot_answers(conversation, contact):
        # Lo atiende una persona (o es un grupo, o no está en la lista de prueba):
        # se dan por vistos para que el bot no los conteste después.
        await _mark_processed(session, pending_ids)
        return ReplyResult(ReplyOutcome.NOT_BOT)

    now = await session.scalar(select(func.clock_timestamp()))
    fresh = [m for m in pending if now - m.created_at <= MAX_AGE]
    if not fresh:
        await _mark_processed(session, pending_ids)
        return ReplyResult(ReplyOutcome.NOTHING)

    since_last = (now - fresh[-1].created_at).total_seconds()
    since_first = (now - fresh[0].created_at).total_seconds()
    if since_last < debounce_seconds and since_first < max_wait_seconds:
        wait = min(debounce_seconds - since_last, max_wait_seconds - since_first)
        return ReplyResult(ReplyOutcome.WAIT, wait_seconds=max(wait, 0.5))
    return pending_ids, fresh


def _bot_answers(conversation: Conversation, contact: Contact) -> bool:
    if conversation.status != ConversationStatus.OPEN:
        return False
    if conversation.mode != ConversationMode.BOT:
        return False
    if contact.is_group:
        return False  # en grupos el bot no contesta (por ahora)
    phones = allowed_phones()
    return not phones or contact.phone_e164 in phones


async def _mark_processed(session: AsyncSession, message_ids: list[uuid.UUID]) -> None:
    await session.execute(
        update(Message).where(Message.id.in_(message_ids)).values(processed_at=func.now())
    )


async def find_conversations_awaiting_reply(
    older_than_seconds: int = 30,
    limit: int = 100,
    factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Conversaciones con mensajes sin contestar que nadie tomó (Redis caído, etc.)."""
    async with factory() as session, session.begin():
        rows = await session.execute(
            text(
                "SELECT conversation_id, tenant_id FROM conversations_awaiting_reply("
                "make_interval(secs => :secs), make_interval(secs => :max_age), :limit)"
            ),
            {
                "secs": older_than_seconds,
                "max_age": MAX_AGE.total_seconds(),
                "limit": limit,
            },
        )
        return [(row.conversation_id, row.tenant_id) for row in rows]
