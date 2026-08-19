"""
Tests for login throttling.

The VAPT found no login rate limiting or account lockout anywhere in EPMS. These tests pin
the behaviour that closes that, and in particular the parts that are easy to get subtly
wrong:

* the delay ladder must gate without sleeping,
* the lockout must escalate and then expire on its own,
* an unknown username must be throttled exactly like a real one, or the limiter becomes a
  username-existence oracle,
* a success must clear the account budget but NOT the IP budget,
* per-IP throttling must switch itself off when the resolved address is a proxy or
  loopback, because that means IP resolution is broken and keying on it would put the
  whole internet in one bucket.

Both storage backends are exercised: the in-process fallback and the Redis paths that run
in production.
"""

import time

import pytest

from services.auth.app.services.login_rate_limiter import (
    LoginRateLimiter,
    THROTTLED_MESSAGE,
    _parse_delay_ladder,
    normalise_username,
)
from services.auth.app.services.rate_limit_engine import SlidingWindowRateLimiter
from tests.auth.fake_redis import _FakeRedis

LADDER = {3: 2, 4: 5, 5: 15}


def _build(backend, **overrides):
    """A limiter with the agreed production numbers, on the requested backend."""
    kwargs = dict(connect=False) if backend == "memory" else dict(redis_client=_FakeRedis())
    account = SlidingWindowRateLimiter(
        namespace="login:acct",
        max_attempts=overrides.get("max_attempts", 6),
        window_minutes=15,
        lockout_minutes=overrides.get("lockout_minutes", 5),
        max_lockout_minutes=60,
        delay_ladder=overrides.get("ladder", LADDER),
        **kwargs,
    )
    ip_kwargs = dict(connect=False) if backend == "memory" else dict(redis_client=_FakeRedis())
    ip = SlidingWindowRateLimiter(
        namespace="login:ip",
        max_attempts=overrides.get("ip_max_attempts", 50),
        window_minutes=15,
        lockout_minutes=5,
        max_lockout_minutes=15,
        delay_ladder=None,
        **ip_kwargs,
    )
    return LoginRateLimiter(
        account_limiter=account,
        ip_limiter=ip,
        mode=overrides.get("mode", "enforce"),
        ip_enabled=overrides.get("ip_enabled", True),
    )


@pytest.fixture(params=["memory", "redis"])
def limiter(request):
    return _build(request.param)


CLIENT = "203.0.113.9"


class TestDelayLadder:
    def test_first_two_failures_are_not_gated(self, limiter):
        for _ in range(2):
            limiter.record_failure("trader", CLIENT)
            assert limiter.check("trader", CLIENT)["allowed"] is True

    def test_third_failure_gates_for_two_seconds(self, limiter):
        for _ in range(3):
            limiter.record_failure("trader", CLIENT)
        decision = limiter.check("trader", CLIENT)
        assert decision["allowed"] is False
        assert 0 < decision["retry_after"] <= 2

    def test_ladder_grows_with_each_further_failure(self, limiter):
        expected = {3: 2, 4: 5, 5: 15}
        for n in range(1, 6):
            result = limiter.record_failure("trader", CLIENT)
            assert result["account"]["delay_seconds"] == expected.get(n, 0)

    def test_sixth_failure_locks_instead_of_delaying(self, limiter):
        for _ in range(6):
            result = limiter.record_failure("trader", CLIENT)
        assert result["account"]["locked_out"] is True
        assert result["account"]["lockout_duration_minutes"] == 5

    def test_gate_is_stored_not_slept(self, limiter):
        """A gate must never block the handler — that is the DoS it exists to prevent."""
        for _ in range(3):
            limiter.record_failure("trader", CLIENT)
        started = time.time()
        limiter.check("trader", CLIENT)
        assert time.time() - started < 0.5

    def test_malformed_ladder_entries_are_skipped_not_fatal(self):
        assert _parse_delay_ladder("3:2,nonsense,5:15") == {3: 2, 5: 15}
        assert _parse_delay_ladder("") == {}


class TestLockoutEscalation:
    def test_lock_doubles_on_each_strike_and_caps(self, limiter):
        durations = []
        for _ in range(6):
            for _ in range(6):
                result = limiter.record_failure("trader", CLIENT)
            durations.append(result["account"]["lockout_duration_minutes"])
            # Unlock by hand so the next cycle can run without waiting five real minutes.
            limiter.account._delete(limiter.account._lock_key.format("trader"))
        assert durations == [5, 10, 20, 40, 60, 60]

    def test_lock_clears_the_window_so_next_cycle_starts_fresh(self, limiter):
        for _ in range(6):
            limiter.record_failure("trader", CLIENT)
        assert limiter.account.get_attempt_count("trader") == 0

    def test_success_clears_the_account_budget(self, limiter):
        for _ in range(3):
            limiter.record_failure("trader", CLIENT)
        limiter.record_success("trader", CLIENT)
        assert limiter.check("trader", CLIENT)["allowed"] is True
        assert limiter.account.get_attempt_count("trader") == 0

    def test_success_does_not_clear_the_ip_budget(self, limiter):
        """A valid login from a shared office address must not erase evidence of a spray."""
        for n in range(5):
            limiter.record_failure(f"victim{n}", CLIENT)
        limiter.record_success("trader", CLIENT)
        assert limiter.ip.get_attempt_count(CLIENT) == 5

    def test_strikes_survive_a_success(self, limiter):
        for _ in range(6):
            limiter.record_failure("trader", CLIENT)
        limiter.record_success("trader", CLIENT)
        for _ in range(6):
            result = limiter.record_failure("trader", CLIENT)
        assert result["account"]["lockout_duration_minutes"] == 10


