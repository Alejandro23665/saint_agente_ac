"""El agente de IA que contesta en una conversación y lo que se le manda al modelo.

Cada negocio tiene sus agentes en la tabla agents (instrucciones, modelo...). Si un
negocio todavía no configuró ninguno, se le crea uno básico de atención al cliente.
Más adelante (4.4-4.5) el agente también recibirá el conocimiento del negocio.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agent, Conversation, Message, Tenant
from app.models.enums import (
    AgentKind,
    ContentType,
    MessageDirection,
    MessageStatus,
)

DEFAULT_AGENT_KEY = "atencion"
DEFAULT_MODEL = "claude-haiku-5-5"
# Mensajes anteriores que ve el modelo: suficiente para seguir el hilo, sin gastar de más.
HISTORY_LIMIT = 30

DEFAULT_PROMPT = """\
Eres el asistente de WhatsApp de {business}. Atiendes a sus clientes en español.

Cómo escribir:
- Respuestas cortas y amables, como en un chat: una o dos frases, sin listas largas.
- Sin formato Markdown (nada de **negritas** ni títulos): WhatsApp no lo muestra igual.
- Si el cliente mandó varios mensajes seguidos, contesta todo en una sola respuesta.

Qué no hacer:
- No inventes precios, horarios, productos, direcciones ni promociones. Todavía no
  tienes información del negocio: si te preguntan algo así, di con naturalidad que lo
  vas a consultar y que una persona del equipo le confirmará.
- No prometas cosas que el negocio no ha dicho (descuentos, entregas, reservas).
"""

# Lo que no es texto, contado al modelo con palabras.
MEDIA_LABELS: dict[ContentType, str] = {
    ContentType.AUDIO: "[El cliente envió una nota de voz que todavía no se puede escuchar]",
    ContentType.IMAGE: "[El cliente envió una foto]",
    ContentType.VIDEO: "[El cliente envió un video]",
    ContentType.DOCUMENT: "[El cliente envió un documento]",
    ContentType.STICKER: "[El cliente envió un sticker]",
    ContentType.LOCATION: "[El cliente envió una ubicación]",
}


async def get_agent(session: AsyncSession, conversation: Conversation) -> Agent:
    """El agente de la conversación; si no tiene, el básico del negocio (se crea si falta)."""
    if conversation.active_agent_id is not None:
        agent = await session.get(Agent, conversation.active_agent_id)
        if agent is not None and agent.is_active:
            return agent
    agent = await session.scalar(
        select(Agent).where(
            Agent.tenant_id == conversation.tenant_id, Agent.key == DEFAULT_AGENT_KEY
        )
    )
    if agent is None:
        tenant = await session.get(Tenant, conversation.tenant_id)
        agent = Agent(
            tenant_id=conversation.tenant_id,
            key=DEFAULT_AGENT_KEY,
            name="Atención al cliente",
            kind=AgentKind.SPECIALIST,
            system_prompt=DEFAULT_PROMPT.format(business=tenant.name),
            model=DEFAULT_MODEL,
        )
        session.add(agent)
        await session.flush()
    return agent


def message_content(message: Message) -> str:
    """El mensaje como texto para el modelo."""
    body = message.transcript or message.text
    label = MEDIA_LABELS.get(message.content_type)
    if message.direction == MessageDirection.OUTBOUND:
        return body or "[mensaje sin texto]"
    if body and label and message.content_type != ContentType.AUDIO:
        return f"{label} {body}"
    return body or label or "[mensaje]"


async def build_history(session: AsyncSession, conversation_id: uuid.UUID) -> list[dict[str, Any]]:
    """Los últimos mensajes en el formato de la API de Claude.

    El cliente es "user" y lo que salió del negocio (bot o persona) es "assistant".
    Mensajes seguidos del mismo lado se juntan en uno, y la conversación tiene que
    empezar por el cliente.
    """
    rows = list(
        await session.scalars(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.status != MessageStatus.FAILED,
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(HISTORY_LIMIT)
        )
    )
    rows.reverse()
    turns: list[dict[str, Any]] = []
    for message in rows:
        role = "user" if message.direction == MessageDirection.INBOUND else "assistant"
        text = message_content(message)
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] += "\n" + text
        else:
            turns.append({"role": role, "content": text})
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    return turns
