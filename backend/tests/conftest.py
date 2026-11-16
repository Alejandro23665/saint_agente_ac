"""Preparación común de las pruebas.

Se crea una base de datos nueva (saint_test), se le aplican las migraciones y al final
se borra. Hacen falta PostgreSQL con pgvector corriendo (docker compose up -d postgres).
"""

import asyncio
import os
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.core.config import get_settings

BACKEND_DIR = Path(__file__).resolve().parent.parent
TEST_DB = "saint_test"


def admin_url(database: str) -> URL:
    """URL del dueño (saint_owner) apuntando a otra base de datos."""
    base = os.environ.get("TEST_DATABASE_ADMIN_URL", get_settings().database_admin_url)
    return make_url(base).set(database=database)


def run_alembic(*args: str, database: str = TEST_DB) -> subprocess.CompletedProcess[str]:
    url = admin_url(database).render_as_string(hide_password=False)
    return subprocess.run(
        ["alembic", "-x", f"url={url}", *args],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )


async def recreate_database(name: str) -> None:
    # CREATE DATABASE no puede ir dentro de una transacción: AUTOCOMMIT.
    engine = create_async_engine(admin_url("postgres"), isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    await engine.dispose()


async def drop_database(name: str) -> None:
    engine = create_async_engine(admin_url("postgres"), isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    await engine.dispose()


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[str]:
    """Base de prueba con todas las migraciones aplicadas."""
    asyncio.run(recreate_database(TEST_DB))
    result = run_alembic("upgrade", "head")
    assert result.returncode == 0, result.stderr
    yield TEST_DB
    asyncio.run(drop_database(TEST_DB))


@pytest.fixture(scope="session")
async def owner_engine(migrated_database: str) -> AsyncIterator[AsyncEngine]:
    """Conexión como dueño de las tablas: no le aplica RLS (prepara datos de prueba)."""
    engine = create_async_engine(admin_url(migrated_database))
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
async def app_engine(migrated_database: str) -> AsyncIterator[AsyncEngine]:
    """Conexión con los permisos reales de la aplicación (rol saint_app, con RLS).

    Entramos como dueño y cambiamos de rol con SET ROLE; así la prueba no necesita la
    contraseña de saint_app. A partir de ahí la conexión tiene SOLO los permisos de
    saint_app. pool_size=1 obliga a reutilizar la misma conexión entre peticiones, para
    comprobar que el negocio activo no pasa de una transacción a la siguiente.
    """
    engine = create_async_engine(admin_url(migrated_database), pool_size=1, max_overflow=0)
    role = get_settings().app_db_user

    @event.listens_for(engine.sync_engine, "connect")
    def _set_role(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute(f"SET ROLE {role}")
        cursor.close()

    yield engine
    await engine.dispose()


@pytest.fixture
async def owner_session(owner_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Sesión de dueño dentro de una transacción que se deshace al terminar la prueba."""
    async with owner_engine.connect() as conn:
        trans = await conn.begin()
        # create_savepoint: si una prueba provoca un error, solo se deshace el savepoint
        # y la transacción exterior sigue viva para el rollback final.
        session = AsyncSession(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield session
        finally:
            await session.close()
            if trans.is_active:
                await trans.rollback()


@pytest.fixture(autouse=True)
def bot_answers_everyone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Las pruebas no dependen de BOT_TEST_PHONES del .env de quien las corre."""
    monkeypatch.setattr(get_settings(), "bot_test_phones", "")
