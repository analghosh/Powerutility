"""
Login throttling: per-account and per-IP, with a delay ladder in front of a short
auto-expiring lockout.

Why this exists
---------------
The InfoHash penetration test found no login rate limiting or account lockout anywhere in
EPMS. One phished or guessed password reached the whole platform, and an unauthenticated
attacker could hammer `/login` without limit. The second half matters as much as the
first: password hashing runs on the thread pool, so unmetered login attempts are also a
CPU-exhaustion path against the one service every other service depends on for request
validation.

Shape of the control
--------------------
Two independent limiters, doing two different jobs:

* **Account key** (the submitted username, normalised) is the primary control. Six
  failures in fifteen minutes locks the account for five minutes, doubling on each repeat
  strike to a one-hour cap. It works regardless of network topology, which is what makes
  it the one that must not be evadable.
* **IP key** is secondary and deliberately far looser -- fifty failures in the same
  window. It catches spraying (one guess each against many accounts), which the account
  key cannot see by construction. It is loose because PTC users may egress through a
  shared corporate NAT address, where one IP is the entire company.

Both record every failure; whichever refuses first supplies the `Retry-After`.

Design decisions that are easy to get wrong
-------------------------------------------
1. **Key on the submitted username whether or not the account exists.** Throttling only
   real accounts turns the limiter into a username-existence oracle: spray, watch which
   names start slowing down, harvest the valid list. Unknown names walk the identical
   ladder.
2. **The refusal looks the same whichever key tripped.** The caller gets one message and
   one status. Which limiter fired is logged, never returned.
3. **The delay is a stored expiry, never a `sleep()`.** Sleeping in the handler holds a
   worker slot per attacker and rebuilds the denial of service this exists to prevent.
4. **The lockout auto-expires; no admin unlock is required.** A hard lockout would let an
   unauthenticated attacker take the platform down with six bad passwords per account
   across 740 accounts, with a human as the only recovery path. Auto-expiry makes that
   attack self-healing. It is also why the first lockout is five minutes rather than the
   thirty MFA uses: DAM bid submission closes at 11:00, and a trader locked out at 10:55
   is a financial event, not a helpdesk ticket.
5. **Rate limiting is more lenient than MFA on purpose.** See `mfa_rate_limiter`.

Rollout
-------
`LOGIN_RATE_LIMIT_MODE` mirrors `AUTHZ_MODE`: `enforce` (default), `warn` (log the refusal
that would have happened, allow the request), `off`. Anything that could lock 740 accounts
needs a switch that does not require a code change to pull.
"""

import logging
from typing import Dict, Optional

from ..core.config import settings
from .rate_limit_engine import SlidingWindowRateLimiter

logger = logging.getLogger(__name__)

# One message for every refusal, whichever limiter fired and whether or not the account
# exists. See design decision 2.
THROTTLED_MESSAGE = (
    "Too many failed login attempts. Please wait before trying again."
)


def _parse_delay_ladder(raw: str) -> Dict[int, int]:
    """
    Parse "3:2,4:5,5:15" into {3: 2, 4: 5, 5: 15}.

    A malformed entry is skipped with a warning rather than raising: a typo in an
    environment variable must not stop the auth service from booting, and losing one rung
    of the ramp is a far smaller problem than losing logins entirely.
    """
    ladder: Dict[int, int] = {}
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            count, seconds = chunk.split(":", 1)
            ladder[int(count)] = int(seconds)
        except (ValueError, TypeError):
            logger.warning("Ignoring malformed LOGIN_DELAY_LADDER entry %r", chunk)
    return ladder


def normalise_username(username: str) -> str:
    """
    Fold the username to one bucket per account.

    Without this, "Admin", "admin " and "ADMIN" are three separate budgets and the
    effective limit triples for anyone who thinks to vary the case.
    """
    return (username or "").strip().lower()


