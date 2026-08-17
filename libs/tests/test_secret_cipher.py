"""
Tests for libs.crypto.secret_cipher.

Two things are being protected here. The obvious one is that encryption round-trips. The
one that will actually bite someone is compatibility: this module deliberately reproduces
the key derivation in `services/processing/app/v2/utils/encryption.py`, and if the two drift
apart, ciphertext written by one service silently stops opening in the other.
"""

import base64
import importlib
import os
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from libs.crypto.secret_cipher import SecretCipher


@pytest.fixture
def cipher():
    return SecretCipher(encryption_key=Fernet.generate_key().decode())


class TestRoundTrip:
    def test_encrypt_then_decrypt(self, cipher):
        assert cipher.decrypt(cipher.encrypt("JBSWY3DPEHPK3PXP")) == "JBSWY3DPEHPK3PXP"

    def test_ciphertext_does_not_contain_the_plaintext(self, cipher):
        secret = "JBSWY3DPEHPK3PXP"
        assert secret not in cipher.encrypt(secret)

    def test_encryption_is_not_deterministic(self, cipher):
        """Fernet carries a random IV, so the same secret must not produce the same token."""
        assert cipher.encrypt("SAMESECRET") != cipher.encrypt("SAMESECRET")

    def test_empty_and_none_pass_through(self, cipher):
        assert cipher.encrypt(None) is None
        assert cipher.encrypt("") == ""
        assert cipher.decrypt(None) is None
        assert cipher.decrypt("") == ""


class TestLegacyPlaintext:
    """
    What makes the deploy safe: accounts enrolled before encryption hold plaintext base32
    and must keep working with no migration window.
    """

    def test_a_plaintext_secret_decrypts_to_itself(self, cipher):
        assert cipher.decrypt("JBSWY3DPEHPK3PXP") == "JBSWY3DPEHPK3PXP"

    def test_plaintext_is_not_reported_as_encrypted(self, cipher):
        assert cipher.is_encrypted("JBSWY3DPEHPK3PXP") is False

    def test_ciphertext_is_reported_as_encrypted(self, cipher):
        assert cipher.is_encrypted(cipher.encrypt("JBSWY3DPEHPK3PXP")) is True

    def test_encrypting_ciphertext_is_a_no_op(self, cipher):
        """
        Double encryption is unrecoverable in practice: the next decrypt returns the inner
        ciphertext, which is not the secret and does not look wrong. The migration is
        re-runnable because of this.
        """
        once = cipher.encrypt("JBSWY3DPEHPK3PXP")
        assert cipher.encrypt(once) == once

    def test_a_value_that_looks_like_ciphertext_but_will_not_open_raises(self, cipher):
        """
        Better a loud failure than handing pyotp a garbage secret and rejecting every code
        the user types with no explanation. This is the shape of a key rotation gone wrong.
        """
        written_with_another_key = SecretCipher(
            encryption_key=Fernet.generate_key().decode()
        ).encrypt("JBSWY3DPEHPK3PXP")

        with pytest.raises(Exception):
            cipher.decrypt(written_with_another_key)


class TestKeyMaterial:
    def test_an_explicit_key_is_used(self):
        key = Fernet.generate_key().decode()
        assert SecretCipher(encryption_key=key).decrypt(
            Fernet(key.encode()).encrypt(b"hello").decode()
        ) == "hello"

    def test_a_short_key_is_rejected(self):
        short = base64.urlsafe_b64encode(b"tooshort").decode()
        with pytest.raises(ValueError, match="32 bytes"):
            SecretCipher(encryption_key=short)

    def test_a_non_base64_key_is_rejected(self):
        with pytest.raises(ValueError):
            SecretCipher(encryption_key="not base64 at all !!!")

    def test_the_environment_key_is_picked_up(self):
        key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {"ENCRYPTION_KEY": key}):
            assert SecretCipher().decrypt(
                Fernet(key.encode()).encrypt(b"env").decode()
            ) == "env"

    def test_generate_key_produces_a_usable_key(self):
        SecretCipher(encryption_key=SecretCipher.generate_key())


class TestProcessingServiceCompatibility:
    """
    One ENCRYPTION_KEY must serve the whole platform. If these constants drift, the auth
    service and the processing service stop being able to read each other's secrets --
    silently, and only for values written before the drift.
    """

    def test_fallback_derivation_matches_the_processing_service(self):
        from libs.crypto import secret_cipher

        processing = importlib.import_module(
            "services.processing.app.v2.utils.encryption"
        )

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENCRYPTION_KEY", None)
            ours = SecretCipher()
            theirs = processing.EncryptionService()

            # The real assertion: a value one encrypts, the other decrypts.
            assert theirs.decrypt(ours.encrypt("SHAREDSECRET")) == "SHAREDSECRET"
            assert ours.decrypt(theirs.encrypt("SHAREDSECRET")) == "SHAREDSECRET"

    def test_explicit_key_is_interchangeable_with_the_processing_service(self):
        processing = importlib.import_module(
            "services.processing.app.v2.utils.encryption"
        )
        key = Fernet.generate_key().decode()

        with patch.dict(os.environ, {"ENCRYPTION_KEY": key}):
            ours = SecretCipher()
            theirs = processing.EncryptionService()
            assert theirs.decrypt(ours.encrypt("SHAREDSECRET")) == "SHAREDSECRET"
            assert ours.decrypt(theirs.encrypt("SHAREDSECRET")) == "SHAREDSECRET"
