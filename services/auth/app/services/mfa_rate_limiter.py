"""
MFA rate limiting and TOTP replay protection.

What changed and why
--------------------
The previous implementation had two defects that together made MFA hostile to the people
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

Storage
-------
State lives in Redis, on the same connection settings as the token blacklist, so it
survives a restart and is shared by every worker and replica. The previous in-process
`dict`/`deque` gave each uvicorn worker its own budget: with four workers an attacker got
four times the attempts and a locked-out user could be let straight back in by the next
worker in the rotation.

**Degraded behaviour, stated explicitly.** If Redis is disabled by configuration or
unreachable, this falls back to the in-process store -- the old behaviour. Rate limiting
still functions, but per-worker, so the effective budget is multiplied by the worker count
and lockouts are lost on restart. It never fails closed: a Redis outage must not lock every
user out of the platform, and an attacker cannot cause one to gain attempts because taking
Redis down is strictly harder than exhausting the limit. Every fallback is logged.

Replay protection
-----------------
`pyotp.verify` has no memory, so a code stayed valid for the rest of its 30-second step no
matter how many times it was presented -- verified on the running service, where one code
authenticated four separate logins. Honouring `MFA_WINDOW=1` widens that acceptance to
~90 seconds, making it worse. `claim_code()` burns a `(user_id, code)` pair atomically
(`SET NX EX`) for the length of the acceptance window, so a code observed once -- over a
shoulder, in a screenshot pasted into a support chat, through a relaying phishing page --
cannot be presented a second time.
"""

import hashlib
import logging
import threading
import time
import uuid
from typing import Dict, Optional, Tuple

from ..core.config import settings

logger = logging.getLogger(__name__)

# Key prefixes inside the blacklist Redis database. Distinct enough that a `SCAN MATCH
# mfa:*` shows exactly this subsystem and nothing of the token blacklist.
_FAIL_KEY = "mfa:fail:{}"        # sorted set of failed-attempt timestamps
_LOCK_KEY = "mfa:lock:{}"        # string, value = strike count, TTL = remaining lockout
_STRIKE_KEY = "mfa:strikes:{}"   # int, drives exponential backoff across lockouts
_CODE_KEY = "mfa:used:{}:{}"     # replay guard, one per (user, code)


