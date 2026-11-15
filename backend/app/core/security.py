"""Cifrado de datos guardados y manejo de secretos.

- Cifrar (Fernet): para lo que hay que volver a leer, como el token de cada número
  en Evolution. Sin FERNET_KEY nadie puede leerlo, ni con una copia de la base.
- Hash (SHA-256): para lo que solo hay que comprobar, como el secreto del webhook.
  La base guarda la huella; ni siquiera Saint puede recuperar el secreto original.
"""

import hashlib
import hmac
import secrets
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class DecryptionError(Exception):
    """El dato no se pudo descifrar: FERNET_KEY distinta o dato alterado."""


@lru_cache
def _fernet(key: str) -> Fernet:
    try:
        return Fernet(key.encode())
    except ValueError as exc:
        raise ValueError(
            "FERNET_KEY no es válida. Genera una con: python -c "
            '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        ) from exc


def _current_fernet(key: str | None) -> Fernet:
    key = key or get_settings().fernet_key.get_secret_value()
    if not key:
        raise ValueError("Falta FERNET_KEY en el .env")
    return _fernet(key)


def encrypt(plain: str, *, key: str | None = None) -> bytes:
    return _current_fernet(key).encrypt(plain.encode())


def decrypt(token: bytes, *, key: str | None = None) -> str:
    try:
        return _current_fernet(key).decrypt(token).decode()
    except InvalidToken as exc:
        raise DecryptionError("No se pudo descifrar el dato") from exc


def new_secret() -> str:
    """Secreto aleatorio de 256 bits, apto para URL y cabeceras."""
    return secrets.token_urlsafe(32)


def hash_secret(secret: str) -> str:
    # SHA-256 basta (sin sal ni bcrypt) porque el secreto es aleatorio y largo:
    # no hay diccionario con el que adivinarlo, a diferencia de una contraseña humana.
    return hashlib.sha256(secret.encode()).hexdigest()


def verify_secret(secret: str, expected_hash: str) -> bool:
    # compare_digest tarda lo mismo acierte o falle: no da pistas por el tiempo.
    return hmac.compare_digest(hash_secret(secret), expected_hash)
