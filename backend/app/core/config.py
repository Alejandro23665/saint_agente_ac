"""Configuración de la aplicación, leída de variables de entorno o del archivo .env."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    app_env: str = "development"

    # Conexión de la aplicación: usuario sin privilegios, sujeto a RLS.
    database_url: str = "postgresql+asyncpg://saint_app:saint_app@127.0.0.1:5432/saint"
    # Conexión de administración: dueño de las tablas (migraciones, tareas globales).
    database_admin_url: str = "postgresql+asyncpg://saint_owner:saint_owner@127.0.0.1:5432/saint"
    # Nombre del usuario de la app, para que la migración le dé permisos.
    app_db_user: str = "saint_app"

    redis_url: str = "redis://127.0.0.1:6379/0"

    # Evolution API. SecretStr evita que la clave aparezca si se imprime la configuración.
    evolution_base_url: str = "http://127.0.0.1:8080"
    evolution_api_key: SecretStr = SecretStr("")

    # Dirección con la que Evolution llega a esta API para mandar los webhooks.
    # En desarrollo Evolution corre dentro de Docker y la API en tu computadora:
    # host.docker.internal es "la computadora" vista desde un contenedor.
    public_webhook_base_url: str = "http://host.docker.internal:8000"

    # Clave para cifrar los tokens guardados en la base (Fernet, 32 bytes en base64).
    # Si se pierde, los tokens cifrados ya no se pueden leer.
    fernet_key: SecretStr = SecretStr("")

    # Solo para pruebas: si tiene teléfonos (separados por coma), el bot contesta
    # únicamente a esos números. Vacío = contesta a todos los clientes.
    bot_test_phones: str = ""

    # Clave de Anthropic (modelo de IA del bot). Sin clave, el bot contesta con un eco.
    anthropic_api_key: SecretStr = SecretStr("")

    # Inicio de sesión del panel (bloque 5.1). JWT_SECRET firma los pases de 15 minutos:
    # quien la tenga puede fabricar pases, así que va solo en el .env (32+ caracteres).
    jwt_secret: SecretStr = SecretStr("")
    # Direcciones desde las que el panel web llama a la API (separadas por coma).
    panel_origins: str = "http://127.0.0.1:5173,http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
