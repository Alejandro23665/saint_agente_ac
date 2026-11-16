"""Respuesta con IA (bloque 4.3): agente, llm_runs y descartar si el cliente sigue escribiendo.

Claude se simula: FakeLLM hace de modelo y AnthropicLLM se prueba con una respuesta de la
API falsa. Ninguna prueba llama a Anthropic de verdad.
"""

import json

import httpx2 as httpx
import pytest
from sqlalchemy import select

from app.db.session import tenant_session
from app.integrations.llm import AnthropicLLM, LLMError, LLMReply
from app.models import Agent, LLMRun
from app.models.enums import LLMRunStatus, MessageDirection, SenderType
from app.services.agents import DEFAULT_AGENT_KEY, build_history
from app.services.bot_reply import ReplyOutcome, answer_conversation
from tests import test_bot_reply
from tests.test_bot_reply import Chat
from tests.test_message_ingest import upsert

# Las mismas fixtures que test_bot_reply: un negocio con un número y un chat.
factory = test_bot_reply.factory
chat = test_bot_reply.chat


class FakeLLM:
    def __init__(self, text: str = "¡Hola! Ya te confirmo los horarios.", fail=None, during=None):
        self.text = text
        self.fail = fail
        self.during = during  # se ejecuta "mientras la IA escribe"
        self.calls: list[dict] = []

    async def reply(self, *, model, system, messages) -> LLMReply:
        self.calls.append({"model": model, "system": system, "messages": messages})
        if self.during:
            await self.during()
        if self.fail:
            raise self.fail
        return LLMReply(self.text, model, 120, 15, 0, 800, "end_turn")


async def answer(chat: Chat, llm):
    return await answer_conversation(
        await chat.conversation_id(), chat.tenant_id, llm=llm, factory=chat.factory
    )


async def runs(chat: Chat) -> list[LLMRun]:
    async with tenant_session(chat.tenant_id, factory=chat.factory) as s:
        return list(await s.scalars(select(LLMRun).order_by(LLMRun.created_at)))


async def test_la_ia_contesta_y_queda_registrado(chat: Chat) -> None:
    await chat.receive(upsert("M1", text="Hola"))
    await chat.receive(upsert("M2", text="¿A qué hora abren?"))
    await chat.age(16)
    llm = FakeLLM()
    result = await answer(chat, llm)
    assert result.outcome == ReplyOutcome.REPLIED

    [call] = llm.calls
    assert call["model"] == "claude-haiku-5-5"
    assert "Bot" in call["system"]  # el nombre del negocio va en las instrucciones
    assert call["messages"] == [{"role": "user", "content": "Hola\n¿A qué hora abren?"}]

    *inbound, reply = await chat.messages()
    assert all(m.processed_at is not None for m in inbound)
    assert (reply.sender_type, reply.text) == (SenderType.BOT, llm.text)
    [run] = await runs(chat)
    assert reply.llm_run_id == run.id
    assert (run.status, run.input_tokens, run.output_tokens) == (LLMRunStatus.OK, 120, 15)
    assert run.conversation_id == reply.conversation_id and run.agent_id is not None


async def test_el_agente_se_crea_una_sola_vez(chat: Chat) -> None:
    for msg_id in ("M1", "M2"):
        await chat.receive(upsert(msg_id))
        await chat.age(16)
        assert (await answer(chat, FakeLLM())).outcome == ReplyOutcome.REPLIED
    async with tenant_session(chat.tenant_id, factory=chat.factory) as s:
        agents = list(await s.scalars(select(Agent)))
    assert [a.key for a in agents] == [DEFAULT_AGENT_KEY]


async def test_si_el_cliente_escribe_mientras_tanto_se_descarta(chat: Chat) -> None:
    await chat.receive(upsert("M1", text="Hola"))
    await chat.age(16)

    async def new_message():
        await chat.receive(upsert("M2", text="ah, y otra cosa"))

    result = await answer(chat, FakeLLM(during=new_message))
    assert result.outcome == ReplyOutcome.WAIT and result.wait_seconds == 15
    [run] = await runs(chat)
    assert run.status == LLMRunStatus.DISCARDED
    messages = await chat.messages()
    assert all(m.direction == MessageDirection.INBOUND for m in messages)
    assert all(m.processed_at is None for m in messages)  # se contestarán juntos luego

    await chat.age(16)
    llm = FakeLLM()
    assert (await answer(chat, llm)).outcome == ReplyOutcome.REPLIED
    assert llm.calls[0]["messages"][-1]["content"] == "Hola\nah, y otra cosa"


