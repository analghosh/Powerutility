"""
Shared sliding-window rate limiter with exponential lockout backoff.

This is the engine extracted from `mfa_rate_limiter.py` so that login throttling and MFA
throttling share one implementation instead of two. The Redis semantics here are
security-critical and were arrived at by fixing real defects (see `mfa_rate_limiter` for
the history); duplicating them for a second caller would have meant duplicating the bugs
that are already fixed here.

What the engine provides
------------------------
* **A failure window.** A Redis sorted set per key, trimmed to `window_minutes`. Only
  failures are recorded; a success clears the window outright. Counting successes was a
  real defect that refused correct codes after four legitimate logins.
* **A lockout with exponential backoff.** Once the window holds `max_attempts` failures the
  key is locked for `lockout_minutes`, doubling on each repeat offence up to
  `max_lockout_minutes`. The strike counter survives a success on purpose, so an attacker
  who guesses one password mid-campaign does not reset their own penalty.
* **An optional delay ladder.** A map of failure-count to seconds. After the Nth failure
  the key is gated for that many seconds. This is a *ramp* in front of the lockout cliff,
  not a defence in its own right -- a few seconds means nothing to an attacker, but it
  stops naive unthrottled scripts and tells a real user something is wrong before they hit
  a wall. The gate is a stored expiry, never a `sleep()`: sleeping in the handler would
  hold a worker slot per attacker and rebuild the denial of service the limiter exists to
  prevent.
* **Namespacing.** Every caller gets its own key prefix, so `SCAN MATCH login:*` shows
  exactly one subsystem.

Storage and degraded behaviour
------------------------------
State lives in Redis on the token blacklist's connection settings, so it survives restarts
and is shared by every worker. If Redis is disabled or unreachable this falls back to an
in-process store: rate limiting still functions, but per-worker, so the effective budget is
multiplied by the worker count and lockouts are lost on restart. It never fails closed -- a
Redis outage must not lock every user out of the platform, and an attacker cannot gain
attempts by taking Redis down, because doing so is strictly harder than exhausting the
limit. Every fallback is logged.
"""

import hashlib
import logging
import threading
import time
import uuid
from typing import Dict, Mapping, Optional, Tuple

from ..core.config import settings

logger = logging.getLogger(__name__)


