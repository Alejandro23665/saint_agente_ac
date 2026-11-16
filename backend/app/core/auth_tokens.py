"""Contraseñas y pases de sesión del panel.

- Contraseñas: se guardan con argon2, un hash lento a propósito. Si alguien roba la base,
  adivinar cada contraseña le cuesta muchísimo. Nunca se guarda la contraseña.
- Pase de acceso (JWT, 15 minutos): lo manda el panel en cada petición. Lleva quién es
  la persona, en qué negocio está trabajando y con qué rol. Está firmado con JWT_SECRET:
  si alguien cambia una letra, la firma deja de valer.
- Pase de renovación (7 días): un texto aleatorio que vive en una cookie que el navegador
  no deja leer al JavaScript. Sirve para pedir un pase de acceso nuevo sin escribir la
  contraseña. La base guarda solo su huella (ver services/auth.py).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings

ACCESS_TOKEN_MINUTES = 15
REFRESH_TOKEN_DAYS = 7
ALGORITHM = "HS256"
MIN_SECRET_LENGTH = 32
MIN_PASSWORD_LENGTH = 10

_hasher = PasswordHasher()
# Hash de una contraseña cualquiera: cuando el correo no existe se verifica contra él, para
# que la respuesta tarde lo mismo y no delate qué correos están registrados.
_DUMMY_HASH = _hasher.hash("contraseña-que-nadie-usa")


class InvalidTokenError(Exception):
    """El pase no es válido: vencido, alterado o de otro tipo."""


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """True si el hash se hizo con parámetros más débiles que los actuales."""
    return _hasher.check_needs_rehash(password_hash)


def _secret(secret: str | None) -> str:
    secret = secret or get_settings().jwt_secret.get_secret_value()
    if len(secret) < MIN_SECRET_LENGTH:
        raise ValueError(
            "JWT_SECRET falta o es muy corta. Genera una con: "
            'python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    return secret


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    tenant_id: uuid.UUID | None  # None = todavía no eligió negocio
    role: str | None
    expires_at: datetime


def create_access_token(
    user_id: uuid.UUID,
    tenant_id: uuid.UUID | None = None,
    role: str | None = None,
    *,
    now: datetime | None = None,
    secret: str | None = None,
) -> tuple[str, datetime]:
    """Devuelve el pase y cuándo vence."""
    now = now or datetime.now(UTC)
    expires_at = now + timedelta(minutes=ACCESS_TOKEN_MINUTES)
    payload = {
        "sub": str(user_id),
        "typ": "access",
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    if tenant_id is not None:
        payload["tid"] = str(tenant_id)
        payload["role"] = role
    return jwt.encode(payload, _secret(secret), algorithm=ALGORITHM), expires_at


def decode_access_token(token: str, *, secret: str | None = None) -> AccessClaims:
    try:
        # algorithms fijo: no se acepta un pase que diga "sin firma" (alg none) ni otro tipo.
        payload = jwt.decode(
            token,
            _secret(secret),
            algorithms=[ALGORITHM],
            options={"require": ["exp", "sub", "typ"]},
        )
        if payload["typ"] != "access":
            raise InvalidTokenError("No es un pase de acceso")
        return AccessClaims(
            user_id=uuid.UUID(payload["sub"]),
            tenant_id=uuid.UUID(payload["tid"]) if payload.get("tid") else None,
            role=payload.get("role"),
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
        )
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise InvalidTokenError(str(exc)) from exc
