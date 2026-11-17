"""Consumo del negocio: contadores diarios (usage_daily) y el reporte para el panel (bloque 6.10).

Cada día se cuenta en la zona horaria del negocio (la que tenga cuando ocurre). Los contadores
se suman con INSERT ... ON CONFLICT DO UPDATE, así que varios workers pueden sumar a la vez.

Qué se cuenta:
- conversations: conversaciones nuevas.
- inbound_messages: mensajes que llegaron de clientes.
- outbound_messages: mensajes que salieron (los de Saint cuando WhatsApp los acepta, y los que
  el dueño escribe desde su teléfono).
- llm_input_tokens / llm_output_tokens: lo que gastó cada llamada a la IA, también las
  descartadas (se pagan igual).
- audio_seconds: todavía no se cuenta (llega con las notas de voz, bloque 4.7).

Las respuestas de IA del reporte salen de llm_runs (las de estado 'ok'). Solo hay datos desde
que se instaló este bloque: el pasado no se rellena.

Contar nunca debe romper lo que se está haciendo: si la suma falla, se registra el error y el
mensaje o la respuesta siguen su camino.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Date, cast, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import SessionFactory, tenant_session
from app.models import LLMRun, Tenant, UsageDaily
from app.models.enums import LLMRunStatus
from app.services.members import Member, PermissionDeniedError, can_edit_tenant

logger = logging.getLogger(__name__)

Factory = async_sessionmaker[AsyncSession]

PERIODS = ("this_month", "last_month", "last_7_days", "last_30_days")
COUNTERS = (
    "conversations",
    "inbound_messages",
    "outbound_messages",
    "audio_seconds",
    "llm_input_tokens",
    "llm_output_tokens",
)


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


async def record(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    at: datetime | None = None,
    conversations: int = 0,
    inbound_messages: int = 0,
    outbound_messages: int = 0,
    audio_seconds: int = 0,
    llm_input_tokens: int = 0,
    llm_output_tokens: int = 0,
) -> None:
    """Suma al contador del día (en la zona del negocio) en que ocurrió algo.

    Va dentro de un savepoint y nunca lanza: un fallo aquí no debe deshacer el mensaje.
    """
    deltas = {
        "conversations": conversations,
        "inbound_messages": inbound_messages,
        "outbound_messages": outbound_messages,
        "audio_seconds": audio_seconds,
        "llm_input_tokens": llm_input_tokens,
        "llm_output_tokens": llm_output_tokens,
    }
    if not any(deltas.values()):
        return
    try:
        async with session.begin_nested():
            timezone = await session.scalar(select(Tenant.timezone).where(Tenant.id == tenant_id))
            day = (at or datetime.now(UTC)).astimezone(_zone(timezone)).date()
            stmt = insert(UsageDaily).values(tenant_id=tenant_id, day=day, **deltas)
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["tenant_id", "day"],
                    set_={
                        **{k: getattr(UsageDaily, k) + stmt.excluded[k] for k in COUNTERS},
                        "updated_at": func.now(),
                    },
                )
            )
    except Exception:
        logger.exception("No se pudo sumar el consumo del negocio %s", tenant_id)


# --- Reporte ---


@dataclass(frozen=True)
class DayUsage:
    day: date
    conversations: int = 0
    inbound_messages: int = 0
    outbound_messages: int = 0
    ai_replies: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class UsageReport:
    period: str
    start: date
    end: date
    timezone: str
    days: list[DayUsage]
    tracking_since: date | None  # primer día con datos: antes de esa fecha no se registraba


def period_range(period: str, today: date) -> tuple[date, date]:
    """Primer y último día (incluidos) de un periodo, contando 'hoy' en la zona del negocio."""
    if period == "this_month":
        return today.replace(day=1), today
    if period == "last_month":
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last
    if period == "last_7_days":
        return today - timedelta(days=6), today
    if period == "last_30_days":
        return today - timedelta(days=29), today
    raise ValueError(f"Periodo desconocido: {period}")


async def report(
    member: Member,
    period: str = "this_month",
    *,
    now: datetime | None = None,
    factory: Factory = SessionFactory,
) -> UsageReport:
    if not can_edit_tenant(member.role):
        raise PermissionDeniedError("Tu rol no te permite ver el consumo del negocio")
    async with tenant_session(member.tenant_id, member.user_id, factory=factory) as session:
        timezone = await session.scalar(
            select(Tenant.timezone).where(Tenant.id == member.tenant_id)
        )
        zone = _zone(timezone)
        today = (now or datetime.now(UTC)).astimezone(zone).date()
        start, end = period_range(period, today)

        rows = await session.scalars(
            select(UsageDaily).where(UsageDaily.day >= start, UsageDaily.day <= end)
        )
        by_day = {row.day: row for row in rows}

        # Las respuestas de IA se cuentan por el día local en que se escribieron.
        local_day = cast(func.timezone(zone.key, LLMRun.created_at), Date)
        replies = dict(
            (
                await session.execute(
                    select(local_day, func.count())
                    .where(
                        LLMRun.status == LLMRunStatus.OK,
                        LLMRun.created_at >= datetime.combine(start, time.min, tzinfo=zone),
                        LLMRun.created_at
                        < datetime.combine(end + timedelta(days=1), time.min, tzinfo=zone),
                    )
                    .group_by(local_day)
                )
            ).all()
        )
        tracking_since = await session.scalar(select(func.min(UsageDaily.day)))

    days: list[DayUsage] = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        row = by_day.get(day)
        days.append(
            DayUsage(
                day=day,
                conversations=row.conversations if row else 0,
                inbound_messages=row.inbound_messages if row else 0,
                outbound_messages=row.outbound_messages if row else 0,
                ai_replies=int(replies.get(day, 0)),
                input_tokens=int(row.llm_input_tokens) if row else 0,
                output_tokens=int(row.llm_output_tokens) if row else 0,
            )
        )
    return UsageReport(period, start, end, zone.key, days, tracking_since)
