"""
Minimal in-process Redis double, shared by the rate-limiter suites.

Implements only the handful of commands the limiters use, but it means the Redis
branches -- the ones that actually run in production -- are tested rather than only
the in-process fallback. Lifted out of `test_mfa_rate_limiter` when the login limiter
needed the same double.
"""

import time


class _FakePipeline:
    def __init__(self, redis):
        self._redis = redis
        self._queued = []

    def __getattr__(self, name):
        def _queue(*args, **kwargs):
            self._queued.append((name, args, kwargs))
            return self
        return _queue

    def execute(self):
        results = []
        for name, args, kwargs in self._queued:
            results.append(getattr(self._redis, name)(*args, **kwargs))
        self._queued = []
        return results


class _FakeRedis:
    """Just enough Redis: strings with TTL, sorted sets, pipelines."""

    def __init__(self):
        self.strings = {}   # key -> (value, expiry)
        self.zsets = {}     # key -> ({member: score}, expiry)
        self.calls = 0

    # -- helpers
    def _live(self, store, key):
        entry = store.get(key)
        if entry is None:
            return None
        value, expiry = entry
        if expiry is not None and expiry <= time.time():
            del store[key]
            return None
        return value

    # -- commands
    def ping(self):
        return True

    def get(self, key):
        self.calls += 1
        return self._live(self.strings, key)

    def set(self, key, value, nx=False, ex=None):
        self.calls += 1
        if nx and self._live(self.strings, key) is not None:
            return None
        self.strings[key] = (value, time.time() + ex if ex else None)
        return True

    def setex(self, key, ttl, value):
        self.calls += 1
        self.strings[key] = (value, time.time() + ttl)
        return True

    def incr(self, key):
        self.calls += 1
        current = int(self._live(self.strings, key) or 0) + 1
        expiry = self.strings.get(key, (None, None))[1]
        self.strings[key] = (str(current), expiry)
        return current

    def expire(self, key, ttl):
        self.calls += 1
        for store in (self.strings, self.zsets):
            if key in store:
                store[key] = (store[key][0], time.time() + ttl)
        return True

    def ttl(self, key):
        self.calls += 1
        entry = self.strings.get(key) or self.zsets.get(key)
        if entry is None or entry[1] is None:
            return -2 if entry is None else -1
        remaining = entry[1] - time.time()
        return int(remaining) if remaining > 0 else -2

    def delete(self, *keys):
        self.calls += 1
        removed = 0
        for key in keys:
            removed += self.strings.pop(key, None) is not None
            removed += self.zsets.pop(key, None) is not None
        return removed

    def zadd(self, key, mapping):
        self.calls += 1
        members, expiry = self.zsets.get(key, ({}, None))
        members.update(mapping)
        self.zsets[key] = (members, expiry)
        return len(mapping)

    def zcard(self, key):
        self.calls += 1
        members = self._live(self.zsets, key)
        return len(members) if members else 0

    def zremrangebyscore(self, key, minimum, maximum):
        self.calls += 1
        members = self._live(self.zsets, key)
        if not members:
            return 0
        low = float("-inf") if minimum == "-inf" else float(minimum)
        doomed = [m for m, score in members.items() if low <= score <= float(maximum)]
        for member in doomed:
            del members[member]
        return len(doomed)

    def pipeline(self):
        return _FakePipeline(self)
