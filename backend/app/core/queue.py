"""Colas de trabajo (ARQ sobre Redis): nombres y cómo encolar."""

import uuid

from arq.connections import ArqRedis, RedisSettings
from redis.asyncio import ConnectionPool

# Cola de entrada: un trabajo por cada aviso de Evolution guardado en webhook_events.
INGEST_QUEUE = "saint:ingest"
PROCESS_WEBHOOK_EVENT = "process_webhook_event"


def redis_settings(url: str) -> RedisSettings:
    return RedisSettings.from_dsn(url)


def create_queue_pool(url: str, queue_name: str = INGEST_QUEUE) -> ArqRedis:
    """Conexión para encolar. No se conecta hasta el primer uso: si Redis está caído
    al arrancar, la API arranca igual (y /health lo avisa)."""
    pool = ConnectionPool.from_url(url, socket_connect_timeout=2, socket_timeout=2)
    return ArqRedis(connection_pool=pool, default_queue_name=queue_name)


def webhook_job_id(event_id: uuid.UUID) -> str:
    # Mismo id de trabajo para el mismo aviso: ARQ no lo encola dos veces si ya está
    # en la cola (por ejemplo, si el barrido lo vuelve a encontrar).
    return f"webhook-event:{event_id}"


async def enqueue_webhook_event(pool: ArqRedis, event_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
    """Encola el aviso. Devuelve False si ya estaba en la cola."""
    job = await pool.enqueue_job(
        PROCESS_WEBHOOK_EVENT,
        str(event_id),
        str(tenant_id),
        _job_id=webhook_job_id(event_id),
        # La cola de la conexión: saint:ingest (las pruebas usan otra para no mezclarse).
        _queue_name=pool.default_queue_name,
    )
    return job is not None