async def test_fallo_pasajero_reintenta(chat: Chat) -> None:
    await chat.receive(upsert("M1"))
    await chat.age(16)
    result = await answer(chat, FakeLLM(fail=LLMError("Anthropic saturado")))
    assert result.outcome == ReplyOutcome.ERROR and result.wait_seconds == 30
    [run] = await runs(chat)
    assert (run.status, run.error) == (LLMRunStatus.ERROR, "Anthropic saturado")
    [message] = await chat.messages()
    assert message.processed_at is None


async def test_fallo_permanente_no_reintenta(chat: Chat) -> None:
    await chat.receive(upsert("M1"))
    await chat.age(16)
    result = await answer(chat, FakeLLM(fail=LLMError("clave inválida", permanent=True)))
    assert result.outcome == ReplyOutcome.FAILED
    [message] = await chat.messages()
    assert message.processed_at is not None


async def test_respuesta_vacia_no_se_envia(chat: Chat) -> None:
    await chat.receive(upsert("M1"))
    await chat.age(16)
    assert (await answer(chat, FakeLLM(text=""))).outcome == ReplyOutcome.FAILED
    assert len(await chat.messages()) == 1


async def test_si_el_dueno_ya_contesto_no_responde(chat: Chat) -> None:
    await chat.receive(upsert("M1", text="Hola"))
    await chat.receive(upsert("M2", text="Hola, ¿qué buscas?", from_me=True))
    await chat.age(16)
    llm = FakeLLM()
    assert (await answer(chat, llm)).outcome == ReplyOutcome.NOTHING
    assert llm.calls == []


async def test_historial_para_el_modelo(chat: Chat) -> None:
    await chat.receive(upsert("M0", text="Soy el dueño", from_me=True))  # antes del cliente
    await chat.receive(upsert("M1", text="Hola"))
    await chat.receive(upsert("M2", text="Hola, ¿qué buscas?", from_me=True))
    await chat.receive(upsert("M3", text="Pan dulce"))
    await chat.receive(
        upsert(
            "M4",
            message={"audioMessage": {"seconds": 3, "mimetype": "audio/ogg"}},
            messageType="audioMessage",
        )
    )
    conversation_id = await chat.conversation_id()
    async with tenant_session(chat.tenant_id, factory=chat.factory) as s:
        history = await build_history(s, conversation_id)
    assert [t["role"] for t in history] == ["user", "assistant", "user"]
    assert history[-1]["content"].startswith("Pan dulce\n[El cliente envió una nota de voz")


async def test_cliente_de_anthropic() -> None:
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-5-5",
                "content": [{"type": "text", "text": " Abrimos a las 7. "}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 50,
                    "output_tokens": 8,
                    "cache_read_input_tokens": 10,
                    "cache_creation_input_tokens": 0,
                },
            },
        )

    llm = AnthropicLLM(
        "sk-ant-prueba", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    reply = await llm.reply(
        model="claude-haiku-5-5",
        system="Instrucciones",
        messages=[{"role": "user", "content": "Hola"}],
    )
    await llm.aclose()
    assert (reply.text, reply.input_tokens, reply.output_tokens, reply.cached_tokens) == (
        "Abrimos a las 7.",
        50,
        8,
        10,
    )
    assert sent["model"] == "claude-haiku-5-5" and sent["system"] == "Instrucciones"
    assert sent["thinking"] == {"type": "disabled"}
    assert "temperature" not in sent  # Haiku 5.5 rechaza cambiar temperature


async def test_error_de_anthropic() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"type": "error", "error": {"type": "authentication_error", "message": "x"}}
        )

    llm = AnthropicLLM(
        "sk-ant-mala", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(LLMError) as info:
        await llm.reply(model="m", system="s", messages=[{"role": "user", "content": "Hola"}])
    await llm.aclose()
    assert info.value.permanent
