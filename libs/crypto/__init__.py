"""Symmetric encryption for secrets that must be stored and read back.

Deliberately tiny and dependency-free beyond `cryptography`, so any service can import
it without dragging in a database or a Redis connection at import time.
"""

from .secret_cipher import (  # noqa: F401
    SecretCipher,
    decrypt_secret,
    encrypt_secret,
    get_cipher,
    is_encrypted,
)

__all__ = [
    "SecretCipher",
    "decrypt_secret",
    "encrypt_secret",
    "get_cipher",
    "is_encrypted",
]
