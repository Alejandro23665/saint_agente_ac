"""Pruebas del endpoint /health."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from cryptography.fernet import Fernet

from app.core.config import ConfigError, Settings, get_settings
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


FERNET = Fernet.generate_key().decode()
JWT = "x" * 40

# Lo mínimo para que arranque en producción: claves, sin contraseñas de ejemplo y HTTPS.
PRODUCTION = {
    "app_env": "production",
    "database_admin_url": "postgresql+asyncpg://saint_owner:clave-real@db:5432/saint",
    "evolution_api_key": "clave-evolution",
    "panel_url": "https://panel.saint.example",
    "panel_origins": "https://panel.saint.example",
}


def settings_with(migrated_database: str, **overrides: str) -> Settings:
    base = {
        "app_env": "development",
        "database_url": admin_url(migrated_database).render_as_string(hide_password=False),
        "redis_url": get_settings().redis_url,
        "jwt_secret": JWT,
        "fernet_key": FERNET,
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
    async with client_for(settings_with(migrated_database, **PRODUCTION)) as client:
        assert (await client.get("/docs")).status_code == 404
        assert (await client.get("/openapi.json")).status_code == 404


def test_sin_app_env_se_asume_produccion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    assert Settings(_env_file=None).app_env == "production"


def test_sin_claves_no_arranca() -> None:
    settings = Settings(_env_file=None, app_env="development", jwt_secret="", fernet_key="")
    problems = settings.startup_problems()
    assert any("JWT_SECRET" in p for p in problems)
    assert any("FERNET_KEY" in p for p in problems)
    with pytest.raises(ConfigError):
        settings.check_startup()
    # Una clave Fernet mal copiada tampoco sirve.
    bad = Settings(_env_file=None, app_env="development", jwt_secret=JWT, fernet_key="abc")
    assert [p for p in bad.startup_problems() if "FERNET_KEY" in p]


def test_desarrollo_con_claves_arranca() -> None:
    settings = Settings(_env_file=None, app_env="development", jwt_secret=JWT, fernet_key=FERNET)
    assert settings.startup_problems() == []


def test_produccion_rechaza_valores_de_ejemplo() -> None:
    settings = Settings(_env_file=None, app_env="production", jwt_secret=JWT, fernet_key=FERNET)
    problems = " | ".join(settings.startup_problems())
    for name in ("DATABASE_URL", "DATABASE_ADMIN_URL", "EVOLUTION_API_KEY"):
        assert name in problems
    assert "PANEL_URL" in problems and "PANEL_ORIGINS" in problems

    ok = Settings(
        _env_file=None,
        jwt_secret=JWT,
        fernet_key=FERNET,
        database_url="postgresql+asyncpg://saint_app:clave-real@db:5432/saint",
        **PRODUCTION,
    )
    assert ok.startup_problems() == []


async def test_la_api_no_arranca_sin_claves(migrated_database: str) -> None:
    settings = settings_with(migrated_database, jwt_secret="")
    with pytest.raises(ConfigError):
        async with client_for(settings):
            pass