class TestNoUsernameOracle:
    def test_unknown_username_is_throttled_identically(self, limiter):
        """The limiter never consults the user store, so absence is indistinguishable."""
        for _ in range(3):
            limiter.record_failure("does-not-exist", CLIENT)
        known = limiter.check("does-not-exist", CLIENT)
        assert known["allowed"] is False
        assert known["message"] == THROTTLED_MESSAGE

    def test_refusal_message_is_the_same_whichever_key_fires(self, limiter):
        for _ in range(6):
            limiter.record_failure("trader", CLIENT)
        by_account = limiter.check("trader", CLIENT)

        fresh = _build("memory", ip_max_attempts=2)
        for n in range(2):
            fresh.record_failure(f"other{n}", CLIENT)
        by_ip = fresh.check("someone-else", CLIENT)

        assert by_account["allowed"] is False and by_ip["allowed"] is False
        assert by_account["message"] == by_ip["message"] == THROTTLED_MESSAGE

    def test_username_case_and_padding_share_one_budget(self, limiter):
        for name in ("Trader", "TRADER ", " trader"):
            limiter.record_failure(name, CLIENT)
        assert limiter.account.get_attempt_count("trader") == 3

    def test_normalisation(self):
        assert normalise_username("  AdMin ") == "admin"
        assert normalise_username(None) == ""


class TestIpTier:
    def test_ip_tier_catches_spraying_the_account_key_cannot_see(self):
        limiter = _build("memory", ip_max_attempts=5)
        for n in range(5):
            # One guess each against five different accounts: no account key gets near
            # its own limit, but the IP does.
            limiter.record_failure(f"victim{n}", CLIENT)
        assert limiter.check("victim9", CLIENT)["allowed"] is False

    def test_ip_tier_is_skipped_for_loopback(self, limiter):
        """Loopback means IP resolution failed; keying on it would be one global bucket."""
        for n in range(80):
            limiter.record_failure(f"victim{n}", "127.0.0.1")
        assert limiter.ip.get_attempt_count("127.0.0.1") == 0
        assert limiter.check("someone", "127.0.0.1")["allowed"] is True

    def test_ip_tier_is_skipped_for_a_trusted_proxy_address(self, monkeypatch, limiter):
        monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.7")
        for n in range(80):
            limiter.record_failure(f"victim{n}", "10.0.0.7")
        assert limiter.ip.get_attempt_count("10.0.0.7") == 0

    def test_ip_tier_is_skipped_when_the_address_is_unknown(self, limiter):
        for n in range(80):
            limiter.record_failure(f"victim{n}", "unknown")
        assert limiter.ip.get_attempt_count("unknown") == 0

    def test_ip_tier_can_be_disabled_outright(self):
        limiter = _build("memory", ip_enabled=False, ip_max_attempts=2)
        for n in range(10):
            limiter.record_failure(f"victim{n}", CLIENT)
        assert limiter.ip.get_attempt_count(CLIENT) == 0

    def test_account_lock_does_not_depend_on_the_ip_tier(self):
        """Per-account throttling must keep working when per-IP is off or inert."""
        limiter = _build("memory", ip_enabled=False)
        for _ in range(6):
            result = limiter.record_failure("trader", CLIENT)
        assert result["account"]["locked_out"] is True


class TestModes:
    def test_off_records_nothing_and_refuses_nothing(self):
        limiter = _build("memory", mode="off")
        for _ in range(20):
            limiter.record_failure("trader", CLIENT)
        assert limiter.check("trader", CLIENT)["allowed"] is True
        assert limiter.account.get_attempt_count("trader") == 0

    def test_warn_counts_but_allows(self):
        limiter = _build("memory", mode="warn")
        for _ in range(6):
            limiter.record_failure("trader", CLIENT)
        decision = limiter.check("trader", CLIENT)
        assert decision["allowed"] is True
        assert "warn" in decision["detail"]
        # Counters stay warm, so flipping to enforce does not start from zero.
        assert limiter.account.is_locked_out("trader") is True

    def test_unknown_mode_falls_back_to_enforce(self):
        limiter = _build("memory", mode="banana")
        assert limiter.mode == "enforce"


class TestRetryAfter:
    def test_retry_after_is_always_at_least_one_second(self, limiter):
        for _ in range(3):
            limiter.record_failure("trader", CLIENT)
        assert limiter.check("trader", CLIENT)["retry_after"] >= 1

    def test_longer_of_the_two_waits_wins(self):
        limiter = _build("memory", ip_max_attempts=1)
        limiter.record_failure("trader", CLIENT)      # IP tier locks for 5 minutes
        for _ in range(2):
            limiter.record_failure("trader", CLIENT)  # account ladder gates for 2 seconds
        decision = limiter.check("trader", CLIENT)
        assert decision["allowed"] is False
        assert decision["retry_after"] > 60


class TestDegradedRedis:
    def test_a_redis_outage_does_not_lock_everyone_out(self):
        """Failing closed here would turn a cache outage into a platform outage."""

        class _Broken:
            def __getattr__(self, name):
                def _raise(*args, **kwargs):
                    raise ConnectionError("redis down")
                return _raise

        account = SlidingWindowRateLimiter(
            namespace="login:acct", max_attempts=6, delay_ladder=LADDER,
            redis_client=_Broken(),
        )
        limiter = LoginRateLimiter(account_limiter=account, ip_limiter=account, mode="enforce")
        assert limiter.check("trader", CLIENT)["allowed"] is True
        assert account.backend == "redis"  # still configured; serving from local state
