"""
Unit tests for MFA Service

Several assertions here changed direction. The suite used to pin `valid_window=0` behaviour
by mocking pyotp and asserting whatever the code happened to pass; it now asserts the
configured window is honoured, which is what both the API documentation and
`settings.MFA_WINDOW` have always promised.
"""

import base64
import hashlib
import io
import re
from unittest.mock import Mock, patch

import pyotp
import qrcode
import pytest

# Import after global mocks in conftest.py
from services.auth.app.services.mfa_service import MFAService
from services.auth.app.services.mfa_rate_limiter import MFARateLimiter


class TestMFAService:
    """Test cases for MFA Service"""

    @pytest.fixture
    def mfa_service(self):
        """Create MFAService instance for testing"""
        return MFAService()

    def test_generate_secret(self, mfa_service):
        """Test TOTP secret generation"""
        secret = mfa_service.generate_secret()

        assert isinstance(secret, str)
        assert len(secret) == 32  # Base32 encoded secret should be 32 chars
        pyotp.TOTP(secret)  # raises if the secret is not valid base32

    def test_generate_secret_uniqueness(self, mfa_service):
        """Test that generated secrets are unique"""
        assert mfa_service.generate_secret() != mfa_service.generate_secret()

    @patch('pyotp.TOTP')
    def test_verify_totp_valid(self, mock_totp, mfa_service):
        """A valid code verifies, and pyotp is called with the configured parameters"""
        secret = "JBSWY3DPEHPK3PXP"
        valid_code = "123456"

        mock_totp_instance = Mock()
        mock_totp.return_value = mock_totp_instance
        mock_totp_instance.verify.return_value = True

        assert mfa_service.verify_totp(secret, valid_code) is True
        mock_totp.assert_called_once_with(
            secret,
            digits=mfa_service.digits,
            interval=mfa_service.period,
            digest=mfa_service.digest,
            name=mfa_service.issuer,
        )
        mock_totp_instance.verify.assert_called_once_with(
            valid_code, valid_window=mfa_service.window
        )

    @patch('pyotp.TOTP')
    def test_verify_totp_invalid(self, mock_totp, mfa_service):
        """Test TOTP code verification with invalid code"""
        secret = "JBSWY3DPEHPK3PXP"
        invalid_code = "000000"

        mock_totp_instance = Mock()
        mock_totp.return_value = mock_totp_instance
        mock_totp_instance.verify.return_value = False

        assert mfa_service.verify_totp(secret, invalid_code) is False
        mock_totp_instance.verify.assert_called_once_with(
            invalid_code, valid_window=mfa_service.window
        )

    def test_verify_totp_empty_secret(self, mfa_service):
        assert mfa_service.verify_totp("", "123456") is False

    def test_verify_totp_empty_code(self, mfa_service):
        assert mfa_service.verify_totp("JBSWY3DPEHPK3PXP", "") is False

    def test_verify_totp_accepts_a_real_current_code(self, mfa_service):
        """End to end against pyotp itself, no mocks: the happy path really works."""
        secret = pyotp.random_base32()
        code = pyotp.TOTP(secret).now()
        assert mfa_service.verify_totp(secret, code) is True

    def test_verify_totp_rejects_a_code_from_another_secret(self, mfa_service):
        secret = pyotp.random_base32()
        other = pyotp.TOTP(pyotp.random_base32()).now()
        assert mfa_service.verify_totp(secret, other) is False

    def test_verify_totp_tolerates_the_previous_time_step(self, mfa_service):
        """
        The point of MFA_WINDOW.

        With valid_window=0 a code typed in the last second of its 30-second step was
        rejected, because it arrived after the step had rolled. The previous step's code
        must verify.
        """
        import time

        secret = pyotp.random_base32()
        totp = pyotp.TOTP(secret)
        previous_step_code = totp.at(time.time() - mfa_service.period)
        assert mfa_service.verify_totp(secret, previous_step_code) is True

    def test_verify_totp_rejects_a_code_outside_the_window(self, mfa_service):
        import time

        secret = pyotp.random_base32()
        totp = pyotp.TOTP(secret)
        far_code = totp.at(time.time() - (mfa_service.period * 10))
        assert mfa_service.verify_totp(secret, far_code) is False

    def test_never_logs_the_code(self, mfa_service):
        """
        A TOTP code stays valid for the rest of its window, so writing it to the log at
        INFO published a live credential to whoever can read application logs.

        caplog is unusable here: the session conftest patches `logging.getLogger` for every
        test, so a handler is attached to the module's own logger instead.
        """
        import logging
        import sys

        # The package re-exports the singleton under the module's own name, so the plain
        # `from ... import mfa_service` gives the object, not the module.
        mfa_module = sys.modules["services.auth.app.services.mfa_service"]

        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = _Capture()
        module_logger = mfa_module.logger
        module_logger.addHandler(handler)
        previous_level = module_logger.level
        module_logger.setLevel(logging.DEBUG)
        try:
            secret = pyotp.random_base32()
            code = pyotp.TOTP(secret).now()
            mfa_service.verify_totp(secret, code)
        finally:
            module_logger.removeHandler(handler)
            module_logger.setLevel(previous_level)

        assert records, "expected the verification result to be logged at all"
        assert all(code not in message for message in records)

    def test_generate_qr_code(self, mfa_service):
        """Test QR code generation"""
        secret = pyotp.random_base32()
        qr_code_data = mfa_service.generate_qr_code(secret, "testuser@example.com")

        assert isinstance(qr_code_data, str)
        assert qr_code_data.startswith("data:image/png;base64,")
        base64.b64decode(qr_code_data.split(",")[1])  # raises if not valid base64

    def test_provisioning_uri_is_unchanged_by_the_explicit_digest(self, mfa_service):
        """
        MFA_ALGORITHM is now passed to pyotp instead of being read and dropped. That must
        not alter the enrolment URI, or every authenticator app already holding an EPMS
        account would need re-enrolling.
        """
        secret = "JBSWY3DPEHPK3PXP"
        explicit = pyotp.TOTP(
            secret, digits=mfa_service.digits, interval=mfa_service.period,
            digest=mfa_service.digest, name=mfa_service.issuer,
        ).provisioning_uri(name="u@example.com", issuer_name=mfa_service.issuer)
        implicit = pyotp.TOTP(
            secret, digits=mfa_service.digits, interval=mfa_service.period,
            name=mfa_service.issuer,
        ).provisioning_uri(name="u@example.com", issuer_name=mfa_service.issuer)
        assert explicit == implicit

    def test_digest_matches_configured_algorithm(self, mfa_service):
        assert mfa_service.algorithm == "SHA1"
        assert mfa_service.digest is hashlib.sha1

    def test_unknown_algorithm_falls_back_to_sha1(self, mfa_service):
        mfa_service.algorithm = "SHA3-512-NOPE"
        assert mfa_service.digest is hashlib.sha1

    def test_get_remaining_time(self, mfa_service):
        remaining_time = mfa_service.get_remaining_time()
        assert isinstance(remaining_time, int)
        assert 0 <= remaining_time <= 30

    def test_get_remaining_time_consistency(self, mfa_service):
        assert abs(mfa_service.get_remaining_time() - mfa_service.get_remaining_time()) <= 1

    def test_mfa_service_initialization(self, mfa_service):
        assert mfa_service is not None
        for attribute in ("generate_secret", "verify_totp", "generate_qr_code",
                          "get_remaining_time", "generate_totp"):
            assert hasattr(mfa_service, attribute)

    def test_secret_format(self, mfa_service):
        assert re.match(r'^[A-Z2-7]+$', mfa_service.generate_secret())

    def test_generate_totp_code(self, mfa_service):
        code = mfa_service.generate_totp(pyotp.random_base32())
        assert isinstance(code, str)
        assert len(code) == mfa_service.digits
        assert code.isdigit()

    def test_configurable_issuer_from_app_name(self, mfa_service):
        assert mfa_service.issuer == "EPMS"

    def test_replay_window_covers_the_whole_acceptance_span(self, mfa_service):
        """
        valid_window=W accepts W steps either side of now, so a code is live for (2W+1)
        periods and must stay burnt for at least that long.
        """
        assert mfa_service.replay_window_seconds == mfa_service.period * (
            2 * mfa_service.window + 1
        )

    @patch('qrcode.QRCode')
    def test_qr_code_generation_parameters(self, mock_qrcode, mfa_service):
        """Test QR code generation with correct parameters"""
        mock_qr_instance = Mock()
        mock_qrcode.return_value = mock_qr_instance
        mock_qr_instance.make_image.return_value = Mock()

        with patch('io.BytesIO', return_value=Mock()):
            with patch('base64.b64encode', return_value=b'base64data'):
                mfa_service.generate_qr_code("JBSWY3DPEHPK3PXP", "testuser@example.com")

                mock_qrcode.assert_called_once_with(
                    version=1,
                    error_correction=qrcode.ERROR_CORRECT_L,
                    box_size=10,
                    border=4,
                )


