"""GET /events: avisos en tiempo real del negocio activo (Server-Sent Events).

El panel abre esta conexión y la deja abierta. Por ella llegan avisos así:

    event: conversation.changed
    data: {"type": "conversation.changed", "conversation_id": "..."}

y cada 15 segundos una línea ": ping" para que nada en el camino corte la conexión.

Se pide con el pase de acceso en la cabecera Authorization, como el resto de la API (el
panel usa fetch con streaming, no EventSource, que no deja mandar cabeceras). Así el
pase nunca va en la dirección y no queda en los registros.

La conexión se cierra sola, con un aviso "session.expired", cuando vence el pase de
acceso o cuando la persona pierde el acceso al negocio. El panel renueva el pase y
vuelve a conectarse.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.auth import Factory, current_member, current_tenant_claims, get_redis
from app.core import events
from app.core.auth_tokens import AccessClaims
from app.services import members
from app.services.members import Member

router = APIRouter(tags=["tiempo real"])

PING_SECONDS = 15.0
POLL_SECONDS = 1.0  # cada cuánto se mira Redis (menos que su socket_timeout de 2 s)


def _sse(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


async def event_stream(
    member: Member,
    expires_at: datetime,
    redis: Redis,
    factory: async_sessionmaker[AsyncSession],
    is_disconnected: Callable[[], Awaitable[bool]],
    *,
    ping_seconds: float = PING_SECONDS,
    poll_seconds: float = POLL_SECONDS,
) -> AsyncIterator[str]:
    """Los avisos del negocio, en formato SSE, hasta que se corte o venza el pase."""
    pubsub = redis.pubsub(ignore_subscribe_messages=True)
    await pubsub.subscribe(events.channel(member.tenant_id))
    try:
        yield _sse("ready", {"type": "ready"})
        last_ping = time.monotonic()
        while True:
            if await is_disconnected():
                return
            message = await pubsub.get_message(timeout=poll_seconds)
            if message is not None and message.get("type") == "message":
                raw = message["data"]
                data = json.loads(raw if isinstance(raw, str) else raw.decode())
                yield _sse(data.get("type", "message"), data)
            if time.monotonic() - last_ping >= ping_seconds:
                last_ping = time.monotonic()
                # Con cada ping se revisa que la persona siga teniendo acceso.
                expired = datetime.now(UTC) >= expires_at
                still = await members.get_member(member.user_id, member.tenant_id, factory=factory)
                if expired or still is None:
                    yield _sse("session.expired", {"type": "session.expired"})
                    return
                yield ": ping\n\n"
            if message is None:
                await asyncio.sleep(0)  # deja respirar al loop si Redis respondió al instante
    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()


@router.get("/events")
async def stream_events(
    request: Request,
    member: Annotated[Member, Depends(current_member)],
    claims: Annotated[AccessClaims, Depends(current_tenant_claims)],
    redis: Annotated[Redis, Depends(get_redis)],
    factory: Factory,
) -> StreamingResponse:
    return StreamingResponse(
        event_stream(member, claims.expires_at, redis, factory, request.is_disconnected),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Caddy/nginx: no guardar la respuesta, mandarla tal cual va saliendo.
            "X-Accel-Buffering": "no",
        },
    )
