"""Conexión a PostgreSQL y sesiones con el negocio activo (para RLS)."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


def build_engine(url: str) -> AsyncEngine:
    # pool_pre_ping descarta conexiones muertas antes de usarlas.
    return create_async_engine(url, pool_pre_ping=True)


engine = build_engine(get_settings().database_url)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def set_request_context(
    session: AsyncSession, tenant_id: uuid.UUID | None, user_id: uuid.UUID | None = None
) -> None:
    """Le dice a PostgreSQL qué negocio (y qué usuario) está activo.

    set_config(..., true) equivale a SET LOCAL: el valor dura solo hasta que termina
    la transacción, así que nunca se "filtra" a la siguiente petición que reutilice
    la misma conexión del pool.
    """
    if tenant_id is not None:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
    if user_id is not None:
        await session.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"),
            {"user_id": str(user_id)},
        )


@asynccontextmanager
async def tenant_session(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> AsyncIterator[AsyncSession]:
    """Abre una transacción en la que todas las consultas ven solo los datos del negocio.

    Uso:
        async with tenant_session(tenant_id) as session:
            ...  # consultas; al salir se hace commit (o rollback si hubo error)
    """
    async with factory() as session, session.begin():
        await set_request_context(session, tenant_id, user_id)
        yield session


@asynccontextmanager
async def user_session(
    user_id: uuid.UUID,
    factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> AsyncIterator[AsyncSession]:
    """Transacción sin negocio activo, solo con el usuario (pantalla de elegir negocio).

    Solo deja ver los negocios y membresías del propio usuario. Cualquier tabla de datos
    de un negocio (contactos, mensajes...) da error porque no hay negocio activo.
    """
    async with factory() as session, session.begin():
        await set_request_context(session, None, user_id)
        yield session
