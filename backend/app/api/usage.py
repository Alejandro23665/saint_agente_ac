"""Ruta del consumo del negocio activo: GET /usage (bloque 6.10).

- GET /usage?period=this_month|last_month|last_7_days|last_30_days

Solo dueño y administrador. Los días van en la zona horaria del negocio y se devuelven todos
los del periodo, con ceros donde no pasó nada. El costo estimado solo aparece si hay precios
en el .env (LLM_INPUT_USD_PER_MILLION y LLM_OUTPUT_USD_PER_MILLION): Saint no inventa tarifas.
"""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.auth import Factory, current_member
from app.core.config import get_settings
from app.services import members, usage
from app.services.members import Member

router = APIRouter(prefix="/usage", tags=["consumo"])

CurrentMember = Annotated[Member, Depends(current_member)]
Period = Literal["this_month", "last_month", "last_7_days", "last_30_days"]


class DayOut(BaseModel):
    day: date
    conversations: int
    inbound_messages: int
    outbound_messages: int
    ai_replies: int
    input_tokens: int
    output_tokens: int


class TotalsOut(BaseModel):
    conversations: int
    inbound_messages: int
    outbound_messages: int
    ai_replies: int
    input_tokens: int
    output_tokens: int


class PricingOut(BaseModel):
    configured: bool
    input_usd_per_million: float | None
    output_usd_per_million: float | None


class UsageOut(BaseModel):
    period: Period
    start: date
    end: date
    timezone: str
    # Primer día con datos. Antes de esa fecha el consumo no se registraba.
    tracking_since: date | None
    totals: TotalsOut
    pricing: PricingOut
    estimated_cost_usd: float | None
    days: list[DayOut]


def _totals(days: list[usage.DayUsage]) -> TotalsOut:
    return TotalsOut(
        conversations=sum(d.conversations for d in days),
        inbound_messages=sum(d.inbound_messages for d in days),
        outbound_messages=sum(d.outbound_messages for d in days),
        ai_replies=sum(d.ai_replies for d in days),
        input_tokens=sum(d.input_tokens for d in days),
        output_tokens=sum(d.output_tokens for d in days),
    )


@router.get("", response_model=UsageOut)
async def get_usage(
    member: CurrentMember, factory: Factory, period: Period = "this_month"
) -> UsageOut:
    try:
        result = await usage.report(member, period, factory=factory)
    except members.PermissionDeniedError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc

    totals = _totals(result.days)
    settings = get_settings()
    price_in = settings.llm_input_usd_per_million
    price_out = settings.llm_output_usd_per_million
    configured = price_in is not None and price_out is not None
    cost = None
    if configured:
        cost = round(
            totals.input_tokens / 1_000_000 * price_in
            + totals.output_tokens / 1_000_000 * price_out,
            4,
        )
    return UsageOut(
        period=period,
        start=result.start,
        end=result.end,
        timezone=result.timezone,
        tracking_since=result.tracking_since,
        totals=totals,
        pricing=PricingOut(
            configured=configured, input_usd_per_million=price_in, output_usd_per_million=price_out
        ),
        estimated_cost_usd=cost,
        days=[
            DayOut(
                day=d.day,
                conversations=d.conversations,
                inbound_messages=d.inbound_messages,
                outbound_messages=d.outbound_messages,
                ai_replies=d.ai_replies,
                input_tokens=d.input_tokens,
                output_tokens=d.output_tokens,
            )
            for d in result.days
        ],
    )
