"""Consumo del negocio (bloque 6.10): contadores diarios, reporte y GET /usage."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.auth import get_redis
from app.api.webhooks import get_session_factory
from app.core.config import get_settings
from app.db.session import tenant_session
from app.main import create_app
from app.models import LLMRun, UsageDaily, WebhookEvent
from app.models.enums import LLMRunStatus, MembershipRole
from app.services import usage
from app.services.bot_reply import ReplyOutcome, answer_conversation
from app.services.members import Member, PermissionDeniedError
from app.services.webhook_processing import process_event
from tests import test_bot_reply
from tests.test_ai_reply import FakeLLM
from tests.test_auth import SECRET, bearer, create_user, login, new_tenant
from tests.test_bot_reply import Chat
from tests.test_message_ingest import upsert
from tests.test_outbound import JID, FakeEvolution, new_number, queue, send

CARACAS = ZoneInfo("America/Caracas")  # UTC-4: la zona por defecto de un negocio

# Las mismas fixtures que test_bot_reply: un negocio con un número y un chat.
factory = test_bot_reply.factory
chat = test_bot_reply.chat


@pytest.fixture(autouse=True)
def jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "jwt_secret", SecretStr(SECRET))


async def rows_of(owner_engine: AsyncEngine, tenant_id: uuid.UUID) -> dict[date, UsageDaily]:
    async with AsyncSession(owner_engine) as s:
        rows = await s.scalars(select(UsageDaily).where(UsageDaily.tenant_id == tenant_id))
        return {row.day: row for row in rows}


async def tokens_of(owner_engine: AsyncEngine, tenant_id: uuid.UUID) -> tuple[int, int]:
    """Tokens de entrada y salida sumando todos los días del negocio."""
    rows = await rows_of(owner_engine, tenant_id)
    return (
        sum(r.llm_input_tokens for r in rows.values()),
        sum(r.llm_output_tokens for r in rows.values()),
    )


async def only_row(owner_engine: AsyncEngine, tenant_id: uuid.UUID) -> UsageDaily:
    rows = await rows_of(owner_engine, tenant_id)
    assert len(rows) == 1, rows
    return next(iter(rows.values()))


# --- Contar ---


async def test_suma_por_dia_en_la_zona_del_negocio(chat: Chat, owner_engine) -> None:
    async with tenant_session(chat.tenant_id, factory=chat.factory) as s:
        # 02:30 UTC = 22:30 del día 15 en Caracas; 05:00 UTC = 01:00 del día 16.
        late = datetime(2026, 11, 16, 2, 30, tzinfo=UTC)
        early = datetime(2026, 11, 16, 5, 0, tzinfo=UTC)
        await usage.record(s, chat.tenant_id, at=late, inbound_messages=1)
        await usage.record(s, chat.tenant_id, at=late, inbound_messages=2, conversations=1)
        await usage.record(s, chat.tenant_id, at=early, outbound_messages=1)

    rows = await rows_of(owner_engine, chat.tenant_id)
    assert set(rows) == {date(2026, 11, 15), date(2026, 11, 16)}
    assert (rows[date(2026, 11, 15)].inbound_messages, rows[date(2026, 11, 15)].conversations) == (
        3,
        1,
    )
    assert rows[date(2026, 11, 16)].outbound_messages == 1
    assert rows[date(2026, 11, 16)].inbound_messages == 0


async def test_cada_negocio_tiene_sus_contadores(chat: Chat, owner_engine) -> None:
    other = await new_number(owner_engine)
    async with tenant_session(chat.tenant_id, factory=chat.factory) as s:
        await usage.record(s, chat.tenant_id, inbound_messages=1)
    async with tenant_session(other[0], factory=chat.factory) as s:
        await usage.record(s, other[0], inbound_messages=5)
    assert (await only_row(owner_engine, chat.tenant_id)).inbound_messages == 1
    assert (await only_row(owner_engine, other[0])).inbound_messages == 5


async def test_sin_nada_que_sumar_no_escribe(chat: Chat, owner_engine) -> None:
    async with tenant_session(chat.tenant_id, factory=chat.factory) as s:
        await usage.record(s, chat.tenant_id)
    assert await rows_of(owner_engine, chat.tenant_id) == {}


async def test_un_fallo_al_contar_no_rompe_lo_que_se_hacia(chat: Chat, owner_engine) -> None:
    async with tenant_session(chat.tenant_id, factory=chat.factory) as s:
        # Otro negocio: la política de RLS rechaza la fila. No debe lanzar ni dejar la sesión rota.
        await usage.record(s, uuid.uuid4(), inbound_messages=1)
        await usage.record(s, chat.tenant_id, inbound_messages=1)
    assert (await only_row(owner_engine, chat.tenant_id)).inbound_messages == 1


async def test_mensajes_y_conversaciones_se_cuentan_al_recibir(chat: Chat, owner_engine) -> None:
    await chat.receive(upsert("M1", text="Hola"))
    await chat.receive(upsert("M2", text="¿tienen pan?"))
    row = await only_row(owner_engine, chat.tenant_id)
    # Se cuenta en el día en que escribió el cliente (1763240000 = 15 nov 2025, 17:33 en Caracas).
    assert row.day == date(2025, 11, 15)
    assert (row.conversations, row.inbound_messages, row.outbound_messages) == (1, 2, 0)

    # El dueño contesta desde su teléfono: es un mensaje saliente, en la misma conversación.
    await chat.receive(upsert("M3", from_me=True, text="Claro que sí"))
    row = await only_row(owner_engine, chat.tenant_id)
    assert (row.conversations, row.inbound_messages, row.outbound_messages) == (1, 2, 1)


async def test_un_mensaje_repetido_no_se_cuenta_dos_veces(chat: Chat, owner_engine) -> None:
    await chat.receive(upsert("M1"))
    # Evolution manda el mismo mensaje por otro tipo de aviso: se guarda una sola vez.
    await chat.receive(upsert("M1"), event_type="send.message")
    assert (await only_row(owner_engine, chat.tenant_id)).inbound_messages == 1


async def test_el_envio_de_saint_se_cuenta_una_vez(owner_engine, factory) -> None:
    tenant_id, instance_id, _ = await new_number(owner_engine)
    message_id = await queue(factory, tenant_id, instance_id)
    assert await rows_of(owner_engine, tenant_id) == {}  # en cola todavía no cuenta

    fake = FakeEvolution()
    await send(fake, factory, tenant_id, message_id)
    await send(fake, factory, tenant_id, message_id)  # el barrido no lo reenvía ni lo recuenta
    row = await only_row(owner_engine, tenant_id)
    assert row.outbound_messages == 1


async def test_un_envio_que_fallo_no_se_cuenta(owner_engine, factory) -> None:
    tenant_id, instance_id, _ = await new_number(owner_engine)
    message_id = await queue(factory, tenant_id, instance_id)
    await send(FakeEvolution(fail=400), factory, tenant_id, message_id)
    assert await rows_of(owner_engine, tenant_id) == {}


async def test_envio_sin_respuesta_se_cuenta_al_confirmarlo_el_aviso(owner_engine, factory) -> None:
    tenant_id, instance_id, _ = await new_number(owner_engine)
    message_id = await queue(factory, tenant_id, instance_id, "Abrimos a las 7")
    await send(FakeEvolution(fail=httpx.ReadTimeout("lento")), factory, tenant_id, message_id)
    assert await rows_of(owner_engine, tenant_id) == {}  # no sabemos si salió

    data = {
        "key": {"remoteJid": JID, "fromMe": True, "id": "3EB0TARDE"},
        "message": {"conversation": "Abrimos a las 7"},
        "messageType": "conversation",
        "messageTimestamp": 1763250000,
    }
    async with AsyncSession(owner_engine) as s, s.begin():
        event = WebhookEvent(
            tenant_id=tenant_id,
            channel_instance_id=instance_id,
            event_type="send.message",
            external_id="3EB0TARDE",
            payload={"event": "send.message", "data": data},
        )
        s.add(event)
        await s.flush()
        event_id = event.id
    await process_event(event_id, tenant_id, factory=factory)
    assert (await only_row(owner_engine, tenant_id)).outbound_messages == 1


async def test_los_tokens_de_la_ia_se_cuentan_tambien_si_se_descarta(
    chat: Chat, owner_engine
) -> None:
    await chat.receive(upsert("M1", text="Hola"))
    await chat.age(16)

    async def new_message():
        await chat.receive(upsert("M2", text="ah, y otra cosa"))

    conversation_id = await chat.conversation_id()
    discarded = await answer_conversation(
        conversation_id, chat.tenant_id, llm=FakeLLM(during=new_message), factory=chat.factory
    )
    assert discarded.outcome == ReplyOutcome.WAIT
    assert await tokens_of(owner_engine, chat.tenant_id) == (120, 15)  # se pagaron igual

    await chat.age(16)
    replied = await answer_conversation(
        conversation_id, chat.tenant_id, llm=FakeLLM(), factory=chat.factory
    )
    assert replied.outcome == ReplyOutcome.REPLIED
    assert await tokens_of(owner_engine, chat.tenant_id) == (240, 30)


# --- Reporte ---


def owner_of(tenant_id: uuid.UUID, role: MembershipRole = MembershipRole.OWNER) -> Member:
    return Member(uuid.uuid4(), tenant_id, role)


async def seed(owner_engine: AsyncEngine, tenant_id: uuid.UUID, days: dict[date, dict]) -> None:
    async with AsyncSession(owner_engine) as s, s.begin():
        for day, values in days.items():
            s.add(UsageDaily(tenant_id=tenant_id, day=day, **values))


def test_periodos() -> None:
    today = date(2026, 3, 1)
    assert usage.period_range("this_month", today) == (date(2026, 3, 1), date(2026, 3, 1))
    assert usage.period_range("last_month", today) == (date(2026, 2, 1), date(2026, 2, 28))
    assert usage.period_range("last_7_days", today) == (date(2026, 2, 23), date(2026, 3, 1))
    assert usage.period_range("last_30_days", date(2026, 1, 15)) == (
        date(2025, 12, 17),
        date(2026, 1, 15),
    )
    with pytest.raises(ValueError):
        usage.period_range("ayer", today)


async def test_el_reporte_rellena_los_dias_sin_datos(chat: Chat, owner_engine) -> None:
    await seed(
        owner_engine,
        chat.tenant_id,
        {
            date(2026, 11, 3): {"conversations": 2, "inbound_messages": 10, "outbound_messages": 8},
            date(2026, 11, 16): {"llm_input_tokens": 1500, "llm_output_tokens": 300},
            date(2026, 10, 20): {"inbound_messages": 99},
        },
    )
    now = datetime(2026, 11, 16, 18, 0, tzinfo=UTC)  # 14:00 del 16 en Caracas
    report = await usage.report(
        owner_of(chat.tenant_id), "this_month", now=now, factory=chat.factory
    )

    assert (report.start, report.end, report.timezone) == (
        date(2026, 11, 1),
        date(2026, 11, 16),
        "America/Caracas",
    )
    assert len(report.days) == 16 and report.days[0].day == date(2026, 11, 1)
    by_day = {d.day: d for d in report.days}
    assert by_day[date(2026, 11, 3)].inbound_messages == 10
    assert by_day[date(2026, 11, 4)].inbound_messages == 0  # día sin datos: ceros
    assert (by_day[date(2026, 11, 16)].input_tokens, by_day[date(2026, 11, 16)].output_tokens) == (
        1500,
        300,
    )
    assert report.tracking_since == date(2026, 10, 20)  # el primer día con datos del negocio

    last = await usage.report(owner_of(chat.tenant_id), "last_month", now=now, factory=chat.factory)
    assert (last.start, last.end) == (date(2026, 10, 1), date(2026, 10, 31))
    assert {d.day: d.inbound_messages for d in last.days}[date(2026, 10, 20)] == 99


async def test_el_hoy_depende_de_la_zona_del_negocio(chat: Chat) -> None:
    # 02:00 UTC del 17 todavía es el 16 en Caracas (UTC-4).
    now = datetime(2026, 11, 17, 2, 0, tzinfo=UTC)
    report = await usage.report(
        owner_of(chat.tenant_id), "last_7_days", now=now, factory=chat.factory
    )
    assert report.end == date(2026, 11, 16) and report.start == date(2026, 11, 10)


async def test_respuestas_de_ia_por_dia_local(chat: Chat, owner_engine) -> None:
    def run(at: datetime, status: LLMRunStatus = LLMRunStatus.OK) -> LLMRun:
        return LLMRun(tenant_id=chat.tenant_id, model="m", status=status, created_at=at)

    async with AsyncSession(owner_engine) as s, s.begin():
        s.add_all(
            [
                # 03:00 UTC del 16 = 23:00 del 15 en Caracas.
                run(datetime(2026, 11, 16, 3, 0, tzinfo=UTC)),
                run(datetime(2026, 11, 16, 15, 0, tzinfo=UTC)),
                run(datetime(2026, 11, 16, 16, 0, tzinfo=UTC)),
                run(datetime(2026, 11, 16, 17, 0, tzinfo=UTC), LLMRunStatus.DISCARDED),
                run(datetime(2026, 11, 16, 17, 30, tzinfo=UTC), LLMRunStatus.ERROR),
                run(datetime(2026, 11, 10, 3, 59, tzinfo=UTC)),  # 23:59 del 9 en Caracas
                run(datetime(2026, 11, 1, 3, 0, tzinfo=UTC)),  # 23:00 del 31 de octubre: fuera
            ]
        )
    now = datetime(2026, 11, 16, 18, 0, tzinfo=UTC)
    report = await usage.report(
        owner_of(chat.tenant_id), "this_month", now=now, factory=chat.factory
    )
    by_day = {d.day: d.ai_replies for d in report.days}
    assert by_day[date(2026, 11, 15)] == 1
    assert by_day[date(2026, 11, 16)] == 2  # solo las de estado ok
    assert by_day[date(2026, 11, 9)] == 1  # el día local, no el de UTC
    assert sum(by_day.values()) == 4  # la de octubre no cuenta


async def test_un_agente_no_ve_el_consumo(chat: Chat) -> None:
    with pytest.raises(PermissionDeniedError):
        await usage.report(owner_of(chat.tenant_id, MembershipRole.AGENT), factory=chat.factory)


# --- Ruta ---


@pytest.fixture
async def client(app_engine: AsyncEngine) -> AsyncIterator[httpx.AsyncClient]:
    app_factory = async_sessionmaker(app_engine, expire_on_commit=False)
    redis = Redis.from_url(get_settings().redis_url)
    app = create_app()
    app.dependency_overrides[get_session_factory] = lambda: app_factory
    app.dependency_overrides[get_redis] = lambda: redis
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://panel.test") as c:
        yield c
    keys = [k async for k in redis.scan_iter("saint:login-fail:*")]
    if keys:
        await redis.delete(*keys)
    await redis.aclose()


async def test_ruta_del_consumo(client, owner_engine, monkeypatch) -> None:
    tenant = await new_tenant(owner_engine, "Consumo")
    person = await create_user(owner_engine, (tenant, MembershipRole.OWNER))
    today = datetime.now(CARACAS).date()
    await seed(
        owner_engine,
        tenant.id,
        {
            today: {
                "conversations": 3,
                "inbound_messages": 20,
                "llm_input_tokens": 1_000_000,
                "llm_output_tokens": 500_000,
            }
        },
    )
    headers = bearer(await login(client, person.email))
    monkeypatch.setattr(get_settings(), "llm_input_usd_per_million", None)
    monkeypatch.setattr(get_settings(), "llm_output_usd_per_million", None)

    response = await client.get("/usage", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["period"] == "this_month" and body["timezone"] == "America/Caracas"
    assert body["totals"]["inbound_messages"] == 20 and body["totals"]["conversations"] == 3
    assert body["totals"]["input_tokens"] == 1_000_000
    assert body["days"][-1]["day"] == today.isoformat()
    assert body["tracking_since"] == today.isoformat()
    # Sin precios en el .env no se inventa un costo.
    assert body["pricing"]["configured"] is False and body["estimated_cost_usd"] is None

    monkeypatch.setattr(get_settings(), "llm_input_usd_per_million", 1.0)
    monkeypatch.setattr(get_settings(), "llm_output_usd_per_million", 5.0)
    priced = (await client.get("/usage?period=last_30_days", headers=headers)).json()
    assert priced["pricing"] == {
        "configured": True,
        "input_usd_per_million": 1.0,
        "output_usd_per_million": 5.0,
    }
    assert priced["estimated_cost_usd"] == 3.5  # 1 M entrada x 1 + 0,5 M salida x 5
    assert len(priced["days"]) == 30


async def test_la_ruta_valida_rol_periodo_y_sesion(client, owner_engine) -> None:
    tenant = await new_tenant(owner_engine, "Permisos")
    owner = await create_user(owner_engine, (tenant, MembershipRole.OWNER))
    agent = await create_user(owner_engine, (tenant, MembershipRole.AGENT))

    assert (await client.get("/usage")).status_code == 401
    agent_headers = bearer(await login(client, agent.email))
    denied = await client.get("/usage", headers=agent_headers)
    assert denied.status_code == 403 and "consumo" in denied.json()["detail"]

    owner_headers = bearer(await login(client, owner.email))
    assert (await client.get("/usage?period=ayer", headers=owner_headers)).status_code == 422
    empty = await client.get("/usage?period=last_7_days", headers=owner_headers)
    assert empty.status_code == 200
    body = empty.json()
    assert body["tracking_since"] is None and len(body["days"]) == 7
    assert all(d["inbound_messages"] == 0 for d in body["days"])
