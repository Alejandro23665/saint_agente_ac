"""Cifrado del token y huella del secreto del webhook."""

import pytest
from cryptography.fernet import Fernet

from app.core.security import (
    DecryptionError,
    decrypt,
    encrypt,
    hash_secret,
    new_secret,
    verify_secret,
)


def test_cifrar_y_descifrar() -> None:
    key = Fernet.generate_key().decode()
    token = encrypt("token-de-evolution", key=key)
    assert b"token-de-evolution" not in token  # en la base solo quedan bytes ilegibles
    assert decrypt(token, key=key) == "token-de-evolution"


def test_otra_clave_no_descifra() -> None:
    token = encrypt("x", key=Fernet.generate_key().decode())
    with pytest.raises(DecryptionError):
        decrypt(token, key=Fernet.generate_key().decode())


def test_sin_clave_o_clave_invalida() -> None:
    with pytest.raises(ValueError, match="FERNET_KEY no es válida"):
        encrypt("x", key="corta")


def test_secreto_y_su_huella() -> None:
    secret = new_secret()
    assert len(secret) >= 43  # 32 bytes aleatorios
    assert new_secret() != secret
    digest = hash_secret(secret)
    assert secret not in digest
    assert verify_secret(secret, digest)
    assert not verify_secret(secret + "x", digest)
