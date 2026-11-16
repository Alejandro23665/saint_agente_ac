"""Pruebas del endpoint /health."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from app.core.config import Settings, get_settings
from app.main import create_app
from tests.conftest import admin_url

# Direcciones donde no escucha nada: simulan un servicio caído.
REDIS_DOWN = "redis://127.0.0.1:6399/0"
POSTGRES_DOWN = "postgresql+asyncpg://nadie:nada@127.0.0.1:6398/saint"


@asynccontextmanager
async def client_for(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """Arranca la app (con su lifespan) y le hace peticiones sin abrir un puerto real."""
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def settings_with(migrated_database: str, **overrides: str) -> Settings:
    base = {
        "database_url": admin_url(migrated_database).render_as_string(hide_password=False),
        "redis_url": get_settings().redis_url,
    }
    return Settings(**(base | overrides))


async def test_todo_bien(migrated_database: str) -> None:
    async with client_for(settings_with(migrated_database)) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"postgres": "ok", "redis": "ok"}}


async def test_redis_caido(migrated_database: str) -> None:
    async with client_for(settings_with(migrated_database, redis_url=REDIS_DOWN)) as client:
        response = await client.get("/health")
    assert response.status_code == 503
    assert response.json()["checks"] == {"postgres": "ok", "redis": "error"}


async def test_postgres_caido(migrated_database: str) -> None:
    settings = settings_with(migrated_database, database_url=POSTGRES_DOWN)
    async with client_for(settings) as client:
        response = await client.get("/health")
    assert response.status_code == 503
    body = response.json()
    assert body["checks"] == {"postgres": "error", "redis": "ok"}
    # La respuesta no revela detalles internos (usuario, host, mensaje del error).
    assert "nadie" not in response.text


async def test_docs_solo_en_desarrollo(migrated_database: str) -> None:
    async with client_for(settings_with(migrated_database, app_env="development")) as client:
        assert (await client.get("/docs")).status_code == 200
    async with client_for(settings_with(migrated_database, app_env="production")) as client:
        assert (await client.get("/docs")).status_code == 404
        assert (await client.get("/openapi.json")).status_code == 404
