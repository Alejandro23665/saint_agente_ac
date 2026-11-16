"""Registrar un número de WhatsApp para un negocio y vincularlo con QR.

Uso (con docker compose levantado y la migración aplicada):
    uv run python -m app.scripts.registrar_numero "Panadería Prueba"
    uv run python -m app.scripts.registrar_numero "Panadería Prueba" --grupos

Si el negocio no existe, lo crea. Luego crea el número en Evolution con su webhook
apuntando a Saint, lo guarda en channel_instances, guarda el QR en qr-<negocio>.png y
espera a que lo escanees. Al conectar, guarda el teléfono y el estado "open".
"""

import argparse
import asyncio
import base64
import re
import unicodedata
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.db.session import build_engine
from app.integrations.evolution import EvolutionClient
from app.models import Tenant
from app.models.enums import InstanceStatus
from app.services.channel_registration import (
    refresh_connection_status,
    register_whatsapp_number,
)

WAIT_SECONDS = 120


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")


async def find_or_create_tenant(name: str) -> uuid.UUID:
    # Herramienta de administración: usa el usuario dueño (sin RLS) para buscar el
    # negocio por su slug. La API nunca hará esto; el panel sabrá el negocio del usuario.
    engine = build_engine(get_settings().database_admin_url)
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            slug = slugify(name)
            tenant = await session.scalar(select(Tenant).where(Tenant.slug == slug))
            if tenant is None:
                tenant = Tenant(name=name, slug=slug, country_code="VE")
                session.add(tenant)
                await session.flush()
                print(f"Negocio creado: {name} ({slug})")
            else:
                print(f"Negocio existente: {tenant.name} ({slug})")
            return tenant.id
    finally:
        await engine.dispose()


async def main_async(name: str, groups: bool) -> None:
    tenant_id = await find_or_create_tenant(name)
    async with EvolutionClient.from_settings() as evo:
        # Desde aquí todo va con el usuario de la app (saint_app), con RLS.
        registered = await register_whatsapp_number(evo, tenant_id, groups_enabled=groups)
        print(f"Número registrado. Webhook: {registered.webhook_url}")

        if registered.qr is None or not registered.qr.base64:
            print("Evolution no devolvió QR; vuelve a intentarlo en unos segundos.")
            return
        path = Path(f"qr-{slugify(name)}.png")
        path.write_bytes(base64.b64decode(registered.qr.base64.split(",", 1)[-1]))
        print(f"Abre {path.resolve()} y escanéalo desde WhatsApp > Dispositivos vinculados.")

        for _ in range(WAIT_SECONDS // 3):
            status = await refresh_connection_status(evo, tenant_id, registered.channel_instance_id)
            if status == InstanceStatus.OPEN:
                path.unlink(missing_ok=True)
                print("Conectado. Estado guardado en channel_instances.")
                return
            await asyncio.sleep(3)
        print(f"No se conectó en {WAIT_SECONDS} s. El número quedó guardado como qr_pending.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("negocio", help='nombre del negocio, ej. "Panadería Prueba"')
    parser.add_argument("--grupos", action="store_true", help="el bot también lee grupos")
    args = parser.parse_args()
    asyncio.run(main_async(args.negocio, args.grupos))


if __name__ == "__main__":
    main()
