"""Conexión a Redis (colas de ARQ, bloqueos del debouncing y caché)."""

from redis.asyncio import Redis


def create_redis(url: str) -> Redis:
    # decode_responses: Redis devuelve texto (str) en vez de bytes.
    # socket_timeout: si Redis no responde en 2 s, falla en vez de quedarse colgado.
    return Redis.from_url(url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)
