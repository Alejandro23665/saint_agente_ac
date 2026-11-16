"""La migración crea todo lo previsto, coincide con los modelos y se puede deshacer."""

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

import app.models  # noqa: F401  (registra las 19 tablas)
from app.db.base import Base
from tests.conftest import admin_url, drop_database, recreate_database, run_alembic

RLS_TABLES = {name for name, t in Base.metadata.tables.items() if "tenant_id" in t.c} | {
    "tenants",
    "agent_versions",
}


async def test_crea_las_19_tablas(owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        tables = {r[0] for r in rows} - {"alembic_version"}
    assert tables == set(Base.metadata.tables)
    assert len(tables) == 19


def test_modelos_y_migracion_coinciden(migrated_database: str) -> None:
    # "alembic check" falla si los modelos tienen algo que la migración no creó.
    result = run_alembic("check", database=migrated_database)
    assert result.returncode == 0, result.stdout + result.stderr


async def test_rls_activado_y_forzado(owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace "
                "AND relkind = 'r' AND relrowsecurity AND relforcerowsecurity"
            )
        )
        protected = {r[0] for r in rows}
    assert len(RLS_TABLES) == 17
    assert protected == RLS_TABLES


async def test_cada_tabla_protegida_tiene_politica(owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as conn:
        rows = await conn.execute(text("SELECT tablename FROM pg_policies"))
        with_policy = {r[0] for r in rows}
    assert with_policy == RLS_TABLES


def test_bajar_y_volver_a_subir() -> None:
    """downgrade deja la base vacía y upgrade la reconstruye sin errores."""
    db = "saint_test_migracion"
    asyncio.run(recreate_database(db))
    try:
        for step in (("upgrade", "head"), ("downgrade", "base"), ("upgrade", "head")):
            result = run_alembic(*step, database=db)
            assert result.returncode == 0, f"{step}: {result.stderr}"

        result = run_alembic("downgrade", "base", database=db)
        assert result.returncode == 0, result.stderr

        async def leftovers() -> tuple[int, int]:
            engine = create_async_engine(admin_url(db))
            async with engine.connect() as conn:
                tables = await conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' "
                        "AND tablename <> 'alembic_version'"
                    )
                )
                enums = await conn.scalar(text("SELECT count(*) FROM pg_type WHERE typtype = 'e'"))
            await engine.dispose()
            return tables, enums

        assert asyncio.run(leftovers()) == (0, 0)
    finally:
        asyncio.run(drop_database(db))