class LoginRateLimiter:
    """Per-account and per-IP login throttling. Neither limiter can veto the other's key."""

    def __init__(
        self,
        account_limiter: Optional[SlidingWindowRateLimiter] = None,
        ip_limiter: Optional[SlidingWindowRateLimiter] = None,
        mode: Optional[str] = None,
        ip_enabled: Optional[bool] = None,
        connect: bool = True,
    ):
        self.mode = (mode if mode is not None else settings.LOGIN_RATE_LIMIT_MODE).strip().lower()
        if self.mode not in ("enforce", "warn", "off"):
            logger.error(
                "LOGIN_RATE_LIMIT_MODE=%r is not one of enforce/warn/off; falling back to "
                "enforce.", self.mode
            )
            self.mode = "enforce"

        self.ip_enabled = (
            settings.LOGIN_IP_RATE_LIMIT_ENABLED if ip_enabled is None else ip_enabled
        )

        self.account = account_limiter or SlidingWindowRateLimiter(
            namespace="login:acct",
            max_attempts=settings.LOGIN_MAX_ATTEMPTS,
            window_minutes=settings.LOGIN_WINDOW_MINUTES,
            lockout_minutes=settings.LOGIN_LOCKOUT_MINUTES,
            max_lockout_minutes=settings.LOGIN_MAX_LOCKOUT_MINUTES,
            delay_ladder=_parse_delay_ladder(settings.LOGIN_DELAY_LADDER),
            connect=connect,
        )
        self.ip = ip_limiter or SlidingWindowRateLimiter(
            namespace="login:ip",
            max_attempts=settings.LOGIN_IP_MAX_ATTEMPTS,
            window_minutes=settings.LOGIN_IP_WINDOW_MINUTES,
            lockout_minutes=settings.LOGIN_IP_LOCKOUT_MINUTES,
            max_lockout_minutes=settings.LOGIN_IP_MAX_LOCKOUT_MINUTES,
            # No ramp on the IP tier: the ladder exists to warn a human who is mistyping,
            # and nobody reads a shared egress address as feedback.
            delay_ladder=None,
            connect=connect,
        )

        self._bad_ip_warned = False

    # ------------------------------------------------------------------ IP eligibility

    def _ip_key(self, ip: Optional[str]) -> Optional[str]:
        """
        The IP to key on, or None when per-IP limiting must be skipped for this request.

        The interlock is the important part. If the resolved address is unknown, loopback,
        or one of our own trusted proxies, then IP resolution did not work -- the usual
        cause being nginx not setting X-Real-IP / X-Forwarded-For, so every request in the
        world resolves to the proxy. Keying on that value would put the entire internet in
        one bucket and lock out all of PTC after fifty failures from anywhere. Skipping is
        strictly better than a limiter that looks like it works and takes the platform
        down: the account limiter is unaffected and still does the primary job.
        """
        if not self.ip_enabled:
            return None
        if not ip or ip == "unknown":
            return None

        from libs.middleware.utils import get_trusted_proxies

        if ip in get_trusted_proxies() or ip in ("127.0.0.1", "::1", "localhost"):
            if not self._bad_ip_warned:
                logger.error(
                    "Per-IP login throttling is inactive: the client address resolves to "
                    "%s, which is a trusted proxy or loopback. The reverse proxy is "
                    "probably not setting X-Real-IP / X-Forwarded-For, or TRUSTED_PROXIES "
                    "does not list it. Per-account throttling is unaffected.", ip
                )
                self._bad_ip_warned = True
            return None
        return ip

    # ------------------------------------------------------------------ queries

    def check(self, username: str, ip: Optional[str]) -> Dict:
        """
        May this login attempt proceed?

        Call this *before* verifying the password: hashing is the expensive half, and
        letting a throttled attempt reach it defeats the availability half of the control.

        Returns `allowed`, `retry_after` (seconds, for the HTTP header), `message` (safe to
        return to the caller) and `detail` (for logs only -- it names the key that fired).
        """
        if self.mode == "off":
            return {"allowed": True, "retry_after": 0, "message": "", "detail": "mode=off"}

        account_key = normalise_username(username)
        decisions = []
        if account_key:
            decisions.append(("account", self.account.check(account_key)))
        ip_key = self._ip_key(ip)
        if ip_key:
            decisions.append(("ip", self.ip.check(ip_key)))

        refused = [(scope, d) for scope, d in decisions if not d["allowed"]]
        if not refused:
            return {"allowed": True, "retry_after": 0, "message": "", "detail": "ok"}

        # If both tiers refuse, honour the longer wait — telling the caller to retry while
        # the other limiter is still holding would just burn another attempt.
        scope, decision = max(refused, key=lambda item: item[1]["retry_after"])
        detail = f"{scope}:{decision['reason']} retry_after={decision['retry_after']}s"

        if self.mode == "warn":
            logger.warning(
                "LOGIN_RATE_LIMIT_MODE=warn: would have refused login for %r from %s (%s)",
                account_key or "<empty>", ip or "unknown", detail,
            )
            return {"allowed": True, "retry_after": 0, "message": "", "detail": f"warn {detail}"}

        return {
            "allowed": False,
            "retry_after": max(1, int(decision["retry_after"])),
            "message": THROTTLED_MESSAGE,
            "detail": detail,
        }

    # ------------------------------------------------------------------ mutations

    def record_failure(self, username: str, ip: Optional[str]) -> Dict:
        """Record one failed login against both keys. Safe to call in `warn` mode — the
        counters stay warm so switching to `enforce` does not start from zero."""
        if self.mode == "off":
            return {}
        result: Dict[str, Dict] = {}
        account_key = normalise_username(username)
        if account_key:
            result["account"] = self.account.record_attempt(account_key, success=False)
        ip_key = self._ip_key(ip)
        if ip_key:
            result["ip"] = self.ip.record_attempt(ip_key, success=False)
        return result

    def record_success(self, username: str, ip: Optional[str]) -> None:
        """
        Clear the account's failure state on a correct password.

        The IP counter is deliberately NOT cleared. One valid login from a shared office
        address would otherwise wipe the evidence of a spray in progress from that same
        address — which is exactly the attack the IP tier exists to see. Its window expires
        on its own soon enough.
        """
        if self.mode == "off":
            return
        account_key = normalise_username(username)
        if account_key:
            self.account.record_attempt(account_key, success=True)

    def reset_account(self, username: str) -> None:
        """Admin recovery: clear all throttle state for one account."""
        account_key = normalise_username(username)
        if account_key:
            self.account.reset_user(account_key)


# Global instance
login_rate_limiter = LoginRateLimiter()
