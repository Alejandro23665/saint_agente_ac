"""Configuración de la aplicación, leída de variables de entorno o del archivo .env."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    app_env: str = "development"

    # Conexión de la aplicación: usuario sin privilegios, sujeto a RLS.
    database_url: str = "postgresql+asyncpg://saint_app:saint_app@localhost:5432/saint"
    # Conexión de administración: dueño de las tablas (migraciones, tareas globales).
    database_admin_url: str = "postgresql+asyncpg://saint_owner:saint_owner@localhost:5432/saint"
    # Nombre del usuario de la app, para que la migración le dé permisos.
    app_db_user: str = "saint_app"

    redis_url: str = "redis://localhost:6379/0"

    # Evolution API. SecretStr evita que la clave aparezca si se imprime la configuración.
    evolution_base_url: str = "http://localhost:8080"
    evolution_api_key: SecretStr = SecretStr("")

    # Dirección con la que Evolution llega a esta API para mandar los webhooks.
    # En desarrollo Evolution corre dentro de Docker y la API en tu computadora:
    # host.docker.internal es "la computadora" vista desde un contenedor.
    public_webhook_base_url: str = "http://host.docker.internal:8000"

    # Clave para cifrar los tokens guardados en la base (Fernet, 32 bytes en base64).
    # Si se pierde, los tokens cifrados ya no se pueden leer.
    fernet_key: SecretStr = SecretStr("")


@lru_cache
def get_settings() -> Settings:
    return Settings()
