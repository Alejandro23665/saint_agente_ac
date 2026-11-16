"""Enviar un mensaje de prueba por WhatsApp desde el número de un negocio.

Uso (con la API de Evolution, Redis y el worker en marcha):
    uv run python -m app.scripts.enviar_mensaje panaderia-prueba "+58 412 1234567" "Hola"

Guarda el mensaje en la cola de salida (messages, status=queued), le avisa al worker
por Redis y espera hasta 30 s mostrando cómo cambia el estado:
queued -> sending -> sent -> delivered -> read.
"""

import argparse
import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.queue import create_queue_pool, enqueue_outbound
from app.db.session import build_engine, tenant_session
from app.models import ChannelInstance, Message, Tenant
from app.models.enums import InstanceStatus, MessageStatus
from app.services.outbound import queue_text_to_phone

WAIT_SECONDS = 30
FINAL = {MessageStatus.DELIVERED, MessageStatus.READ, MessageStatus.FAILED}


async def find_tenant(slug: str) -> uuid.UUID:
    # Herramienta de administración: busca el negocio por slug con el usuario dueño.
    engine = build_engine(get_settings().database_admin_url)
    try:
        async with async_sessionmaker(engine)() as session:
            tenant_id = await session.scalar(select(Tenant.id).where(Tenant.slug == slug))
    finally:
        await engine.dispose()
    if tenant_id is None:
        raise SystemExit(f"No existe el negocio {slug!r}")
    return tenant_id


async def main_async(slug: str, phone: str, text: str) -> None:
    settings = get_settings()
    tenant_id = await find_tenant(slug)

    # Desde aquí, con el usuario de la app y RLS, igual que hará el bot.
    async with tenant_session(tenant_id) as session:
        instance_id = await session.scalar(
            select(ChannelInstance.id)
            .where(ChannelInstance.status == InstanceStatus.OPEN)
            .order_by(ChannelInstance.created_at)
            .limit(1)
        )
        if instance_id is None:
            raise SystemExit("El negocio no tiene ningún número conectado (estado open)")
        message_id = await queue_text_to_phone(session, tenant_id, instance_id, phone, text)
    print(f"Mensaje guardado en la cola: {message_id}")

    # Encolar DESPUÉS del commit, para que el worker encuentre la fila.
    pool = create_queue_pool(settings.redis_url)
    try:
        await enqueue_outbound(pool, message_id, tenant_id)
    except Exception as exc:  # noqa: BLE001 - el barrido del worker lo enviará igual
        print(f"No se pudo avisar al worker ({exc!r}); el barrido lo enviará en ~2 min.")
    finally:
        await pool.aclose()

    last = None
    for _ in range(WAIT_SECONDS):
        async with tenant_session(tenant_id) as session:
            message = await session.get(Message, message_id)
        if message.status != last:
            last = message.status
            extra = f" (id WhatsApp {message.external_id})" if message.external_id else ""
            print(f"Estado: {last}{extra}" + (f" - {message.error}" if message.error else ""))
        if last in FINAL:
            return
        await asyncio.sleep(1)
    print(f"Pasaron {WAIT_SECONDS} s; el último estado fue {last}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("negocio", help="slug del negocio, ej. panaderia-prueba")
    parser.add_argument("telefono", help='número con código de país, ej. "+58 412 1234567"')
    parser.add_argument("texto", help="texto a enviar")
    args = parser.parse_args()
    asyncio.run(main_async(args.negocio, args.telefono, args.texto))


if __name__ == "__main__":
    main()
