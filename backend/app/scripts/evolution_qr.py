"""Prueba manual: conectar un WhatsApp a Evolution escaneando un QR.

Uso (con docker compose levantado):
    uv run python -m app.scripts.evolution_qr prueba-andre
    uv run python -m app.scripts.evolution_qr prueba-andre --borrar

Crea la instancia (si no existe), guarda el QR como imagen en qr-<nombre>.png y espera
hasta que el teléfono quede conectado. Todavía no guarda nada en la base de Saint:
eso es el bloque 3.3.
"""

import argparse
import asyncio
import base64
from pathlib import Path

from app.integrations.evolution import EvolutionClient, EvolutionError, QrCode

WAIT_SECONDS = 120


def save_qr(qr: QrCode, name: str) -> Path:
    # base64 viene como "data:image/png;base64,iVBOR...": se quita el prefijo.
    raw = (qr.base64 or "").split(",", 1)[-1]
    path = Path(f"qr-{name}.png")
    path.write_bytes(base64.b64decode(raw))
    return path


async def connect(name: str) -> None:
    async with EvolutionClient.from_settings() as evo:
        try:
            created = await evo.create_instance(name)
            qr = created.qr
            print(f"Instancia creada: {created.name}")
        except EvolutionError as exc:
            if exc.status_code != 403:  # 403 = "This name ... is already in use"
                raise
            print(f"La instancia {name} ya existía; pido un QR nuevo.")
            qr = await evo.get_qr(name)

        if qr is None or not qr.base64:
            print("Evolution no devolvió QR: el número ya está conectado.")
            return
        path = save_qr(qr, name)
        print(f"Abre {path.resolve()} y escanéalo desde WhatsApp > Dispositivos vinculados.")

        for _ in range(WAIT_SECONDS // 3):
            state = await evo.connection_state(name)
            if state == "open":
                print("Conectado.")
                path.unlink(missing_ok=True)  # el QR ya no sirve; no dejarlo tirado
                return
            await asyncio.sleep(3)
        print(f"No se conectó en {WAIT_SECONDS} s. Vuelve a ejecutar el comando.")


async def delete(name: str) -> None:
    async with EvolutionClient.from_settings() as evo:
        await evo.delete_instance(name)
        print(f"Instancia {name} borrada de Evolution.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("nombre", help="nombre de la instancia, ej. prueba-andre")
    parser.add_argument("--borrar", action="store_true", help="desvincula y borra la instancia")
    args = parser.parse_args()
    asyncio.run(delete(args.nombre) if args.borrar else connect(args.nombre))


if __name__ == "__main__":
    main()
