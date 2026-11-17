"""Punto de entrada de la API.

Para arrancarla en desarrollo:  uv run uvicorn app.main:app --reload
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api import (
    agent,
    auth,
    conversations,
    events,
    health,
    knowledge,
    numbers,
    tenant,
    usage,
    webhooks,
)
from app.core.config import Settings, get_settings
from app.core.queue import create_queue_pool
from app.core.redis import create_redis
from app.db.session import build_engine


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construye la aplicación. Las pruebas pueden pasarle otra configuración."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Al arrancar: abrir las conexiones una sola vez y compartirlas entre peticiones.
        app.state.engine = build_engine(settings.database_url)
        app.state.session_factory = async_sessionmaker(app.state.engine, expire_on_commit=False)
        app.state.redis = create_redis(settings.redis_url)
        app.state.queue = create_queue_pool(settings.redis_url)
        yield
        await app.state.queue.aclose()
        # Al apagar: cerrarlas ordenadamente.
        await app.state.redis.aclose()
        await app.state.engine.dispose()

    is_dev = settings.app_env == "development"
    app = FastAPI(
        title="Saint API",
        version="0.1.0",
        lifespan=lifespan,
        # La documentación interactiva (/docs) solo en desarrollo: en producción
        # no queremos publicar el mapa de la API.
        docs_url="/docs" if is_dev else None,
        redoc_url=None,
        openapi_url="/openapi.json" if is_dev else None,
    )
    # El panel web vive en otra dirección (otro puerto u otro dominio): el navegador solo
    # le deja llamar a la API, con su cookie de sesión, si la API lo autoriza aquí.
    origins = [o.strip() for o in settings.panel_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )
    app.include_router(health.router)
    app.include_router(webhooks.router)
    app.include_router(auth.router)
    app.include_router(tenant.router)
    app.include_router(numbers.router)
    app.include_router(conversations.router)
    app.include_router(agent.router)
    app.include_router(knowledge.router)
    app.include_router(usage.router)
    app.include_router(events.router)
    return app


app = create_app()