class _LocalStore:
    """
    In-process fallback used when Redis is unavailable.

    Deliberately simple and bounded: entries carry an expiry and are swept on write, so a
    long-running process cannot accumulate state for keys that stopped being used.
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


class SlidingWindowRateLimiter:
    """
    Per-subject rate limiting with exponential backoff and an optional delay ladder.

    Only failed attempts count. A success clears the failure record and the lockout, so a
    subject that authenticates correctly can do so as often as it likes.
    """

    # After a failure, stop calling Redis for this long. These are synchronous calls on an
    # async login handler with a 5-second socket timeout, so retrying on every request
    # during an outage would block the event loop for seconds per login -- turning a
    # degraded rate limiter into a degraded platform. One probe every 30 seconds recovers
    # automatically without that cost.
    REDIS_COOLDOWN_SECONDS = 30

    def __init__(
        self,
        namespace: str = "rl",
        max_attempts: int = 5,
        window_minutes: int = 15,
        lockout_minutes: int = 30,
        max_lockout_minutes: int = 1440,  # 24 hours
        delay_ladder: Optional[Mapping[int, int]] = None,
        clear_window_on_lock: bool = True,
        redis_client=None,
        connect: bool = True,
    ):
        """
        Args:
            namespace: Redis key prefix, one per subsystem (e.g. "mfa", "login:acct").
            delay_ladder: {failure_count: seconds} gate applied after that many failures.
            clear_window_on_lock: reset the failure window when a lockout is applied, so
                each lock cycle starts from a clean budget. Without it, a lockout shorter
                than the window leaves stale failures behind and the subject gets a single
                attempt after unlock instead of a fresh allowance -- behaviour nobody would
                predict from the configured numbers.
            redis_client: use this client instead of building one. Lets tests exercise the
                Redis paths, which are the ones that run in production, rather than only
                the in-process fallback.
            connect: set False to run in-process without attempting a connection.
        """
        self.namespace = namespace
        self.max_attempts = max_attempts
        self.window_minutes = window_minutes
        self.lockout_minutes = lockout_minutes
        self.max_lockout_minutes = max_lockout_minutes
        self.delay_ladder: Dict[int, int] = dict(delay_ladder or {})
        self.clear_window_on_lock = clear_window_on_lock

        self._fail_key = namespace + ":fail:{}"
        self._lock_key = namespace + ":lock:{}"
        self._strike_key = namespace + ":strikes:{}"
        self._delay_key = namespace + ":delay:{}"
        self._code_key = namespace + ":used:{}:{}"

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

        Shares `REDIS_BLACKLIST_ENABLED` and `REDIS_BLACKLIST_DB` on purpose: throttling
        state and token revocation have the same availability requirements and the same
        operational owner, so one switch and one database is one thing to get right
        instead of two.
        """
        if not getattr(settings, "REDIS_BLACKLIST_ENABLED", True):
            logger.warning(
                "%s rate limiting is running in-process: REDIS_BLACKLIST_ENABLED is false. "
                "Limits are per-worker and lost on restart.", self.namespace
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
            logger.info(
                "%s rate limiter using Redis db %s", self.namespace, settings.REDIS_BLACKLIST_DB
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "%s rate limiter could not reach Redis (%s). Falling back to in-process "
                "state: limits are per-worker and lost on restart.", self.namespace, exc
            )
            self._redis = None

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
                "%s rate limiter lost Redis (%s); continuing with in-process state for "
                "%ss at a time. Limits are per-worker while this lasts.",
                self.namespace, exc, self.REDIS_COOLDOWN_SECONDS,
            )
            self._redis_warned = True

    @property
    def backend(self) -> str:
        """"redis" or "memory" -- exposed so health checks and tests can see the mode."""
        return "redis" if self._redis is not None else "memory"

    # ------------------------------------------------------------------ queries

    def is_locked_out(self, subject: str) -> bool:
        return self._lockout_ttl(subject) > 0

    def _ttl_of(self, key: str) -> int:
        client = self._client()
        if client is not None:
            try:
                ttl = client.ttl(key)
                return int(ttl) if ttl and ttl > 0 else 0
            except Exception as exc:  # noqa: BLE001
                self._degrade(exc)
        return int(self._local.ttl(key))

    def _lockout_ttl(self, subject: str) -> int:
        """Seconds remaining on the lockout, 0 if not locked out."""
        return self._ttl_of(self._lock_key.format(subject))

    def delay_remaining(self, subject: str) -> int:
        """Seconds remaining on the delay gate, 0 if the subject may attempt now."""
        if not self.delay_ladder:
            return 0
        return self._ttl_of(self._delay_key.format(subject))

    def _strike_count(self, subject: str) -> int:
        key = self._strike_key.format(subject)
        client = self._client()
        if client is not None:
            try:
                value = client.get(key)
                return int(value) if value else 0
            except Exception as exc:  # noqa: BLE001
                self._degrade(exc)
        return int(self._local.get(key) or 0)

    def get_lockout_info(self, subject: str) -> Optional[Dict]:
        """
        Lockout details, or None when the subject is not locked out.

        Callers must handle None -- the old code did not, and dereferencing it was an
        HTTP 500 on `/mfa/verify`. `remaining_seconds` is always present when a dict is
        returned.
        """
        remaining = self._lockout_ttl(subject)
        if remaining <= 0:
            return None
        return {
            "remaining_seconds": remaining,
            "failure_count": self._strike_count(subject),
            "locked_until": time.time() + remaining,
        }

    def can_attempt(self, subject: str) -> bool:
        """
        May this subject attempt right now?

        A lockout blocks, and so does a pending delay-ladder gate. The failure count is
        what *creates* a lockout, so counting it here as well would be the same test twice
        -- and it was the version that counted successes, which is what locked people out
        of their own accounts.
        """
        return self._lockout_ttl(subject) <= 0 and self.delay_remaining(subject) <= 0

    def check(self, subject: str) -> Dict:
        """
        Whether the subject may attempt, and how long until it may.

        Returns a dict with `allowed`, `reason` ("ok" | "locked" | "delayed") and
        `retry_after` in seconds. Callers use `retry_after` for the HTTP `Retry-After`
        header; the reason is for logging, never for the response body, which must look
        identical whichever key tripped.
        """
        locked = self._lockout_ttl(subject)
        if locked > 0:
            return {"allowed": False, "reason": "locked", "retry_after": locked}
        delayed = self.delay_remaining(subject)
        if delayed > 0:
            return {"allowed": False, "reason": "delayed", "retry_after": delayed}
        return {"allowed": True, "reason": "ok", "retry_after": 0}

    def get_attempt_count(self, subject: str) -> int:
        """Failed attempts inside the current window. Successes are not recorded."""
        key = self._fail_key.format(subject)
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

    def record_attempt(self, subject: str, success: bool) -> Dict:
        """
        Record the outcome of an attempt and report the resulting budget.

        A success clears everything bar the strike counter. A failure is appended to the
        window and, once the window holds `max_attempts` failures, applies a lockout that
        doubles with each repeat offence up to `max_lockout_minutes`. Below that threshold
        the delay ladder may gate the next attempt for a few seconds.
        """
        if success:
            self._clear_failures(subject)
            return {
                "success": True,
                "locked_out": False,
                "remaining_attempts": self.max_attempts,
                "message": "Verification successful",
            }

        recent = self._record_failure(subject)
        remaining = max(0, self.max_attempts - recent)

        if remaining > 0:
            delay = self.delay_ladder.get(recent, 0)
            if delay > 0:
                self._apply_delay(subject, delay)
            return {
                "success": False,
                "locked_out": False,
                "remaining_attempts": remaining,
                "delay_seconds": delay,
                "message": f"{remaining} attempts remaining before lockout",
            }

        strikes = self._increment_strikes(subject)
        duration = min(self.lockout_minutes * (2 ** (strikes - 1)), self.max_lockout_minutes)
        self._apply_lockout(subject, duration, strikes)
        if self.clear_window_on_lock:
            # Start the next cycle from a clean budget. Skipping this is only safe while
            # the lockout outlasts the window; it silently breaks the moment someone tunes
            # the lockout shorter.
            self._delete(self._fail_key.format(subject), self._delay_key.format(subject))

        logger.warning(
            "%s lockout: subject %s locked for %s minutes after %s failed attempts (strike %s)",
            self.namespace, subject, duration, recent, strikes,
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

    def _record_failure(self, subject: str) -> int:
        key = self._fail_key.format(subject)
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

    def _apply_delay(self, subject: str, seconds: int) -> None:
        """Gate the next attempt until `seconds` have passed. Stored, never slept."""
        key = self._delay_key.format(subject)
        client = self._client()
        if client is not None:
            try:
                client.setex(key, int(seconds), "1")
                return
            except Exception as exc:  # noqa: BLE001
                self._degrade(exc)
        self._local.set(key, "1", seconds)

    def _increment_strikes(self, subject: str) -> int:
        key = self._strike_key.format(subject)
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
        self._local.set(key, str(value), ttl)
        return value

    def _apply_lockout(self, subject: str, duration_minutes: int, strikes: int) -> None:
        key = self._lock_key.format(subject)
        ttl = duration_minutes * 60
        client = self._client()
        if client is not None:
            try:
                client.setex(key, ttl, str(strikes))
                return
            except Exception as exc:  # noqa: BLE001
                self._degrade(exc)
        self._local.set(key, str(strikes), ttl)

    def _delete(self, *keys: str) -> None:
        client = self._client()
        if client is not None:
            try:
                client.delete(*keys)
                return
            except Exception as exc:  # noqa: BLE001
                self._degrade(exc)
        self._local.delete(*keys)

    def _clear_failures(self, subject: str) -> None:
        """Wipe the failure window, delay gate and lockout. Strikes survive, so backoff escalates."""
        self._delete(
            self._fail_key.format(subject),
            self._lock_key.format(subject),
            self._delay_key.format(subject),
        )

    def reset_user(self, subject: str) -> None:
        """Clear all rate-limit state for a subject (admin recovery)."""
        self._delete(
            self._fail_key.format(subject),
            self._lock_key.format(subject),
            self._strike_key.format(subject),
            self._delay_key.format(subject),
        )
        logger.info("%s rate limiting reset for subject %s", self.namespace, subject)

    # ------------------------------------------------------------------ replay guard

    def claim_code(self, subject: str, code: str, ttl_seconds: int) -> bool:
        """
        Claim a one-time code for this subject, once.

        `pyotp.verify` has no memory, so a code stays valid for the rest of its step no
        matter how many times it is presented. Burning the pair atomically (`SET NX EX`)
        means a code observed once -- over a shoulder, in a screenshot pasted into a
        support chat, through a relaying phishing page -- cannot be presented again.
        """
        digest = hashlib.sha256(f"{subject}:{code}".encode()).hexdigest()[:32]
        key = self._code_key.format(subject, digest)
        client = self._client()
        if client is not None:
            try:
                return bool(client.set(key, "1", nx=True, ex=ttl_seconds))
            except Exception as exc:  # noqa: BLE001
                self._degrade(exc)
        return self._local.set_if_absent(key, "1", ttl_seconds)
