"""
`/health` was an unauthenticated denial-of-service amplifier.

Every request ran the full dependency sweep — Redis connect/ping/info/close, Mongo
connect/ping/buildInfo/list_database_names/close — on the same event loop as live traffic.
Measured on the processing service: 30 concurrent anonymous /health requests took real
authenticated API calls from 8 ms to 1,700 ms median. No credentials required.

The fix is caching with single-flight, not cheaper probes. These tests pin the two
properties that actually remove the amplification: a flood inside the window costs one
sweep, and a flood on a COLD cache also costs one sweep.
"""

import asyncio

import pytest

from libs.health.health_checker import HealthChecker, ComponentHealth, HealthStatus


def _counting_check(counter, delay=0.01):
    async def check():
        counter["n"] += 1
        await asyncio.sleep(delay)
        return ComponentHealth(name="dep", status=HealthStatus.HEALTHY, message="ok")
    return check


class TestSingleFlight:
    @pytest.mark.asyncio
    async def test_a_cold_cache_flood_runs_the_sweep_once(self):
        """The property that removes the amplification. Without the lock this is 50."""
        counter = {"n": 0}
        hc = HealthChecker(cache_ttl_seconds=5)
        hc.register_check("dep", _counting_check(counter))

        await asyncio.gather(*[hc.check_all() for _ in range(50)])
        assert counter["n"] == 1

    @pytest.mark.asyncio
    async def test_every_concurrent_caller_still_gets_a_real_result(self):
        counter = {"n": 0}
        hc = HealthChecker(cache_ttl_seconds=5)
        hc.register_check("dep", _counting_check(counter))

        results = await asyncio.gather(*[hc.check_all() for _ in range(20)])
        assert len(results) == 20
        assert all(r["status"] == HealthStatus.HEALTHY for r in results)
        assert all(r["summary"]["total_checks"] == 1 for r in results)


class TestCaching:
    @pytest.mark.asyncio
    async def test_repeat_calls_inside_the_window_do_not_re_probe(self):
        counter = {"n": 0}
        hc = HealthChecker(cache_ttl_seconds=5)
        hc.register_check("dep", _counting_check(counter))

        for _ in range(10):
            await hc.check_all()
        assert counter["n"] == 1

    @pytest.mark.asyncio
    async def test_cache_expires(self):
        counter = {"n": 0}
        hc = HealthChecker(cache_ttl_seconds=0.05)
        hc.register_check("dep", _counting_check(counter))

        await hc.check_all()
        await asyncio.sleep(0.08)
        await hc.check_all()
        assert counter["n"] == 2

    @pytest.mark.asyncio
    async def test_ttl_zero_disables_caching(self):
        """An operator must be able to turn this off without a code change."""
        counter = {"n": 0}
        hc = HealthChecker(cache_ttl_seconds=0)
        hc.register_check("dep", _counting_check(counter))

        for _ in range(5):
            await hc.check_all()
        assert counter["n"] == 5

    @pytest.mark.asyncio
    async def test_result_shape_is_unchanged_by_caching(self):
        """Jenkins' deploy gate and CloudWatch read this response; it must not move."""
        hc = HealthChecker(cache_ttl_seconds=5)
        hc.register_check("dep", _counting_check({"n": 0}))
        fresh = await hc.check_all()
        cached = await hc.check_all()

        for key in ("status", "message", "timestamp", "components", "summary"):
            assert key in fresh, key
        assert cached == fresh

    def test_ttl_read_from_environment(self, monkeypatch):
        monkeypatch.setenv("HEALTH_CACHE_TTL_SECONDS", "12.5")
        assert HealthChecker().cache_ttl_seconds == 12.5

    def test_malformed_ttl_falls_back_to_the_default(self, monkeypatch):
        """A typo in an env var must not stop a service from booting."""
        monkeypatch.setenv("HEALTH_CACHE_TTL_SECONDS", "banana")
        assert HealthChecker().cache_ttl_seconds == HealthChecker.DEFAULT_CACHE_TTL_SECONDS

    def test_negative_ttl_is_clamped(self, monkeypatch):
        monkeypatch.setenv("HEALTH_CACHE_TTL_SECONDS", "-5")
        assert HealthChecker().cache_ttl_seconds == 0


class TestClientReuse:
    @pytest.mark.asyncio
    async def test_redis_client_is_built_once_and_reused(self, monkeypatch):
        """Connect/probe/close per request is what made a single call cost 145 ms."""
        import redis.asyncio as aioredis

        import libs.health.checks as checks

        checks._CLIENTS.clear()
        built = {"n": 0}

        class _FakeRedis:
            async def ping(self): return True
            async def info(self): return {"redis_version": "7.0", "connected_clients": 1}

        def _from_url(url, **kw):
            built["n"] += 1
            return _FakeRedis()

        monkeypatch.setattr(aioredis, "from_url", _from_url)
        try:
            for _ in range(5):
                r = await checks.check_redis_health("redis://localhost:6379")
                assert r.status == HealthStatus.HEALTHY, r.message
            assert built["n"] == 1, "client must be built once, not per call"
        finally:
            checks._CLIENTS.clear()

    def test_clients_are_keyed_by_event_loop(self):
        """Both drivers bind their pool to the creating loop; a client from a dead loop
        silently fails every operation."""
        import libs.health.checks as checks

        async def key():
            return checks._loop_key("redis", "redis://x")

        k1 = asyncio.run(key())
        k2 = asyncio.run(key())
        assert k1[:2] == k2[:2] == ("redis", "redis://x")
        assert k1 != k2, "different loops must not share a client"