class _LocalStore:
    """
    In-process fallback used when Redis is unavailable.

    Deliberately simple and bounded: entries carry an expiry and are swept on write, so a
    long-running process cannot accumulate state for users who stopped logging in.
    """

    MAX_ENTRIES = 50_000

    def __init__(self):
        self._lock = threading.Lock()
        self._values: Dict[str, Tuple[float, object]] = {}

    def _sweep(self, now: float) -> None:
        expired = [k for k, (exp, _) in self._values.items() if exp <= now]
        for key in expired:
            del self._values[key]
        if len(self._values) > self.MAX_ENTRIES:
            # Pathological case only. Drop the soonest-expiring half rather than refuse
            # to record anything, which would silently disable rate limiting.
            for key in sorted(self._values, key=lambda k: self._values[k][0])[: len(self._values) // 2]:
                del self._values[key]

    def get(self, key: str):
        now = time.time()
        with self._lock:
            entry = self._values.get(key)
            if not entry or entry[0] <= now:
                self._values.pop(key, None)
                return None
            return entry[1]

    def set(self, key: str, value, ttl: float) -> None:
        now = time.time()
        with self._lock:
            self._sweep(now)
            self._values[key] = (now + ttl, value)

    def set_if_absent(self, key: str, value, ttl: float) -> bool:
        now = time.time()
        with self._lock:
            self._sweep(now)
            entry = self._values.get(key)
            if entry and entry[0] > now:
                return False
            self._values[key] = (now + ttl, value)
            return True

    def ttl(self, key: str) -> float:
        now = time.time()
        with self._lock:
            entry = self._values.get(key)
            if not entry or entry[0] <= now:
                return 0.0
            return entry[0] - now

    def delete(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._values.pop(key, None)

    def append_timestamp(self, key: str, now: float, window_seconds: float) -> int:
        with self._lock:
            self._sweep(now)
            entry = self._values.get(key)
            stamps = list(entry[1]) if entry and entry[0] > now else []
            stamps.append(now)
            cutoff = now - window_seconds
            stamps = [s for s in stamps if s >= cutoff]
            self._values[key] = (now + window_seconds, stamps)
            return len(stamps)

    def count_timestamps(self, key: str, now: float, window_seconds: float) -> int:
        with self._lock:
            entry = self._values.get(key)
            if not entry or entry[0] <= now:
                return 0
            cutoff = now - window_seconds
            return sum(1 for s in entry[1] if s >= cutoff)


class MFARateLimiter:
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
        """
        Args:
            redis_client: use this client instead of building one. Lets tests exercise the
                Redis paths, which are the ones that run in production, rather than only
                the in-process fallback.
            connect: set False to run in-process without attempting a connection.
        """
        self.max_attempts = max_attempts
        self.window_minutes = window_minutes
        self.lockout_minutes = lockout_minutes
        self.max_lockout_minutes = max_lockout_minutes

        self._local = _LocalStore()
        self._redis = redis_client
        self._redis_warned = False
        self._redis_down_until = 0.0
        if redis_client is None and connect:
            self._connect_redis()

    # ------------------------------------------------------------------ storage

    def _connect_redis(self) -> None:
        """
        Open the Redis connection, reusing the token blacklist's settings.

        Shares `REDIS_BLACKLIST_ENABLED` and `REDIS_BLACKLIST_DB` on purpose: MFA state and
        token revocation have the same availability requirements and the same operational
        owner, so one switch and one database is one thing to get right instead of two.
        """
        if not getattr(settings, "REDIS_BLACKLIST_ENABLED", True):
            logger.warning(
                "MFA rate limiting is running in-process: REDIS_BLACKLIST_ENABLED is false. "
                "Limits are per-worker and lost on restart."
            )
            return
        try:
            import redis

            client = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_BLACKLIST_DB,
                password=settings.REDIS_PASSWORD or None,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
            )
            client.ping()
            self._redis = client
            logger.info("MFA rate limiter using Redis db %s", settings.REDIS_BLACKLIST_DB)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "MFA rate limiter could not reach Redis (%s). Falling back to in-process "
                "state: limits are per-worker and lost on restart.", exc
            )
            self._redis = None

    # After a failure, stop calling Redis for this long. These are synchronous calls on an
    # async login handler with a 5-second socket timeout, so retrying on every request
    # during an outage would block the event loop for seconds per login -- turning a
    # degraded rate limiter into a degraded platform. One probe every 30 seconds recovers
    # automatically without that cost.
    REDIS_COOLDOWN_SECONDS = 30

    def _client(self):
        """The Redis client, or None while the circuit breaker is open."""
        if self._redis is None:
            return None
        if time.time() < self._redis_down_until:
            return None
        return self._redis

    def _degrade(self, exc: Exception) -> None:
        """A Redis call failed mid-flight. Keep serving from local state, say so once."""
        self._redis_down_until = time.time() + self.REDIS_COOLDOWN_SECONDS
        if not self._redis_warned:
            logger.error(
                "MFA rate limiter lost Redis (%s); continuing with in-process state for "
                "%ss at a time. Limits are per-worker while this lasts.",
                exc, self.REDIS_COOLDOWN_SECONDS,
            )
            self._redis_warned = True

    @property
    def backend(self) -> str:
        """"redis" or "memory" -- exposed so health checks and tests can see the mode."""
        return "redis" if self._redis is not None else "memory"

    # ------------------------------------------------------------------ queries

    def is_locked_out(self, user_id: str) -> bool:
        return self._lockout_ttl(user_id) > 0

    def _lockout_ttl(self, user_id: str) -> int:
        """Seconds remaining on the lockout, 0 if not locked out."""
        key = _LOCK_KEY.format(user_id)
        client = self._client()
        if client is not None:
            try:
                ttl = client.ttl(key)
                return int(ttl) if ttl and ttl > 0 else 0
            except Exception as exc:  # noqa: BLE001
                self._degrade(exc)
        return int(self._local.ttl(key))

    def _strike_count(self, user_id: str) -> int:
        key = _STRIKE_KEY.format(user_id)
        client = self._client()
        if client is not None:
            try:
                value = client.get(key)
                return int(value) if value else 0
            except Exception as exc:  # noqa: BLE001
                self._degrade(exc)
        return int(self._local.get(key) or 0)

    def get_lockout_info(self, user_id: str) -> Optional[Dict]:
        """
        Lockout details, or None when the user is not locked out.

        Callers must handle None -- the old code did not, and dereferencing it was the
        HTTP 500 on `/mfa/verify`. `remaining_seconds` is always present when a dict is
        returned.
        """
        remaining = self._lockout_ttl(user_id)
        if remaining <= 0:
            return None
        return {
            "remaining_seconds": remaining,
            "failure_count": self._strike_count(user_id),
            "locked_until": time.time() + remaining,
        }

    def can_attempt(self, user_id: str) -> bool:
        """
        May this user present a code right now?

        Only a lockout blocks. The failure count is what *creates* a lockout, so counting
        it here as well would be the same test twice -- and it was the version that counted
        successes, which is what locked people out of their own accounts.
        """
        return not self.is_locked_out(user_id)

    def get_attempt_count(self, user_id: str) -> int:
        """Failed attempts inside the current window. Successes are not recorded."""
        key = _FAIL_KEY.format(user_id)
        now = time.time()
        window = self.window_minutes * 60
        client = self._client()
        if client is not None:
            try:
                client.zremrangebyscore(key, "-inf", now - window)
                return int(client.zcard(key) or 0)
            except Exception as exc:  # noqa: BLE001
                self._degrade(exc)
        return self._local.count_timestamps(key, now, window)

    # ------------------------------------------------------------------ mutations

    def record_attempt(self, user_id: str, success: bool) -> Dict:
        """
        Record the outcome of an MFA attempt and report the resulting budget.

        A success clears everything. A failure is appended to the window and, once the
        window holds `max_attempts` failures, applies a lockout that doubles with each
        repeat offence up to `max_lockout_minutes`.
        """
        if success:
            self._clear_failures(user_id)
            return {
                "success": True,
                "locked_out": False,
                "remaining_attempts": self.max_attempts,
                "message": "MFA verification successful",
            }

        recent = self._record_failure(user_id)
        remaining = max(0, self.max_attempts - recent)

        if remaining > 0:
            return {
                "success": False,
                "locked_out": False,
                "remaining_attempts": remaining,
                "message": f"{remaining} attempts remaining before lockout",
            }

        strikes = self._increment_strikes(user_id)
        duration = min(self.lockout_minutes * (2 ** (strikes - 1)), self.max_lockout_minutes)
        self._apply_lockout(user_id, duration, strikes)

        logger.warning(
            "MFA lockout: user %s locked for %s minutes after %s failed attempts (strike %s)",
            user_id, duration, recent, strikes,
        )
        return {
            "success": False,
            "locked_out": True,
            "remaining_attempts": 0,
            "lockout_duration_minutes": duration,
            "locked_until": time.time() + duration * 60,
            "failure_count": strikes,
            "message": f"Account locked for {duration} minutes due to too many failed attempts",
        }

    def _record_failure(self, user_id: str) -> int:
        key = _FAIL_KEY.format(user_id)
        now = time.time()
        window = self.window_minutes * 60
        client = self._client()
        if client is not None:
            try:
                pipe = client.pipeline()
                pipe.zremrangebyscore(key, "-inf", now - window)
                # The member must be unique across every worker, or two failures landing on
                # the same timestamp collapse into one sorted-set entry and hand the
                # attacker a free guess. A random suffix costs nothing and cannot collide.
                pipe.zadd(key, {f"{now:.6f}:{uuid.uuid4().hex[:12]}": now})
                pipe.zcard(key)
                pipe.expire(key, int(window))
                return int(pipe.execute()[2] or 0)
            except Exception as exc:  # noqa: BLE001
                self._degrade(exc)
        return self._local.append_timestamp(key, now, window)

    def _increment_strikes(self, user_id: str) -> int:
        key = _STRIKE_KEY.format(user_id)
        ttl = self.max_lockout_minutes * 60
        client = self._client()
        if client is not None:
            try:
                pipe = client.pipeline()
                pipe.incr(key)
                pipe.expire(key, ttl)
                return int(pipe.execute()[0] or 1)
            except Exception as exc:  # noqa: BLE001
                self._degrade(exc)
        value = int(self._local.get(key) or 0) + 1
        self._local.set(key, value, ttl)
        return value

    def _apply_lockout(self, user_id: str, duration_minutes: int, strikes: int) -> None:
        key = _LOCK_KEY.format(user_id)
        ttl = duration_minutes * 60
        client = self._client()
        if client is not None:
            try:
                client.setex(key, ttl, str(strikes))
                return
            except Exception as exc:  # noqa: BLE001
                self._degrade(exc)
        self._local.set(key, str(strikes), ttl)

    def _clear_failures(self, user_id: str) -> None:
        """Wipe the failure window and any lockout. Strikes survive, so backoff escalates."""
        keys = (_FAIL_KEY.format(user_id), _LOCK_KEY.format(user_id))
        client = self._client()
        if client is not None:
            try:
                client.delete(*keys)
                return
            except Exception as exc:  # noqa: BLE001
                self._degrade(exc)
        self._local.delete(*keys)

    def reset_user(self, user_id: str) -> None:
        """Clear all rate-limit state for a user (admin recovery)."""
        keys = (
            _FAIL_KEY.format(user_id),
            _LOCK_KEY.format(user_id),
            _STRIKE_KEY.format(user_id),
        )
        client = self._client()
        if client is not None:
            try:
                client.delete(*keys)
            except Exception as exc:  # noqa: BLE001
                self._degrade(exc)
                self._local.delete(*keys)
        else:
            self._local.delete(*keys)
        # The backup-code path rate limits under a separate key.
        if not user_id.endswith("_backup"):
            self.reset_user(f"{user_id}_backup")
        logger.info("MFA rate limiting reset for user %s", user_id)

    # ------------------------------------------------------------------ replay guard

    def claim_code(self, user_id: str, code: str, ttl_seconds: int) -> bool:
        """
        Claim a TOTP code for this user, once.

        Returns True the first time and False for every presentation of the same code
        inside `ttl_seconds`. The code is hashed before it becomes part of a key so a
        Redis dump does not contain live second factors.

        Fails **open** when the store is unavailable: the fallback is in-process, which
        still stops replay within a worker. The alternative -- refusing the login -- would
        turn a Redis outage into a total authentication outage for every MFA user, which
        is a larger and more likely harm than a replay inside a 90-second window.
        """
        digest = hashlib.sha256(f"{user_id}:{code}".encode()).hexdigest()[:32]
        key = _CODE_KEY.format(user_id, digest)
        client = self._client()
        if client is not None:
            try:
                return bool(client.set(key, "1", nx=True, ex=ttl_seconds))
            except Exception as exc:  # noqa: BLE001
                self._degrade(exc)
        return self._local.set_if_absent(key, "1", ttl_seconds)


# Global rate limiter instance
mfa_rate_limiter = MFARateLimiter(
    max_attempts=5,      # 5 failed attempts per window
    window_minutes=15,   # 15-minute window
    lockout_minutes=30,  # first lockout is 30 minutes, doubling thereafter
    max_lockout_minutes=1440,  # capped at 24 hours
)
