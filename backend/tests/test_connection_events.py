"""connection.update -> estado del número (con el rol de la app y RLS)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.session import tenant_session
from app.models import ChannelInstance, WebhookEvent
from app.models.enums import InstanceStatus, WebhookEventStatus
from app.services.webhook_processing import Outcome, process_event
from tests.factories import create_instance, create_tenant

NOW = datetime(2026, 11, 15, 22, 0, tzinfo=UTC)


@pytest.fixture
def factory(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(app_engine, expire_on_commit=False)


async def new_number(
    owner_engine: AsyncEngine, status: InstanceStatus = InstanceStatus.OPEN
) -> tuple[uuid.UUID, uuid.UUID]:
    async with AsyncSession(owner_engine) as s, s.begin():
        tenant = await create_tenant(s, "Conexion")
        instance = await create_instance(s, tenant)
        instance.status = status
        ids = (tenant.id, instance.id)
    return ids


async def connection_update(
    owner_engine, factory, ids, data: dict, received_at: datetime = NOW
) -> ChannelInstance:
    tenant_id, instance_id = ids
    async with AsyncSession(owner_engine) as s, s.begin():
        event = WebhookEvent(
            tenant_id=tenant_id,
            channel_instance_id=instance_id,
            event_type="connection.update",
            payload={"event": "connection.update", "instance": "saint-x", "data": data},
        )
        s.add(event)
        await s.flush()
        await s.execute(
            update(WebhookEvent).where(WebhookEvent.id == event.id).values(received_at=received_at)
        )
        event_id = event.id
    assert (await process_event(event_id, tenant_id, factory=factory)).outcome == Outcome.PROCESSED
    async with tenant_session(tenant_id, factory=factory) as s:
        assert (await s.get(WebhookEvent, event_id)).status == WebhookEventStatus.PROCESSED
        return await s.get(ChannelInstance, instance_id)


async def test_conectado(owner_engine, factory) -> None:
    ids = await new_number(owner_engine, InstanceStatus.QR_PENDING)
    data = {
        "instance": "saint-x",
        "wuid": "584146090675@s.whatsapp.net",
        "profileName": "Panadería",
        "state": "open",
        "statusReason": 200,
    }
    instance = await connection_update(owner_engine, factory, ids, data)
    assert instance.status == InstanceStatus.OPEN
    assert instance.display_identifier == "+584146090675"
    assert instance.last_connected_at == NOW
    assert instance.status_changed_at == NOW


async def test_conectando_mientras_espera_qr(owner_engine, factory) -> None:
    ids = await new_number(owner_engine, InstanceStatus.QR_PENDING)
    data = {"instance": "saint-x", "state": "connecting", "statusReason": 200}
    instance = await connection_update(owner_engine, factory, ids, data)
    assert instance.status == InstanceStatus.QR_PENDING


async def test_desconectado_y_bloqueado(owner_engine, factory) -> None:
    ids = await new_number(owner_engine)
    closed = {"instance": "saint-x", "state": "close", "statusReason": 401}
    instance = await connection_update(owner_engine, factory, ids, closed)
    assert instance.status == InstanceStatus.CLOSE

    banned = {"instance": "saint-x", "state": "close", "statusReason": 403}
    later = NOW + timedelta(minutes=1)
    instance = await connection_update(owner_engine, factory, ids, banned, later)
    assert instance.status == InstanceStatus.BANNED


async def test_aviso_atrasado_no_pisa(owner_engine, factory) -> None:
    ids = await new_number(owner_engine, InstanceStatus.QR_PENDING)
    opened = {"state": "open", "wuid": "584146090675@s.whatsapp.net"}
    await connection_update(owner_engine, factory, ids, opened, NOW)
    # Un "connecting" de un minuto antes que se procesó tarde (por un reintento).
    old = {"state": "connecting"}
    instance = await connection_update(owner_engine, factory, ids, old, NOW - timedelta(minutes=1))
    assert instance.status == InstanceStatus.OPEN


async def test_estado_desconocido(owner_engine, factory) -> None:
    ids = await new_number(owner_engine)
    instance = await connection_update(owner_engine, factory, ids, {"state": "raro"})
    assert instance.status == InstanceStatus.OPEN
    assert instance.status_changed_at is None
