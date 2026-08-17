"""
Fernet encryption for secrets at rest, shared across services.

Why this exists
---------------
`services/processing/app/v2/utils/encryption.py` already encrypts the IEX API key and the
exchange bearer token with Fernet, keyed from the `ENCRYPTION_KEY` environment variable.
The auth service needs the same thing for `users.mfa_secret` -- a TOTP secret in plaintext
base32 lets anyone with a database read mint valid second factors for that account forever.

Rather than invent a second scheme, this module reproduces the processing service's key
derivation **exactly**: same env var, same fallback password, same salt, same PBKDF2
parameters. One `ENCRYPTION_KEY` therefore covers the whole platform, and a value encrypted
by one service can be read by the other. It lives in `libs/` instead of being imported
across service boundaries, because the auth service importing from
`services.processing.app.v2` would couple two deployables that are meant to be independent.

If the two derivations ever diverge, ciphertext written by one service becomes unreadable
by the other. `libs/tests/test_secret_cipher.py` pins them together.

Reading legacy plaintext
------------------------
`decrypt_secret()` accepts a value that was never encrypted and returns it unchanged. That
is what makes the rollout safe: 30-odd accounts already hold plaintext base32 secrets, and
they must keep working from the moment the new code starts, with no migration window in
which people cannot log in. Writes always produce ciphertext, so the plaintext population
shrinks as users re-enrol; `tools/migrate_mfa_secrets.py` finishes the job in one pass.

Key management, stated plainly
------------------------------
With `ENCRYPTION_KEY` unset the key is derived from a password and salt that are literals
in this repository. That is obfuscation, not encryption -- anyone holding the source can
decrypt the database. It is inherited from the existing implementation and kept only so
this module can read what that one wrote. **Set `ENCRYPTION_KEY` in production.**
Generate one with:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

# These three literals are the compatibility contract with
# services/processing/app/v2/utils/encryption.py. Changing any of them makes every
# previously stored secret unreadable, in both services.
_FALLBACK_PASSWORD = "default_epms_encryption_password_change_in_production"
_FALLBACK_SALT = "default_salt_change_me"
_PBKDF2_ITERATIONS = 100000

# Fernet tokens are version byte 0x80 + timestamp + IV + ciphertext + HMAC, urlsafe-base64
# encoded, so they always start "gAAAAA". A base32 TOTP secret is [A-Z2-7]{32} and can
# never begin with a lowercase letter. The prefix check is only a fast path -- the real
# decision is always whether decryption succeeds.
_FERNET_PREFIX = "gAAAAA"


class SecretCipher:
    """Encrypt and decrypt short secrets, tolerating values stored before encryption."""

    def __init__(self, encryption_key: Optional[str] = None):
        self._fernet = self._build_fernet(encryption_key)

    @staticmethod
    def _build_fernet(encryption_key: Optional[str] = None) -> Fernet:
        key = encryption_key or os.getenv("ENCRYPTION_KEY")

        if key:
            try:
                key_bytes = base64.urlsafe_b64decode(key)
            except Exception as exc:  # noqa: BLE001 - surface the cause, not a stack trace
                raise ValueError(f"ENCRYPTION_KEY is not valid base64: {exc}") from exc
            if len(key_bytes) != 32:
                raise ValueError(
                    "ENCRYPTION_KEY must decode to 32 bytes "
                    f"(got {len(key_bytes)}); generate one with Fernet.generate_key()"
                )
            return Fernet(key)

        logger.warning(
            "ENCRYPTION_KEY is not set -- deriving the secret-encryption key from the "
            "default password committed to this repository. Stored secrets are obfuscated, "
            "not protected. Set ENCRYPTION_KEY before production."
        )
        password = os.getenv("ENCRYPTION_PASSWORD", _FALLBACK_PASSWORD).encode()
        salt = os.getenv("ENCRYPTION_SALT", _FALLBACK_SALT).encode()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=_PBKDF2_ITERATIONS,
            backend=default_backend(),
        )
        return Fernet(base64.urlsafe_b64encode(kdf.derive(password)))

    def encrypt(self, plaintext: Optional[str]) -> Optional[str]:
        """Encrypt, or pass through a falsy value. Already-encrypted input is returned as is."""
        if not plaintext:
            return plaintext
        if self.is_encrypted(plaintext):
            # Double encryption is unrecoverable in practice: the second decrypt returns
            # the first ciphertext, which is not the secret and does not look wrong.
            return plaintext
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: Optional[str]) -> Optional[str]:
        """
        Decrypt, or return a legacy plaintext value unchanged.

        Never raises for a value that simply was not encrypted -- that is the normal state
        of every account enrolled before this change.
        """
        if not ciphertext:
            return ciphertext
        try:
            return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except (InvalidToken, ValueError, TypeError):
            if ciphertext.startswith(_FERNET_PREFIX):
                # Shaped like ciphertext but will not open: the key changed, or the value
                # is corrupt. Returning it as "plaintext" would hand a garbage TOTP secret
                # to pyotp and fail every code with no clue why.
                logger.error(
                    "Value looks like Fernet ciphertext but could not be decrypted -- "
                    "ENCRYPTION_KEY may have changed since it was written"
                )
                raise
            return ciphertext

    def is_encrypted(self, value: Optional[str]) -> bool:
        """True only if this cipher can actually open the value."""
        if not value or not isinstance(value, str):
            return False
        if not value.startswith(_FERNET_PREFIX):
            return False
        try:
            self._fernet.decrypt(value.encode("utf-8"))
            return True
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode()


_cipher: Optional[SecretCipher] = None


def get_cipher() -> SecretCipher:
    """Process-wide cipher, built on first use so an unset key fails at use, not at import."""
    global _cipher
    if _cipher is None:
        _cipher = SecretCipher()
    return _cipher


def encrypt_secret(plaintext: Optional[str]) -> Optional[str]:
    return get_cipher().encrypt(plaintext)


def decrypt_secret(stored: Optional[str]) -> Optional[str]:
    return get_cipher().decrypt(stored)


def is_encrypted(value: Optional[str]) -> bool:
    return get_cipher().is_encrypted(value)
