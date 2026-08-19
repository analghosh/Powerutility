"""
Tests for the MFA rate limiter.

The limiter previously counted successful verifications toward the attempt budget and
stored everything in a per-process dict. The first made MFA unusable; the second made the
limit meaningless in the multi-worker deployment that actually runs in production.

Both storage modes are exercised. `_FakeRedis` implements only the handful of commands the
limiter uses, but it means the Redis branches -- the ones that run in production -- are
tested rather than only the in-process fallback.
"""

import time

import pytest

from services.auth.app.services.mfa_rate_limiter import MFARateLimiter


from tests.auth.fake_redis import _FakeRedis, _FakePipeline  # noqa: F401


@pytest.fixture(params=["memory", "redis"])
def limiter(request):
    """Every test runs against both storage backends."""
    if request.param == "memory":
        return MFARateLimiter(max_attempts=5, window_minutes=15, lockout_minutes=30,
                              connect=False)
    return MFARateLimiter(max_attempts=5, window_minutes=15, lockout_minutes=30,
                          redis_client=_FakeRedis())


class TestSuccessesDoNotCount:
    """The defect that locked legitimate users out of their own accounts."""

    def test_ten_successes_never_exhaust_the_budget(self, limiter):
        for attempt in range(10):
            assert limiter.can_attempt("u1") is True, f"refused before attempt {attempt + 1}"
            result = limiter.record_attempt("u1", success=True)
            assert result["success"] is True
            assert result["locked_out"] is False
            assert result["remaining_attempts"] == limiter.max_attempts

        assert limiter.can_attempt("u1") is True
        assert limiter.is_locked_out("u1") is False

    def test_successes_are_not_recorded_as_attempts(self, limiter):
        for _ in range(6):
            limiter.record_attempt("u2", success=True)
        assert limiter.get_attempt_count("u2") == 0

    def test_a_success_clears_accumulated_failures(self, limiter):
        for _ in range(4):
            limiter.record_attempt("u3", success=False)
        assert limiter.get_attempt_count("u3") == 4

        limiter.record_attempt("u3", success=True)
        assert limiter.get_attempt_count("u3") == 0
        assert limiter.can_attempt("u3") is True


class TestFailuresStillLock:
    def test_the_fifth_failure_locks(self, limiter):
        for attempt in range(4):
            result = limiter.record_attempt("f1", success=False)
            assert result["locked_out"] is False
            assert result["remaining_attempts"] == 4 - attempt

        result = limiter.record_attempt("f1", success=False)
        assert result["locked_out"] is True
        assert result["remaining_attempts"] == 0
        assert limiter.is_locked_out("f1") is True
        assert limiter.can_attempt("f1") is False

    def test_lockout_info_is_populated_when_locked(self, limiter):
        for _ in range(5):
            limiter.record_attempt("f2", success=False)

        info = limiter.get_lockout_info("f2")
        assert info is not None
        # The crash was a caller doing exactly this against None.
        assert info["remaining_seconds"] > 0
        assert info["failure_count"] >= 1

    def test_lockout_info_is_none_when_not_locked(self, limiter):
        assert limiter.get_lockout_info("never-seen") is None
        limiter.record_attempt("f3", success=False)
        assert limiter.get_lockout_info("f3") is None

    def test_backoff_doubles_on_a_repeat_offence(self, limiter):
        for _ in range(5):
            first = limiter.record_attempt("f4", success=False)
        assert first["lockout_duration_minutes"] == 30

        # Clear the lockout the way an admin would, then reoffend.
        limiter._clear_failures("f4")
        for _ in range(5):
            second = limiter.record_attempt("f4", success=False)
        assert second["lockout_duration_minutes"] == 60

    def test_backoff_is_capped(self, limiter):
        limiter.max_lockout_minutes = 90
        for _ in range(6):
            limiter._clear_failures("f5")
            for _ in range(5):
                result = limiter.record_attempt("f5", success=False)
        assert result["lockout_duration_minutes"] == 90

    def test_users_are_limited_independently(self, limiter):
        for _ in range(5):
            limiter.record_attempt("victim", success=False)
        assert limiter.can_attempt("victim") is False
        assert limiter.can_attempt("bystander") is True


