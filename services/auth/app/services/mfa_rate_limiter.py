"""
MFA rate limiting and TOTP replay protection.

The mechanics live in `rate_limit_engine.SlidingWindowRateLimiter`, which was extracted
from this module when login throttling needed the same behaviour. This file keeps the MFA
configuration, the MFA-specific key namespace, and the backup-code reset pairing.

What changed and why
--------------------
The original implementation had two defects that together made MFA hostile to the people
using it correctly:

1. **Successes counted toward the limit.** `record_attempt` appended a timestamp whether
   the code was right or wrong, and `can_attempt` counted every attempt in the window. So
   five MFA actions of any kind in fifteen minutes exhausted the budget. Measured on the
   running service: a freshly enrolled account got **four** successful logins (enrolment
   consumed the fifth slot) before a correct code was refused for thirty minutes, with the
   message "Invalid temporary token or TOTP code" -- which is untrue and unactionable.
   Only failures are counted now. A success clears the record outright.

2. **The blocked path crashed.** With attempts exhausted but no lockout recorded,
   `can_attempt` was False while `get_lockout_info` returned `None`, and the caller
   dereferenced `lockout_info['remaining_seconds']` -> `TypeError` -> HTTP 500 on
   `/mfa/verify` and `/mfa/disable`, swallowed into a misleading 401 on
   `/auth/verify-login`. `get_lockout_info` now always returns a dict when access is
   refused, and the caller no longer assumes a shape.

Storage, degraded behaviour and replay protection are documented on the engine.

Why MFA is stricter than login
------------------------------
MFA allows 5 failures and locks for 30 minutes doubling; login allows 6 and locks for 5
minutes doubling. That asymmetry is deliberate. A TOTP code is six digits -- a 10^6 space
that rate limiting is the *only* thing guarding -- and it is read off a device the user is
holding, so typos are rare. A password is drawn from a far larger space and typed from
memory. Login can afford to be gentler; MFA cannot.
"""

import logging

from ..core.config import settings
from .rate_limit_engine import SlidingWindowRateLimiter, _LocalStore  # noqa: F401  (re-exported for tests)

logger = logging.getLogger(__name__)

# Key prefixes inside the blacklist Redis database. Distinct enough that a `SCAN MATCH
# mfa:*` shows exactly this subsystem and nothing of the token blacklist.
_FAIL_KEY = "mfa:fail:{}"        # sorted set of failed-attempt timestamps
_LOCK_KEY = "mfa:lock:{}"        # string, value = strike count, TTL = remaining lockout
_STRIKE_KEY = "mfa:strikes:{}"   # int, drives exponential backoff across lockouts
_CODE_KEY = "mfa:used:{}:{}"     # replay guard, one per (user, code)


class MFARateLimiter(SlidingWindowRateLimiter):
    """
    Per-user rate limiting for MFA verification, with exponential backoff.

    Only failed attempts count. A successful verification clears the failure record and the
    lockout, so a user who authenticates correctly can do so as often as they like.
    """

    def __init__(
        self,
        max_attempts: int = 5,
        window_minutes: int = 15,
        lockout_minutes: int = 30,
        max_lockout_minutes: int = 1440,  # 24 hours
        redis_client=None,
        connect: bool = True,
    ):
        super().__init__(
            namespace="mfa",
            max_attempts=max_attempts,
            window_minutes=window_minutes,
            lockout_minutes=lockout_minutes,
            max_lockout_minutes=max_lockout_minutes,
            # MFA has no delay ladder: a six-digit code typed off a phone does not need a
            # ramp, and the lockout is the control that matters here.
            delay_ladder=None,
            redis_client=redis_client,
            connect=connect,
        )

    def record_attempt(self, user_id: str, success: bool) -> dict:
        result = super().record_attempt(user_id, success)
        if success:
            result["message"] = "MFA verification successful"
        return result

    def reset_user(self, user_id: str) -> None:
        """Clear all rate-limit state for a user (admin recovery)."""
        super().reset_user(user_id)
        # The backup-code path rate limits under a separate key.
        if not user_id.endswith("_backup"):
            super().reset_user(f"{user_id}_backup")
        logger.info("MFA rate limiting reset for user %s", user_id)


# Global rate limiter instance
mfa_rate_limiter = MFARateLimiter(
    max_attempts=5,      # 5 failed attempts per window
    window_minutes=15,   # 15-minute window
    lockout_minutes=30,  # first lockout is 30 minutes, doubling thereafter
    max_lockout_minutes=1440,  # capped at 24 hours
)
