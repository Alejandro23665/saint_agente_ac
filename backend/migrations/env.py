"""Punto de entrada de Alembic. Conecta con la BD y ejecuta las migraciones."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401  (registra las 19 tablas en Base.metadata)
from app.core.config import get_settings
from app.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_url() -> str:
    # Las pruebas pueden pasar otra URL con: alembic -x url=postgresql+asyncpg://...
    return context.get_x_argument(as_dictionary=True).get("url") or (
        get_settings().database_admin_url
    )


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    # La columna tsv es generada; Alembic no compara bien su expresión.
    return not (type_ == "column" and name == "tsv")


def run_migrations_offline() -> None:
    """Genera el SQL sin conectarse (alembic upgrade head --sql)."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_server_default=False,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    # NullPool: Alembic abre una sola conexión y la cierra al terminar.
    engine = create_async_engine(database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
