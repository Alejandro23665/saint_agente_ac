"""Cliente de Evolution API: crear números, pedir el QR, ver el estado y borrarlos.

Ejemplo:
    async with EvolutionClient.from_settings() as evo:
        nuevo = await evo.create_instance("saint-panaderia-01")
        qr = await evo.get_qr(nuevo.name)
"""

import secrets
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self
from urllib.parse import quote

import httpx

from app.core.config import Settings, get_settings
from app.integrations.evolution.schemas import (
    ConnectionState,
    ConnectionStateResponse,
    CreateInstanceResponse,
    InstanceInfo,
    QrCode,
)

# Crear una instancia tarda ~5 s: Evolution espera a que Baileys genere el primer QR.
DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=5.0)


# Eventos que Saint pide a Evolution. Los demás (contactos, chats, presencia...) no se
# usan y solo generarían tráfico.
WEBHOOK_EVENTS = (
    "MESSAGES_UPSERT",  # mensaje nuevo (entrante o enviado desde el teléfono)
    "MESSAGES_UPDATE",  # cambio de estado: entregado, leído
    "SEND_MESSAGE",  # confirmación de un mensaje que enviamos por la API
    "CONNECTION_UPDATE",  # el número se conectó, se desconectó o fue bloqueado
    "QRCODE_UPDATED",  # hay un QR nuevo para escanear
)


@dataclass(frozen=True)
class WebhookConfig:
    url: str
    # Cabeceras que Evolution manda en cada webhook; ahí va nuestro secreto.
    headers: dict[str, str]
    events: tuple[str, ...] = WEBHOOK_EVENTS

    def as_body(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "url": self.url,
            "headers": self.headers,
            "events": list(self.events),
            # Un solo endpoint para todos los eventos (no /messages-upsert, etc.).
            "byEvents": False,
            # Sin archivos en base64 dentro del webhook: pesan mucho; se descargan aparte.
            "base64": False,
        }


class EvolutionError(Exception):
    """Evolution respondió con error o no respondió."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class CreatedInstance:
    name: str
    instance_id: str | None
    # Token propio de la instancia. Se guardará cifrado en channel_instances (bloque 3.3).
    token: str
    status: str | None
    qr: QrCode | None


class EvolutionClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ) -> None:
        if not api_key:
            raise ValueError("Falta EVOLUTION_API_KEY en el .env")
        # transport permite a las pruebas simular Evolution sin levantarlo.
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"apikey": api_key},
            timeout=timeout,
            transport=transport,
        )

    @classmethod
    def from_settings(cls, settings: Settings | None = None, **kwargs: Any) -> Self:
        settings = settings or get_settings()
        return cls(
            settings.evolution_base_url, settings.evolution_api_key.get_secret_value(), **kwargs
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- Operaciones -----------------------------------------------------------------

    async def create_instance(
        self,
        name: str,
        *,
        groups_enabled: bool = False,
        token: str | None = None,
        webhook: WebhookConfig | None = None,
    ) -> CreatedInstance:
        """Crea un número nuevo (Baileys) y devuelve su primer QR.

        Con webhook, Evolution queda apuntando a Saint desde el primer evento.
        """
        token = token or secrets.token_urlsafe(32)
        body = {
            "instanceName": name,
            "integration": "WHATSAPP-BAILEYS",
            "qrcode": True,
            # Elegimos nosotros el token, para no depender de que Evolution lo devuelva.
            "token": token,
            # Grupos apagados por defecto: Evolution ni siquiera nos manda sus mensajes.
            "groupsIgnore": not groups_enabled,
            "rejectCall": False,
            "alwaysOnline": False,
            "readMessages": False,
            "readStatus": False,
            "syncFullHistory": False,
        }
        if webhook is not None:
            body["webhook"] = webhook.as_body()
        data = await self._request("POST", "/instance/create", json=body)
        parsed = CreateInstanceResponse.model_validate(data)
        qr = parsed.qrcode if parsed.qrcode and not parsed.qrcode.is_empty else None
        return CreatedInstance(
            name=parsed.instance.instance_name,
            instance_id=parsed.instance.instance_id,
            token=token,
            status=parsed.instance.status,
            qr=qr,
        )

    async def get_qr(self, name: str, *, phone_number: str | None = None) -> QrCode | None:
        """Pide un QR nuevo. Devuelve None si el número ya está conectado.

        Con phone_number (solo dígitos, con código de país) Evolution también genera un
        código de vinculación para escribir en el teléfono en vez de escanear.
        """
        params = {"number": phone_number} if phone_number else None
        data = await self._request("GET", f"/instance/connect/{_path(name)}", params=params)
        # Si ya está conectado, Evolution responde con el estado en vez del QR.
        if isinstance(data, dict) and "instance" in data:
            return None
        qr = QrCode.model_validate(data)
        return None if qr.is_empty else qr

    async def connection_state(self, name: str) -> ConnectionState:
        data = await self._request("GET", f"/instance/connectionState/{_path(name)}")
        state = ConnectionStateResponse.model_validate(data).instance.state
        if state not in ("open", "connecting", "close"):
            raise EvolutionError(f"Estado desconocido de Evolution: {state!r}")
        return state  # type: ignore[return-value]

    async def set_webhook(self, name: str, webhook: WebhookConfig) -> None:
        """Cambia a dónde manda Evolution los eventos de un número (ej. nuevo dominio)."""
        await self._request(
            "POST", f"/webhook/set/{_path(name)}", json={"webhook": webhook.as_body()}
        )

    async def fetch_instance(self, name: str) -> InstanceInfo:
        """Datos de la instancia, incluido el número de teléfono una vez vinculado."""
        data = await self._request("GET", "/instance/fetchInstances", params={"instanceName": name})
        if not data:
            raise EvolutionError(f"Evolution no encontró la instancia {name!r}", 404)
        return InstanceInfo.model_validate(data[0])

    async def logout(self, name: str) -> None:
        """Desvincula el teléfono; la instancia queda creada y puede pedir otro QR."""
        await self._request("DELETE", f"/instance/logout/{_path(name)}")

    async def delete_instance(self, name: str) -> None:
        """Desvincula (si hacía falta) y borra la instancia de Evolution."""
        await self._request("DELETE", f"/instance/delete/{_path(name)}")

    # --- Interno ---------------------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise EvolutionError(f"No se pudo contactar a Evolution: {exc}") from exc
        if response.is_error:
            raise EvolutionError(
                f"Evolution respondió {response.status_code}: {_error_message(response)}",
                status_code=response.status_code,
            )
        return response.json() if response.content else None


def _path(name: str) -> str:
    # El nombre va dentro de la URL: se codifica para que "/" o espacios no la rompan.
    return quote(name, safe="")


def _error_message(response: httpx.Response) -> str:
    """Saca el mensaje de error de Evolution, que viene como {response: {message: [...]}}."""
    try:
        data = response.json()
    except ValueError:
        return response.text[:200]
    message = data.get("response", {}).get("message") if isinstance(data, dict) else None
    if isinstance(message, list):
        message = "; ".join(str(m) for m in message)
    return str(message or data)[:200]
