"""Configuración de la aplicación, leída de variables de entorno o del archivo .env."""

from functools import lru_cache
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    # "development" en tu PC; cualquier servidor real es "production". Si falta en el .env
    # se asume producción: es lo seguro (sin /docs y con la cookie solo por HTTPS).
    app_env: Literal["development", "production"] = "production"

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
    # Dirección del panel con la que se arman los enlaces de invitación (bloque 5.2).
    panel_url: str = "http://127.0.0.1:5173"
    # OpenRouter: convierte el conocimiento del negocio en embeddings (bloque 4.4).
    openrouter_api_key: SecretStr = SecretStr("")
    embedding_model: str = "nvidia/nemotron-3.5-embed-8b:free"

    # Precio del modelo de IA en dólares por millón de tokens, para estimar el costo en la
    # pantalla Consumo (bloque 6.10). Sin precios, el panel muestra los tokens y no el costo:
    # Saint no inventa tarifas. Revisa los precios vigentes de tu proveedor.
    llm_input_usd_per_million: float | None = None
    llm_output_usd_per_million: float | None = None

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    def startup_problems(self) -> list[str]:
        """Lo que impide arrancar la API o el worker con esta configuración.

        Así una clave que falta se ve al arrancar, con un mensaje claro, y no como un
        error 500 la primera vez que alguien inicia sesión o conecta un número.
        """
        problems = []
        if len(self.jwt_secret.get_secret_value()) < MIN_JWT_SECRET_LENGTH:
            problems.append(
                f"JWT_SECRET falta o tiene menos de {MIN_JWT_SECRET_LENGTH} caracteres. "
                'Genera una con: python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        fernet_key = self.fernet_key.get_secret_value()
        try:
            Fernet(fernet_key.encode())
        except ValueError:
            problems.append(
                "FERNET_KEY falta o no es válida. Genera una con: python -c "
                '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )
        if self.is_development:
            return problems

        # En producción, además, nada de valores de ejemplo ni direcciones sin HTTPS.
        for name in ("database_url", "database_admin_url"):
            url = getattr(self, name)
            if url == Settings.model_fields[name].default or "cambia-esta-clave" in url:
                problems.append(
                    f"{name.upper()} usa la contraseña de ejemplo: pon la real en el .env"
                )
        if not self.evolution_api_key.get_secret_value():
            problems.append("Falta EVOLUTION_API_KEY")
        if not self.panel_url.startswith("https://"):
            problems.append("PANEL_URL debe empezar por https:// en producción")
        origins = [o.strip() for o in self.panel_origins.split(",") if o.strip()]
        if not origins or any(not o.startswith("https://") for o in origins):
            problems.append("PANEL_ORIGINS debe tener solo direcciones https:// en producción")
        return problems

    def check_startup(self) -> None:
        """Detiene el arranque si la configuración no sirve (ver startup_problems)."""
        problems = self.startup_problems()
        if problems:
            lines = "\n".join(f"  - {p}" for p in problems)
            raise ConfigError(
                f"Saint no puede arrancar (APP_ENV={self.app_env}). Revisa el .env:\n{lines}"
            )


# Igual que auth_tokens.MIN_SECRET_LENGTH.
MIN_JWT_SECRET_LENGTH = 32


class ConfigError(RuntimeError):
    """Configuración incompleta o insegura para arrancar."""


@lru_cache
def get_settings() -> Settings:
    return Settings()
