"""Forma de las respuestas de Evolution API v2.3.7 que usamos.

Solo se declaran los campos que Saint necesita; el resto se ignora. Si Evolution cambia
un campo en otra versión, falla aquí, en un solo lugar, con un error claro.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Estados de conexión que devuelve Evolution (Baileys).
ConnectionState = Literal["open", "connecting", "close"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class QrCode(_Base):
    """QR para vincular el teléfono.

    code: el texto del QR. base64: la imagen PNG lista para mostrar
    (data:image/png;base64,...). pairing_code: código de 8 letras para vincular sin
    cámara, solo si se pidió con un número. count: cuántos QR se generaron ya.
    """

    code: str | None = None
    base64: str | None = None
    pairing_code: str | None = Field(default=None, alias="pairingCode")
    count: int | None = None

    @property
    def is_empty(self) -> bool:
        return not (self.code or self.base64 or self.pairing_code)


class _InstanceInfo(_Base):
    instance_name: str = Field(alias="instanceName")
    instance_id: str | None = Field(default=None, alias="instanceId")
    status: str | None = None
    state: str | None = None


class CreateInstanceResponse(_Base):
    instance: _InstanceInfo
    hash: str | None = None
    qrcode: QrCode | None = None


class ConnectionStateResponse(_Base):
    instance: _InstanceInfo


class InstanceInfo(_Base):
    """Datos de una instancia según /instance/fetchInstances."""

    name: str
    connection_status: str | None = Field(default=None, alias="connectionStatus")
    # JID del teléfono vinculado, ej. "584121234567@s.whatsapp.net".
    owner_jid: str | None = Field(default=None, alias="ownerJid")
    profile_name: str | None = Field(default=None, alias="profileName")

    @property
    def phone_number(self) -> str | None:
        """Número en formato internacional (+584121234567), sacado del JID."""
        if not self.owner_jid:
            return None
        digits = self.owner_jid.split("@", 1)[0].split(":", 1)[0]
        return f"+{digits}" if digits.isdigit() else None


class _MessageKey(_Base):
    id: str
    remote_jid: str | None = Field(default=None, alias="remoteJid")


class SentMessage(_Base):
    """Respuesta de /message/sendText: el id que WhatsApp le dio al mensaje."""

    key: _MessageKey
    status: str | None = None

    @property
    def message_id(self) -> str:
        return self.key.id
