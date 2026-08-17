"""
MFA Service for TOTP-based two-factor authentication
"""

import base64
import hashlib
import io
import logging
from typing import Dict, Optional

import pyotp
import qrcode

from ..core.config import settings

logger = logging.getLogger(__name__)

# pyotp takes a hashlib constructor, not a name. Only SHA1 is universally supported by
# authenticator apps; the others are here so MFA_ALGORITHM is honoured rather than ignored.
_DIGESTS = {
    "SHA1": hashlib.sha1,
    "SHA256": hashlib.sha256,
    "SHA512": hashlib.sha512,
}


class MFAService:
    """MFA service for TOTP authentication"""

    def __init__(self):
        # Use APP_NAME as issuer if available, otherwise fall back to MFA_ISSUER
        self.issuer = settings.APP_NAME or settings.MFA_ISSUER
        self.algorithm = settings.MFA_ALGORITHM
        self.digits = settings.MFA_DIGITS
        self.period = settings.MFA_PERIOD
        # Clock-skew tolerance, in TOTP periods either side of now. It was read from config
        # and then never passed to pyotp, which hardcoded valid_window=0 -- so a code typed
        # in the last second of its window was rejected while both the API docs and this
        # setting promised +/-1 period.
        self.window = settings.MFA_WINDOW

    @property
    def digest(self):
        """hashlib constructor for MFA_ALGORITHM, defaulting to SHA1."""
        name = (self.algorithm or "SHA1").upper()
        if name not in _DIGESTS:
            logger.warning("Unsupported MFA_ALGORITHM %r, using SHA1", self.algorithm)
            return hashlib.sha1
        return _DIGESTS[name]

    @property
    def replay_window_seconds(self) -> int:
        """
        How long a code stays acceptable, and therefore how long it must stay burnt.

        valid_window=W accepts W periods either side of now, so the acceptance span is
        (2W+1) periods -- 90 seconds at the defaults.
        """
        return int(self.period * (2 * self.window + 1))

    def _totp(self, secret: str) -> pyotp.TOTP:
        return pyotp.TOTP(
            secret,
            digits=self.digits,
            interval=self.period,
            digest=self.digest,
            name=self.issuer,
        )

    def generate_secret(self) -> str:
        """Generate a new TOTP secret"""
        return pyotp.random_base32()

    def generate_totp(self, secret: str) -> str:
        """Generate current TOTP code"""
        return self._totp(secret).now()

    def verify_totp(self, secret: str, code: str) -> bool:
        """Verify a TOTP code, allowing the configured clock-skew window."""
        if not secret or not code:
            logger.warning("TOTP verification failed: empty secret or code")
            return False

        try:
            result = self._totp(secret).verify(code, valid_window=self.window)
            # The code itself is never logged: it is a live credential for the rest of its
            # window, and application logs are read by more people than the database is.
            logger.debug("TOTP verification result: %s", result)
            return result
        except Exception as e:
            logger.error(f"TOTP verification error: {e}")
            return False

    def verify_totp_with_rate_limit(self, user_id: str, secret: str, code: str) -> Dict:
        """
        Verify a TOTP code with rate limiting and single-use enforcement.

        Args:
            user_id: User identifier for rate limiting
            secret: TOTP secret (already decrypted)
            code: TOTP code to verify

        Returns:
            Dict with verification result and rate limit info. `rate_limited` is True only
            when the attempt was refused without being checked, or when this attempt
            triggered a lockout.
        """
        from .mfa_rate_limiter import mfa_rate_limiter

        # Check if user can attempt
        if not mfa_rate_limiter.can_attempt(user_id):
            lockout_info = mfa_rate_limiter.get_lockout_info(user_id)
            # get_lockout_info returns None if the lockout expired between the two calls.
            # The old code indexed into it unconditionally and turned that race -- and,
            # far more often, the success-counting bug -- into an HTTP 500.
            remaining = (lockout_info or {}).get("remaining_seconds", 0)
            logger.warning("MFA attempt blocked for user %s - rate limited", user_id)

            return {
                'verified': False,
                'rate_limited': True,
                'lockout_info': lockout_info,
                'remaining_attempts': 0,
                'message': (
                    f"Too many failed attempts. Try again in {remaining} seconds."
                    if remaining else "Too many failed attempts. Try again later."
                ),
            }

        # Verify TOTP code
        is_valid = self.verify_totp(secret, code)

        # Burn the code so it cannot be presented twice. pyotp has no replay protection:
        # without this, one observed code authenticates every request made for the rest of
        # its acceptance window. Only a code that verified is claimed, so a wrong guess
        # cannot be used to burn a code the legitimate user is about to type.
        if is_valid and not mfa_rate_limiter.claim_code(user_id, code, self.replay_window_seconds):
            logger.warning(
                "MFA replay rejected for user %s: this code has already been used", user_id
            )
            rate_limit_result = mfa_rate_limiter.record_attempt(user_id, False)
            return {
                'verified': False,
                'rate_limited': rate_limit_result['locked_out'],
                'replayed': True,
                'remaining_attempts': rate_limit_result['remaining_attempts'],
                'lockout_info': rate_limit_result.get('lockout_duration_minutes'),
                'message': 'This code has already been used. Wait for the next code.',
            }

        # Record attempt with rate limiter
        rate_limit_result = mfa_rate_limiter.record_attempt(user_id, is_valid)

        return {
            'verified': is_valid,
            'rate_limited': rate_limit_result['locked_out'],
            'remaining_attempts': rate_limit_result['remaining_attempts'],
            'lockout_info': rate_limit_result.get('lockout_duration_minutes'),
            'message': rate_limit_result['message'],
        }

    def generate_qr_code(self, secret: str, username: str) -> str:
        """Generate QR code for authenticator app"""
        # Create TOTP URI
        totp_uri = self._totp(secret).provisioning_uri(
            name=username,
            issuer_name=self.issuer,
        )

        # Generate QR code
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(totp_uri)
        qr.make(fit=True)

        # Create QR code image
        img = qr.make_image(fill_color="black", back_color="white")

        # Convert to base64
        buffer = io.BytesIO()
        img.save(buffer, 'PNG')
        img_str = base64.b64encode(buffer.getvalue()).decode()

        return f"data:image/png;base64,{img_str}"

    def get_remaining_time(self) -> int:
        """Get remaining seconds in current TOTP period"""
        # TOTP is defined against Unix time, which has no timezone. The IST helper used
        # here previously returned an aware datetime whose .timestamp() is the same
        # instant, so this was correct by accident; time.time() says so on purpose.
        import time

        return int(self.period - (time.time() % self.period))


# Create global MFA service instance
mfa_service = MFAService()