class TestAdminReset:
    def test_reset_releases_a_locked_user(self, limiter):
        for _ in range(5):
            limiter.record_attempt("r1", success=False)
        assert limiter.can_attempt("r1") is False

        limiter.reset_user("r1")

        assert limiter.can_attempt("r1") is True
        assert limiter.is_locked_out("r1") is False
        assert limiter.get_attempt_count("r1") == 0
        assert limiter.get_lockout_info("r1") is None

    def test_reset_clears_the_backoff_history(self, limiter):
        for _ in range(5):
            limiter.record_attempt("r2", success=False)
        limiter.reset_user("r2")

        for _ in range(5):
            result = limiter.record_attempt("r2", success=False)
        assert result["lockout_duration_minutes"] == 30

    def test_reset_also_clears_the_backup_code_budget(self, limiter):
        """
        The backup-code login path rate limits under "<user_id>_backup". Resetting only the
        primary key would leave a user locked out of the very path meant to rescue them.
        """
        for _ in range(5):
            limiter.record_attempt("r3_backup", success=False)
        assert limiter.can_attempt("r3_backup") is False

        limiter.reset_user("r3")

        assert limiter.can_attempt("r3_backup") is True


class TestReplayGuard:
    def test_a_code_can_be_claimed_once(self, limiter):
        assert limiter.claim_code("u", "123456", 90) is True
        assert limiter.claim_code("u", "123456", 90) is False

    def test_claims_are_per_user(self, limiter):
        assert limiter.claim_code("a", "123456", 90) is True
        assert limiter.claim_code("b", "123456", 90) is True

    def test_different_codes_are_independent(self, limiter):
        assert limiter.claim_code("u", "111111", 90) is True
        assert limiter.claim_code("u", "222222", 90) is True

    def test_a_claim_expires(self, limiter):
        assert limiter.claim_code("u", "123456", 1) is True
        time.sleep(1.05)
        assert limiter.claim_code("u", "123456", 1) is True

    def test_the_code_is_not_stored_verbatim(self, limiter):
        """A store dump must not be a list of live second factors."""
        limiter.claim_code("user-x", "987654", 90)
        keys = (
            list(limiter._redis.strings)
            if limiter.backend == "redis"
            else list(limiter._local._values)
        )
        assert keys, "the claim recorded nothing"
        assert all("987654" not in key for key in keys)


class TestDegradedBehaviour:
    def test_a_redis_outage_falls_back_instead_of_locking_everyone_out(self):
        """
        Fail safe, deliberately. A Redis outage must not become an authentication outage,
        and an attacker cannot profit: taking Redis down is harder than exhausting five
        attempts.
        """
        class _BrokenRedis(_FakeRedis):
            def __getattribute__(self, name):
                if name in {"get", "set", "setex", "incr", "expire", "ttl", "delete",
                            "zadd", "zcard", "zremrangebyscore", "pipeline"}:
                    raise ConnectionError("redis is down")
                return super().__getattribute__(name)

        limiter = MFARateLimiter(redis_client=_BrokenRedis())

        assert limiter.can_attempt("u") is True
        assert limiter.record_attempt("u", success=True)["success"] is True
        for _ in range(5):
            result = limiter.record_attempt("u", success=False)
        assert result["locked_out"] is True          # still rate limited, in-process
        assert limiter.claim_code("u", "123456", 90) is True
        assert limiter.claim_code("u", "123456", 90) is False   # replay still caught

    def test_backend_is_reported(self):
        assert MFARateLimiter(connect=False).backend == "memory"
        assert MFARateLimiter(redis_client=_FakeRedis()).backend == "redis"


class TestPersistenceAcrossWorkers:
    def test_two_limiters_sharing_redis_share_the_budget(self):
        """
        The reason for moving off in-process state: with four uvicorn workers, a per-process
        dict gave an attacker four times the attempts and let a locked-out user in through
        the next worker in the rotation.
        """
        shared = _FakeRedis()
        worker_a = MFARateLimiter(redis_client=shared)
        worker_b = MFARateLimiter(redis_client=shared)

        for _ in range(5):
            worker_a.record_attempt("shared-user", success=False)

        assert worker_a.can_attempt("shared-user") is False
        assert worker_b.can_attempt("shared-user") is False

    def test_a_code_burnt_on_one_worker_is_burnt_on_all(self):
        shared = _FakeRedis()
        worker_a = MFARateLimiter(redis_client=shared)
        worker_b = MFARateLimiter(redis_client=shared)

        assert worker_a.claim_code("u", "123456", 90) is True
        assert worker_b.claim_code("u", "123456", 90) is False