class TestVerifyTotpWithRateLimit:
    """
    The funnel every TOTP check goes through: /mfa/verify, /mfa/disable and
    /auth/verify-login all call this one method.
    """

    @pytest.fixture
    def mfa_service(self):
        return MFAService()

    @pytest.fixture
    def limiter(self, monkeypatch):
        """A fresh in-process limiter, swapped in for the module-level singleton."""
        limiter = MFARateLimiter(max_attempts=5, window_minutes=15, lockout_minutes=30,
                                 connect=False)
        monkeypatch.setattr(
            "services.auth.app.services.mfa_rate_limiter.mfa_rate_limiter", limiter
        )
        return limiter

    def test_six_correct_codes_in_a_row_all_succeed(self, mfa_service, limiter):
        """
        The regression for the defect that made MFA unusable.

        Successes used to be counted toward the attempt budget, so the fifth MFA action of
        any kind inside fifteen minutes was refused -- and because enrolment consumed one,
        a freshly enrolled user got four logins before a *correct* code was rejected for
        thirty minutes with the message "Invalid temporary token or TOTP code".
        """
        secret = pyotp.random_base32()
        totp = pyotp.TOTP(secret)

        for attempt in range(6):
            # A distinct code each time: replay protection burns a used code, so reusing
            # one would be refused for a different and correct reason.
            code = totp.at(1_000_000 + attempt * 30)
            with patch.object(mfa_service, "verify_totp", return_value=True):
                result = mfa_service.verify_totp_with_rate_limit("user-1", secret, code)
            assert result["verified"] is True, f"login {attempt + 1} was refused"
            assert result["rate_limited"] is False

        assert limiter.is_locked_out("user-1") is False
        assert limiter.get_attempt_count("user-1") == 0

    def test_a_used_code_cannot_be_presented_twice(self, mfa_service, limiter):
        """
        pyotp has no replay protection: a code stayed valid for the rest of its acceptance
        window however many times it was shown. Observed on the running service, where one
        code authenticated four separate logins.
        """
        secret = pyotp.random_base32()
        code = pyotp.TOTP(secret).now()

        first = mfa_service.verify_totp_with_rate_limit("user-2", secret, code)
        assert first["verified"] is True

        second = mfa_service.verify_totp_with_rate_limit("user-2", secret, code)
        assert second["verified"] is False
        assert second["replayed"] is True

    def test_replay_is_scoped_to_one_user(self, mfa_service, limiter):
        """Two users can hold the same code; burning one must not burn the other."""
        secret = pyotp.random_base32()
        code = pyotp.TOTP(secret).now()

        assert mfa_service.verify_totp_with_rate_limit("a", secret, code)["verified"] is True
        assert mfa_service.verify_totp_with_rate_limit("b", secret, code)["verified"] is True

    def test_five_failures_lock_the_account(self, mfa_service, limiter):
        secret = pyotp.random_base32()

        for _ in range(4):
            result = mfa_service.verify_totp_with_rate_limit("user-3", secret, "000000")
            assert result["verified"] is False
            assert result["rate_limited"] is False

        result = mfa_service.verify_totp_with_rate_limit("user-3", secret, "000000")
        assert result["rate_limited"] is True
        assert limiter.is_locked_out("user-3") is True

    def test_blocked_attempt_reports_a_wait_rather_than_crashing(self, mfa_service, limiter):
        """
        The refusal path used to dereference `lockout_info['remaining_seconds']` when
        `get_lockout_info` had returned None -- TypeError, HTTP 500 on /mfa/verify, and a
        misleading 401 on /auth/verify-login.
        """
        secret = pyotp.random_base32()
        for _ in range(5):
            mfa_service.verify_totp_with_rate_limit("user-4", secret, "000000")

        result = mfa_service.verify_totp_with_rate_limit("user-4", secret, "000000")
        assert result["rate_limited"] is True
        assert result["verified"] is False
        assert "seconds" in result["message"]

    def test_a_success_clears_earlier_failures(self, mfa_service, limiter):
        secret = pyotp.random_base32()
        for _ in range(3):
            mfa_service.verify_totp_with_rate_limit("user-5", secret, "000000")
        assert limiter.get_attempt_count("user-5") == 3

        code = pyotp.TOTP(secret).now()
        assert mfa_service.verify_totp_with_rate_limit("user-5", secret, code)["verified"] is True
        assert limiter.get_attempt_count("user-5") == 0
