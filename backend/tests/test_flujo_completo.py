"""Prueba de punta a punta de la fase 3, con Redis y PostgreSQL de verdad.

Evolution (simulado con httpx) llama al webhook -> la API valida y guarda el aviso ->
lo encola en Redis -> el worker ARQ lo toma -> quedan el número conectado, el contacto,
la conversación y el mensaje. Solo Evolution es de mentira; todo lo demás es real.
"""

import uuid

import httpx
from arq.worker import Worker, func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.webhooks import get_queue, get_session_factory
from app.core.config import get_settings
from app.core.queue import (
    INGEST_QUEUE,
    PROCESS_WEBHOOK_EVENT,
    REPLY_TO_CONVERSATION,
    create_queue_pool,
)
from app.core.security import hash_secret, new_secret
from app.db.session import tenant_session
from app.main import create_app
from app.models import ChannelInstance, Contact, Conversation, Message, WebhookEvent
from app.models.enums import InstanceStatus, MessageDirection, WebhookEventStatus
from app.services.channel_registration import WEBHOOK_SECRET_HEADER
from app.workers import ingest
from tests.factories import create_instance, create_tenant


async def test_de_evolution_a_la_conversacion(
    owner_engine: AsyncEngine, app_engine, monkeypatch
) -> None:
    factory = async_sessionmaker(app_engine, expire_on_commit=False)
    secret = new_secret()
    async with AsyncSession(owner_engine) as s, s.begin():
        tenant = await create_tenant(s, "Completo")
        instance = await create_instance(s, tenant)
        instance.status = InstanceStatus.QR_PENDING
        instance.webhook_secret_hash = hash_secret(secret)
        tenant_id, instance_id, public_id = tenant.id, instance.id, instance.public_id

    # Cola propia de esta prueba: no se mezcla con un worker que esté corriendo.
    queue = create_queue_pool(
        get_settings().redis_url, queue_name=f"{INGEST_QUEUE}:test:{uuid.uuid4().hex[:6]}"
    )
    app = create_app()
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[get_queue] = lambda: queue

    url = f"/webhooks/evolution/{public_id}"
    headers = {WEBHOOK_SECRET_HEADER: secret}
    avisos = [
        {
            "event": "connection.update",
            "instance": "saint-x",
            "data": {"state": "open", "wuid": "584146090675@s.whatsapp.net"},
        },
        {
            "event": "messages.upsert",
            "instance": "saint-x",
            "data": {
                "key": {"remoteJid": "584121234567@s.whatsapp.net", "fromMe": False, "id": "E2E1"},
                "pushName": "María",
                "message": {"conversation": "Hola, ¿hacen delivery?"},
                "messageType": "conversation",
                "messageTimestamp": 1763244000,
            },
        },
    ]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for aviso in avisos:
            response = await client.post(url, json=aviso, headers=headers)
            assert response.json()["status"] == "stored"

    # La respuesta del bot (bloque 4.2) se prueba en test_bot_reply; aquí solo se mira
    # que el mensaje del cliente la programe, sin esperar los 15 s.
    monkeypatch.setattr(ingest, "DEBOUNCE_SECONDS", 0)
    replies: list[str] = []

    async def fake_reply(ctx, conversation_id: str, tenant_id: str) -> None:
        replies.append(conversation_id)

    worker = Worker(
        functions=[
            func(ingest.process_webhook_event, name=PROCESS_WEBHOOK_EVENT),
            func(fake_reply, name=REPLY_TO_CONVERSATION),
        ],
        redis_pool=queue,
        queue_name=queue.default_queue_name,
        burst=True,
        ctx={"factory": factory},
        handle_signals=False,
        poll_delay=0.05,
        max_jobs=1,  # en orden, como llegaron
    )
    await worker.async_run()
    assert (worker.jobs_complete, worker.jobs_failed) == (3, 0)
    await queue.delete(worker.health_check_key)
    await queue.aclose()

    async with tenant_session(tenant_id, factory=factory) as s:
        number = await s.get(ChannelInstance, instance_id)
        assert number.status == InstanceStatus.OPEN
        assert number.display_identifier == "+584146090675"

        [contact] = await s.scalars(select(Contact).where(Contact.tenant_id == tenant_id))
        assert (contact.phone_e164, contact.display_name) == ("+584121234567", "María")
        [conversation] = await s.scalars(
            select(Conversation).where(Conversation.channel_instance_id == instance_id)
        )
        [message] = await s.scalars(
            select(Message).where(Message.conversation_id == conversation.id)
        )
        assert message.direction == MessageDirection.INBOUND
        assert message.text == "Hola, ¿hacen delivery?"
        assert replies == [str(conversation.id)]

        statuses = await s.scalars(
            select(WebhookEvent.status).where(WebhookEvent.channel_instance_id == instance_id)
        )
        assert set(statuses) == {WebhookEventStatus.PROCESSED}
