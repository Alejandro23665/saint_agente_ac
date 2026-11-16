"""Endpoint /health: dice si la API puede hablar con PostgreSQL y con Redis.

Lo usarán Docker y el monitoreo del servidor para saber si hay que reiniciar algo.
"""

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])

CHECK_TIMEOUT_SECONDS = 2.0

CheckResult = Literal["ok", "error"]


class HealthResponse(BaseModel):
    status: CheckResult
    checks: dict[str, CheckResult]


async def check_postgres(request: Request) -> CheckResult:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            async with request.app.state.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        return "ok"
    except Exception:
        # El detalle va al log del servidor, nunca a la respuesta: podría revelar
        # direcciones o usuarios internos.
        logger.exception("health: PostgreSQL no responde")
        return "error"


async def check_redis(request: Request) -> CheckResult:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            await request.app.state.redis.ping()
        return "ok"
    except Exception:
        logger.exception("health: Redis no responde")
        return "error"


@router.get("/health", response_model=HealthResponse)
async def health(request: Request, response: Response) -> HealthResponse:
    # Las dos comprobaciones corren a la vez; el endpoint tarda como la más lenta.
    postgres, redis = await asyncio.gather(check_postgres(request), check_redis(request))
    checks: dict[str, CheckResult] = {"postgres": postgres, "redis": redis}
    healthy = all(result == "ok" for result in checks.values())
    if not healthy:
        # 503 = "servicio no disponible": Docker y los balanceadores lo entienden.
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(status="ok" if healthy else "error", checks=checks)
