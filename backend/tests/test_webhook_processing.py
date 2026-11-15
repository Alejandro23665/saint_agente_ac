"""Cola ingest: procesar avisos, reintentos, barrido y el worker ARQ real."""

import uuid
from datetime import timedelta

import pytest
from arq import Retry
from arq.worker import Worker, func
from sqlalchemy import func as sql_func
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.queue import INGEST_QUEUE, PROCESS_WEBHOOK_EVENT, create_queue_pool
from app.db.session import tenant_session
from app.models import Contact, WebhookEvent
from app.models.enums import Channel, WebhookEventStatus
from app.services import webhook_processing
from app.services.webhook_processing import (
    MAX_ATTEMPTS,
    Outcome,
    find_stuck_events,
    process_event,
)
from app.workers import ingest
from tests.factories import create_instance, create_tenant


@pytest.fixture
def factory(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(app_engine, expire_on_commit=False)


async def new_event(
    owner_engine: AsyncEngine,
    event_type: str = "test.event",
    minutes_old: int = 0,
    status: WebhookEventStatus = WebhookEventStatus.PENDING,
) -> tuple[uuid.UUID, uuid.UUID]:
    async with AsyncSession(owner_engine) as s, s.begin():
        tenant = await create_tenant(s, "Cola")
        instance = await create_instance(s, tenant)
        event = WebhookEvent(
            tenant_id=tenant.id,
            channel_instance_id=instance.id,
            event_type=event_type,
            payload={"event": event_type},
            status=status,
        )
        s.add(event)
        await s.flush()
        if minutes_old:
            await s.execute(
                update(WebhookEvent)
                .where(WebhookEvent.id == event.id)
                .values(received_at=sql_func.now() - timedelta(minutes=minutes_old))
            )
        ids = (event.id, tenant.id)
    return ids


async def load(event_id, tenant_id, factory) -> WebhookEvent:
    async with tenant_session(tenant_id, factory=factory) as s:
        return await s.get(WebhookEvent, event_id)


async def ok(session, event) -> None:
    pass


async def boom(session, event) -> None:
    # Escribe algo y luego falla: lo escrito debe deshacerse.
    session.add(
        Contact(
            tenant_id=event.tenant_id,
            channel=Channel.WHATSAPP,
            external_id=f"x-{uuid.uuid4().hex}",
            display_name="no debe quedar",
        )
    )
    await session.flush()
    raise RuntimeError("algo salió mal")


async def test_procesa_y_marca(owner_engine, factory) -> None:
    event_id, tenant_id = await new_event(owner_engine)
    result = await process_event(event_id, tenant_id, {"test.event": ok}, factory)
    assert result.outcome == Outcome.PROCESSED
    event = await load(event_id, tenant_id, factory)
    assert event.status == WebhookEventStatus.PROCESSED
    assert event.attempts == 1 and event.processed_at is not None and event.error is None


async def test_sin_manejador_sigue_pendiente(owner_engine, factory) -> None:
    event_id, tenant_id = await new_event(owner_engine, "messages.upsert")
    result = await process_event(event_id, tenant_id, {}, factory)
    assert result.outcome == Outcome.NO_HANDLER
    event = await load(event_id, tenant_id, factory)
    assert event.status == WebhookEventStatus.PENDING and event.attempts == 0


async def test_ya_procesado_se_salta(owner_engine, factory) -> None:
    event_id, tenant_id = await new_event(owner_engine, status=WebhookEventStatus.PROCESSED)
    result = await process_event(event_id, tenant_id, {"test.event": ok}, factory)
    assert result.outcome == Outcome.SKIPPED


async def test_otro_negocio_no_lo_ve(owner_engine, factory) -> None:
    event_id, _ = await new_event(owner_engine)
    _, other_tenant = await new_event(owner_engine)
    result = await process_event(event_id, other_tenant, {"test.event": ok}, factory)
    assert result.outcome == Outcome.SKIPPED


async def test_fallo_reintenta_y_luego_failed(owner_engine, factory) -> None:
    event_id, tenant_id = await new_event(owner_engine)
    handlers = {"test.event": boom}
    for attempt in range(1, MAX_ATTEMPTS):
        result = await process_event(event_id, tenant_id, handlers, factory)
        assert (result.outcome, result.attempts) == (Outcome.RETRY, attempt)
    result = await process_event(event_id, tenant_id, handlers, factory)
    assert result.outcome == Outcome.FAILED

    event = await load(event_id, tenant_id, factory)
    assert event.status == WebhookEventStatus.FAILED
    assert event.attempts == MAX_ATTEMPTS
    assert event.error == "RuntimeError: algo salió mal"
    # Lo que escribió el manejador antes de fallar se deshizo.
    async with tenant_session(tenant_id, factory=factory) as s:
        assert await s.scalar(select(Contact).where(Contact.tenant_id == tenant_id)) is None


async def test_barrido_encuentra_los_atascados(owner_engine, factory) -> None:
    old_id, old_tenant = await new_event(owner_engine, "test.sweep", minutes_old=5)
    new_id, _ = await new_event(owner_engine, "test.sweep")
    other_id, _ = await new_event(owner_engine, "otro.tipo", minutes_old=5)
    done_id, _ = await new_event(
        owner_engine, "test.sweep", minutes_old=5, status=WebhookEventStatus.PROCESSED
    )
    found = await find_stuck_events(handlers={"test.sweep": ok}, factory=factory)
    assert (old_id, old_tenant) in found
    assert {new_id, other_id, done_id}.isdisjoint(event_id for event_id, _ in found)


async def test_tarea_del_worker_pide_reintento(owner_engine, factory, monkeypatch) -> None:
    event_id, tenant_id = await new_event(owner_engine, "test.retry")
    monkeypatch.setitem(webhook_processing.HANDLERS, "test.retry", boom)
    with pytest.raises(Retry) as info:
        await ingest.process_webhook_event({"factory": factory}, str(event_id), str(tenant_id))
    assert info.value.defer_score == ingest.RETRY_BASE_SECONDS * 1000  # 10 s, en ms


async def test_worker_arq_real(owner_engine, factory, monkeypatch) -> None:
    """De punta a punta con Redis de verdad: encolar -> el worker ARQ lo procesa."""
    event_id, tenant_id = await new_event(owner_engine, "test.e2e")
    monkeypatch.setitem(webhook_processing.HANDLERS, "test.e2e", ok)

    queue_name = f"{INGEST_QUEUE}:test:{uuid.uuid4().hex[:6]}"
    pool = create_queue_pool(get_settings().redis_url)
    await pool.enqueue_job(
        PROCESS_WEBHOOK_EVENT, str(event_id), str(tenant_id), _queue_name=queue_name
    )
    worker = Worker(
        functions=[func(ingest.process_webhook_event, name=PROCESS_WEBHOOK_EVENT)],
        redis_pool=pool,
        queue_name=queue_name,
        burst=True,  # procesa lo que hay en la cola y termina
        ctx={"factory": factory},
        handle_signals=False,
        poll_delay=0.05,
    )
    await worker.async_run()
    assert (worker.jobs_complete, worker.jobs_failed) == (1, 0)
    # No usamos worker.close(): con handle_signals=False manda SIGUSR1, que no existe
    # en Windows. En modo burst ya no queda nada en marcha; solo cerramos la conexión.
    await pool.delete(worker.health_check_key)
    await pool.aclose()

    event = await load(event_id, tenant_id, factory)
    assert event.status == WebhookEventStatus.PROCESSED
