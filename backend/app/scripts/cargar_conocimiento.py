"""Cargar, ver y borrar el conocimiento de un negocio (lo que el bot sabrá responder).

Uso (hace falta OPENROUTER_API_KEY en el .env para cargar):
    uv run python -m app.scripts.cargar_conocimiento cargar panaderia-prueba info.txt
    uv run python -m app.scripts.cargar_conocimiento cargar panaderia-prueba h.md --titulo Horarios
    uv run python -m app.scripts.cargar_conocimiento listar panaderia-prueba
    uv run python -m app.scripts.cargar_conocimiento borrar panaderia-prueba "Horarios"

El título por defecto es el nombre del archivo. Cargar otro texto con el mismo título
reemplaza al anterior. El archivo debe ser texto (.txt o .md) en UTF-8.

Ojo: el modelo gratuito de OpenRouter guarda lo que se le manda. Carga solo información
pública del negocio, nunca datos de clientes ni contraseñas.
"""

import argparse
import asyncio
from pathlib import Path

from app.db.session import tenant_session
from app.integrations.embeddings import OpenRouterEmbedder
from app.models.enums import KnowledgeSourceType, ProcessingStatus
from app.scripts.enviar_mensaje import find_tenant
from app.services.knowledge import KnowledgeError, delete_source, list_sources, load_text


async def cargar(slug: str, path: Path, title: str | None) -> None:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise SystemExit(f"No se pudo leer {path}: {exc}") from exc
    embedder = OpenRouterEmbedder.from_settings()
    if embedder is None:
        raise SystemExit("Falta OPENROUTER_API_KEY en el .env")
    tenant_id = await find_tenant(slug)
    try:
        result = await load_text(
            tenant_id,
            title or path.stem,
            text,
            embedder=embedder,
            source_type=KnowledgeSourceType.FILE,
            file_url=path.name,
        )
    except KnowledgeError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        await embedder.aclose()

    if result.already_loaded:
        print(f"Ese texto ya estaba cargado ({result.chunk_count} fragmentos). No se hizo nada.")
    elif result.status == ProcessingStatus.READY:
        print(f"Listo: {result.chunk_count} fragmentos guardados con su embedding.")
        if result.replaced:
            print(f"Se reemplazó la versión anterior con el mismo título ({result.replaced}).")
    else:
        raise SystemExit(f"No se pudo cargar: {result.error}")


async def listar(slug: str) -> None:
    tenant_id = await find_tenant(slug)
    async with tenant_session(tenant_id) as session:
        sources = await list_sources(session)
    if not sources:
        print("Este negocio todavía no tiene conocimiento cargado.")
    for s in sources:
        extra = f" - {s.error}" if s.error else ""
        when = f"{s.created_at:%Y-%m-%d %H:%M}"
        print(f"- {s.title}: {s.status}, {s.chunk_count} fragmentos, {when}{extra}")


async def borrar(slug: str, title: str) -> None:
    tenant_id = await find_tenant(slug)
    async with tenant_session(tenant_id) as session:
        deleted = await delete_source(session, title)
    print(f"Borrados: {deleted}" if deleted else f"No hay ningún documento llamado {title!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="accion", required=True)
    load = commands.add_parser("cargar", help="cargar un archivo de texto")
    load.add_argument("negocio", help="slug del negocio, ej. panaderia-prueba")
    load.add_argument("archivo", type=Path, help="archivo .txt o .md en UTF-8")
    load.add_argument("--titulo", help="título del documento (por defecto, el nombre del archivo)")
    show = commands.add_parser("listar", help="ver los documentos cargados")
    show.add_argument("negocio")
    remove = commands.add_parser("borrar", help="borrar un documento por su título")
    remove.add_argument("negocio")
    remove.add_argument("titulo")
    args = parser.parse_args()

    if args.accion == "cargar":
        asyncio.run(cargar(args.negocio, args.archivo, args.titulo))
    elif args.accion == "listar":
        asyncio.run(listar(args.negocio))
    else:
        asyncio.run(borrar(args.negocio, args.titulo))


if __name__ == "__main__":
    main()
