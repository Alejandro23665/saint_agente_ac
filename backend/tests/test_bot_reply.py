"""Respuesta del bot: esperar 15 s a que el cliente termine y contestar con un eco."""

import uuid
from dataclasses import dataclass
from datetime import timedelta

import pytest
from arq import Retry
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.queue import REPLY_TO_CONVERSATION, SEND_OUTBOUND_MESSAGE, reply_job_id
from app.db.session import tenant_session
from app.models import Contact, Conversation, Message, Tenant, WebhookEvent
from app.models.enums import (
    ConversationMode,
    MessageDirection,
    MessageStatus,
    SenderType,
)
from app.services.bot_reply import (
    ReplyOutcome,
    answer_conversation,
    echo_text,
    find_conversations_awaiting_reply,
)
from app.services.webhook_ingest import extract_external_id
from app.services.webhook_processing import process_event
from app.workers import ingest
from tests.factories import create_instance, create_tenant
from tests.test_message_ingest import CLIENT, upsert, voice_note


class FakeQueue:
    default_queue_name = "saint:ingest"

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple, dict]] = []

    async def enqueue_job(self, name: str, *args, **kwargs):
        self.jobs.append((name, args, kwargs))
        return object()


@dataclass
class Chat:
    owner_engine: AsyncEngine
    factory: async_sessionmaker[AsyncSession]
    tenant_id: uuid.UUID
    instance_id: uuid.UUID

    async def receive(self, data: dict, event_type: str = "messages.upsert"):
        async with AsyncSession(self.owner_engine) as s, s.begin():
            event = WebhookEvent(
                tenant_id=self.tenant_id,
                channel_instance_id=self.instance_id,
                event_type=event_type,
                external_id=extract_external_id(event_type, data),
                payload={"event": event_type, "data": data},
            )
            s.add(event)
            await s.flush()
            event_id = event.id
        return await process_event(event_id, self.tenant_id, factory=self.factory)

    async def conversation_id(self) -> uuid.UUID:
        async with tenant_session(self.tenant_id, factory=self.factory) as s:
            return await s.scalar(select(Conversation.id))

    async def age(self, seconds: float) -> None:
        """Hace como si los mensajes hubieran llegado hace `seconds` segundos."""
        async with AsyncSession(self.owner_engine) as s, s.begin():
            await s.execute(
                update(Message)
                .where(Message.tenant_id == self.tenant_id)
                .values(created_at=Message.created_at - timedelta(seconds=seconds))
            )

    async def answer(self):
        return await answer_conversation(
            await self.conversation_id(), self.tenant_id, factory=self.factory
        )

    async def messages(self) -> list[Message]:
        async with tenant_session(self.tenant_id, factory=self.factory) as s:
            return list(await s.scalars(select(Message).order_by(Message.created_at, Message.id)))


