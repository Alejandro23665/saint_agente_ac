"""Endpoint que recibe los avisos de Evolution: POST /webhooks/evolution/{public_id}."""

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.channel_registration import WEBHOOK_SECRET_HEADER
from app.services.webhook_ingest import (
    InvalidSecretError,
    UnknownInstanceError,
    store_webhook_event,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Sin imágenes en base64 (base64=false), un aviso normal pesa unos pocos KB.
# Por encima de esto lo rechazamos para que nadie pueda llenarnos la base.
MAX_BODY_BYTES = 1_000_000


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    """La fábrica de sesiones que creó la app al arrancar (las pruebas la reemplazan)."""
    return request.app.state.session_factory


def error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


@router.post("/evolution/{public_id}")
async def evolution_webhook(
    public_id: str,
    request: Request,
    factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> JSONResponse:
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        return error(413, "Aviso demasiado grande")
    try:
        payload = json.loads(body)
    except ValueError:
        return error(400, "JSON no válido")
    if not isinstance(payload, dict) or not isinstance(payload.get("event"), str):
        return error(400, "Falta el campo event")

    try:
        stored = await store_webhook_event(
            public_id, request.headers.get(WEBHOOK_SECRET_HEADER), payload, factory
        )
    except UnknownInstanceError:
        # 404: Evolution no reintenta, y así un número borrado deja de insistir.
        return error(404, "Número desconocido")
    except InvalidSecretError:
        logger.warning("Webhook con secreto inválido para public_id=%s", public_id)
        return error(401, "Secreto inválido")

    return JSONResponse(
        {"status": "duplicate" if stored.duplicate else "stored", "event": stored.event_type}
    )