@pytest.fixture
def factory(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(app_engine, expire_on_commit=False)


@pytest.fixture
async def chat(owner_engine: AsyncEngine, factory) -> Chat:
    async with AsyncSession(owner_engine) as s, s.begin():
        tenant = await create_tenant(s, "Bot")
        instance = await create_instance(s, tenant)
        c = Chat(owner_engine, factory, tenant.id, instance.id)
    return c


async def test_mensaje_del_cliente_pide_respuesta(chat: Chat) -> None:
    result = await chat.receive(upsert("M1"))
    assert result.reply_conversation_id == await chat.conversation_id()
    # Lo que escribe el dueño desde su teléfono no pide respuesta del bot.
    mine = await chat.receive(upsert("M2", from_me=True))
    assert mine.reply_conversation_id is None


async def test_espera_15_segundos_y_contesta_todo_junto(chat: Chat) -> None:
    await chat.receive(upsert("M1", text="Hola"))
    await chat.receive(upsert("M2", text="¿Tienen pan dulce?"))

    waiting = await chat.answer()
    assert waiting.outcome == ReplyOutcome.WAIT
    assert 0 < waiting.wait_seconds <= 15

    await chat.age(16)
    replied = await chat.answer()
    assert replied.outcome == ReplyOutcome.REPLIED

    *inbound, reply = await chat.messages()
    assert all(m.processed_at is not None for m in inbound)
    assert reply.id == replied.message_id
    assert (reply.direction, reply.sender_type, reply.status) == (
        MessageDirection.OUTBOUND,
        SenderType.BOT,
        MessageStatus.QUEUED,
    )
    assert reply.text == "Recibí:\nHola\n¿Tienen pan dulce?"

    # Ya no queda nada por contestar.
    assert (await chat.answer()).outcome == ReplyOutcome.NOTHING


async def test_un_mensaje_nuevo_reinicia_la_espera(chat: Chat) -> None:
    await chat.receive(upsert("M1", text="Hola"))
    await chat.age(10)
    await chat.receive(upsert("M2", text="una pregunta"))
    result = await chat.answer()
    assert result.outcome == ReplyOutcome.WAIT
    assert result.wait_seconds > 10  # cuenta desde el último mensaje, no el primero


async def test_si_no_para_de_escribir_contesta_a_los_2_minutos(chat: Chat) -> None:
    await chat.receive(upsert("M1"))
    await chat.age(125)
    await chat.receive(upsert("M2", text="y otra cosa"))
    assert (await chat.answer()).outcome == ReplyOutcome.REPLIED


async def test_conversacion_atendida_por_una_persona(chat: Chat) -> None:
    await chat.receive(upsert("M1"))
    async with AsyncSession(chat.owner_engine) as s, s.begin():
        await s.execute(update(Conversation).values(mode=ConversationMode.HUMAN))
    await chat.age(16)
    assert (await chat.answer()).outcome == ReplyOutcome.NOT_BOT
    [message] = await chat.messages()
    assert message.processed_at is not None  # el bot no lo contestará después


async def test_contacto_bloqueado_no_recibe_respuesta(chat: Chat) -> None:
    await chat.receive(upsert("M1"))
    async with AsyncSession(chat.owner_engine) as s, s.begin():
        await s.execute(update(Contact).values(blocked=True))
    await chat.age(16)
    assert (await chat.answer()).outcome == ReplyOutcome.NOT_BOT
    [message] = await chat.messages()  # no hay respuesta en la cola
    assert message.processed_at is not None


async def test_negocio_desactivado_no_contesta(chat: Chat) -> None:
    """Lo que ya estaba pendiente cuando se desactivó el negocio tampoco se contesta."""
    await chat.receive(upsert("M1"))
    async with AsyncSession(chat.owner_engine) as s, s.begin():
        await s.execute(update(Tenant).where(Tenant.id == chat.tenant_id).values(is_active=False))
    await chat.age(16)
    assert (await chat.answer()).outcome == ReplyOutcome.NOT_BOT
    [message] = await chat.messages()
    assert message.processed_at is not None


async def test_lista_de_telefonos_de_prueba(chat: Chat, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "bot_test_phones", "+58 414 000 0000")
    await chat.receive(upsert("M1"))
    await chat.age(16)
    assert (await chat.answer()).outcome == ReplyOutcome.NOT_BOT

    monkeypatch.setattr(get_settings(), "bot_test_phones", "+58 414 000 0000, +58 412-1234567")
    await chat.receive(upsert("M2"))
    await chat.age(16)
    assert (await chat.answer()).outcome == ReplyOutcome.REPLIED


async def test_mensajes_viejos_no_se_contestan(chat: Chat) -> None:
    await chat.receive(upsert("M1"))
    await chat.age(2 * 3600)
    assert (await chat.answer()).outcome == ReplyOutcome.NOTHING
    [message] = await chat.messages()
    assert message.processed_at is not None


def test_eco_de_nota_de_voz_y_foto() -> None:
    from app.models.enums import ContentType

    voice = Message(content_type=ContentType.AUDIO, text=None)
    photo = Message(content_type=ContentType.IMAGE, text="Este")
    assert echo_text([voice, photo]) == "Recibí:\n[nota de voz]\n[foto] Este"


async def test_barrido_encuentra_conversaciones_sin_contestar(chat: Chat) -> None:
    await chat.receive(voice_note())
    conversation_id = await chat.conversation_id()
    assert conversation_id not in [
        c for c, _ in await find_conversations_awaiting_reply(factory=chat.factory)
    ]
    await chat.age(40)
    found = await find_conversations_awaiting_reply(factory=chat.factory)
    assert (conversation_id, chat.tenant_id) in found


async def test_tareas_del_worker(chat: Chat) -> None:
    queue = FakeQueue()
    ctx = {"factory": chat.factory, "redis": queue, "job_try": 1}

    # 1) Llega el aviso: el worker lo procesa y programa la respuesta a 15 s.
    async with AsyncSession(chat.owner_engine) as s, s.begin():
        event = WebhookEvent(
            tenant_id=chat.tenant_id,
            channel_instance_id=chat.instance_id,
            event_type="messages.upsert",
            external_id="W1",
            payload={"event": "messages.upsert", "data": upsert("W1", jid=CLIENT)},
        )
        s.add(event)
        await s.flush()
        event_id = event.id
    await ingest.process_webhook_event(ctx, str(event_id), str(chat.tenant_id))
    conversation_id = await chat.conversation_id()
    [(name, args, kwargs)] = queue.jobs
    assert name == REPLY_TO_CONVERSATION
    assert args == (str(conversation_id), str(chat.tenant_id))
    assert kwargs["_job_id"] == reply_job_id(conversation_id)
    assert kwargs["_defer_by"] == 15

    # 2) Aún no pasaron 15 s: la tarea se aplaza.
    with pytest.raises(Retry):
        await ingest.reply_to_conversation(ctx, str(conversation_id), str(chat.tenant_id))

    # 3) Pasaron: contesta y encola el envío.
    await chat.age(16)
    result = await ingest.reply_to_conversation(ctx, str(conversation_id), str(chat.tenant_id))
    assert result == "replied"
    assert queue.jobs[-1][0] == SEND_OUTBOUND_MESSAGE

    async with tenant_session(chat.tenant_id, factory=chat.factory) as s:
        bot_replies = await s.scalar(
            select(func.count()).select_from(Message).where(Message.sender_type == SenderType.BOT)
        )
    assert bot_replies == 1
