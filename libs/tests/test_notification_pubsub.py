import asyncio
import json

import pytest

from libs.notifications.pubsub import CHANNEL, ConnectionRegistry, publish


def test_registry_returns_only_the_matching_users_queues():
    registry = ConnectionRegistry()
    q_a1, q_a2, q_b = asyncio.Queue(), asyncio.Queue(), asyncio.Queue()
    registry.add("A", q_a1)
    registry.add("A", q_a2)
    registry.add("B", q_b)

    assert set(registry.queues_for("A")) == {q_a1, q_a2}
    assert registry.queues_for("B") == [q_b]
    assert registry.queues_for("nobody") == []
    assert registry.count() == 3


def test_removing_the_last_queue_drops_the_user_entry():
    registry = ConnectionRegistry()
    q = asyncio.Queue()
    registry.add("A", q)
    registry.remove("A", q)

    assert registry.queues_for("A") == []
    assert registry.count() == 0
    assert "A" not in registry._by_user


def test_removing_an_unknown_queue_is_a_no_op():
    registry = ConnectionRegistry()
    registry.remove("ghost", asyncio.Queue())
    assert registry.count() == 0


@pytest.mark.asyncio
async def test_publish_sends_one_message_naming_every_recipient():
    class _FakeRedis:
        def __init__(self):
            self.published = []

        async def publish(self, channel, payload):
            self.published.append((channel, payload))

    redis = _FakeRedis()
    await publish(redis, ["u1", "u2"], {"title": "t", "_id": "n1"})

    assert len(redis.published) == 1
    channel, payload = redis.published[0]
    assert channel == CHANNEL
    assert json.loads(payload) == {
        "user_ids": ["u1", "u2"],
        "notification": {"title": "t", "_id": "n1"},
    }


@pytest.mark.asyncio
async def test_publish_with_no_recipients_sends_nothing():
    class _Exploding:
        async def publish(self, channel, payload):
            raise AssertionError("must not publish to an empty audience")

    await publish(_Exploding(), [], {"title": "t"})


def test_adding_same_queue_twice_is_idempotent():
    """Adding the same queue twice for one user is a no-op."""
    registry = ConnectionRegistry()
    q = asyncio.Queue()
    registry.add("A", q)
    registry.add("A", q)

    assert registry.queues_for("A") == [q]
    assert registry.count() == 1


@pytest.mark.asyncio
async def test_subscribe_failure_still_runs_cleanup(monkeypatch):
    """If pubsub.subscribe raises, pubsub.close is still called.

    Updated for the Redis-reconnect fix: this used to also assert that the
    RuntimeError propagated out of run_subscriber, i.e. that the task DIED on
    a failed subscribe. That is precisely the behaviour the reconnect loop
    removes -- a failed subscribe is the case re-subscribing exists for. The
    cleanup guarantee this test is named for is unchanged and still asserted;
    only the die-on-failure half is replaced with retry-and-stay-alive.
    """
    from libs.notifications import pubsub as pubsub_module

    monkeypatch.setattr(pubsub_module, "subscriber_backoff_seconds", lambda attempt: 0)

    class _FailingPubSub:
        def __init__(self):
            self.close_called = False

        async def subscribe(self, channel):
            await asyncio.sleep(0)
            raise RuntimeError("subscribe failed")

        async def unsubscribe(self, channel):
            pass

        async def close(self):
            self.close_called = True

    class _FakeRedis:
        def __init__(self):
            self.connections = []

        def pubsub(self):
            connection = _FailingPubSub()
            self.connections.append(connection)
            return connection

    redis = _FakeRedis()
    stop_event = asyncio.Event()

    task = asyncio.create_task(
        pubsub_module.run_subscriber(redis, ConnectionRegistry(), stop_event)
    )
    await _run_until(lambda: len(redis.connections) >= 3)

    assert not task.done(), "a failed subscribe must be retried, not fatal"
    assert all(c.close_called for c in redis.connections[:-1]), (
        "every abandoned connection must still be closed"
    )

    stop_event.set()
    await asyncio.wait_for(task, timeout=2)
    assert redis.connections[-1].close_called


@pytest.mark.asyncio
async def test_failing_queue_does_not_prevent_other_recipients():
    """A queue whose put_nowait raises does not prevent other queues from receiving."""
    from libs.notifications.pubsub import run_subscriber

    class _FailingQueue:
        def put_nowait(self, item):
            raise RuntimeError("queue full")

    class _GoodQueue:
        def __init__(self):
            self.items = []

        def put_nowait(self, item):
            self.items.append(item)

    class _FakePubSub:
        def __init__(self):
            self.call_count = 0

        async def subscribe(self, channel):
            pass

        async def get_message(self, ignore_subscribe_messages=False, timeout=None):
            self.call_count += 1
            if self.call_count == 1:
                return {"data": json.dumps({
                    "user_ids": ["u1", "u2"],
                    "notification": {"title": "test"}
                })}
            # Return None immediately, yielding control
            await asyncio.sleep(0)
            return None

        async def unsubscribe(self, channel):
            pass

        async def close(self):
            pass

    class _FakeRedis:
        def __init__(self):
            self.pubsub_obj = _FakePubSub()

        def pubsub(self):
            return self.pubsub_obj

    redis = _FakeRedis()
    registry = ConnectionRegistry()

    failing_q = _FailingQueue()
    good_q = _GoodQueue()
    registry.add("u1", failing_q)
    registry.add("u2", good_q)

    stop_event = asyncio.Event()

    async def set_stop_later():
        # Let one iteration complete, then set stop
        await asyncio.sleep(0.02)
        stop_event.set()

    # Run both concurrently
    await asyncio.gather(
        run_subscriber(redis, registry, stop_event),
        set_stop_later()
    )

    # The good queue should have received the notification despite u1's queue failing
    assert good_q.items == [{"title": "test"}]


# ── Redis reconnect: the subscriber must survive a Redis outage ──────────────
#
# Proven against the real container: stop epms-redis and get_message raises
# redis.exceptions.ConnectionError, which propagated out of run_subscriber's
# while loop and ENDED THE TASK. After starting Redis again PUBSUB NUMSUB
# epms:notifications read 0 -- nothing listening -- so every live push was
# silently lost for every user on that worker until the service was restarted.
# The REST sync path still filled the bell on reload, so nothing looked broken.


class _ScriptedPubSub:
    """One pub/sub connection, driven by a script of get_message outcomes.

    Each script entry is either an exception instance (raised) or a value
    (returned). Once the script is exhausted the connection idles, returning
    None -- always after awaiting, so a fake can never spin the consume loop
    without yielding to the event loop.
    """

    def __init__(self, script, broker=None):
        self.script = list(script)
        self.broker = broker
        self.subscribed = []
        self.unsubscribed = []
        self.closed = False

    async def subscribe(self, channel):
        self.subscribed.append(channel)
        if self.broker is not None:
            self.broker.subscribe_happened()

    async def get_message(self, ignore_subscribe_messages=False, timeout=None):
        await asyncio.sleep(0)
        if not self.script:
            await asyncio.sleep(0.002)
            return None
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    async def unsubscribe(self, channel):
        self.unsubscribed.append(channel)
        if self.broker is not None:
            self.broker.unsubscribe_happened()

    async def close(self):
        self.closed = True


class _ScriptedRedis:
    """Hands out a FRESH pubsub per connection cycle, like the real client."""

    def __init__(self, *scripts, count_subscriptions=False):
        self.scripts = list(scripts)
        self.connections = []
        self.count_subscriptions = count_subscriptions
        self.live_subscriptions = 0
        self.max_live_subscriptions = 0

    def pubsub(self):
        script = self.scripts.pop(0) if self.scripts else []
        connection = _ScriptedPubSub(script, broker=self if self.count_subscriptions else None)
        self.connections.append(connection)
        return connection

    # The PUBSUB NUMSUB analogue: how many subscriptions the server would see.
    def subscribe_happened(self):
        self.live_subscriptions += 1
        self.max_live_subscriptions = max(self.max_live_subscriptions, self.live_subscriptions)

    def unsubscribe_happened(self):
        self.live_subscriptions -= 1


def _message(user_ids, notification):
    return {"data": json.dumps({"user_ids": user_ids, "notification": notification})}


class _RecordingQueue:
    def __init__(self):
        self.items = []

    def put_nowait(self, item):
        self.items.append(item)


async def _run_until(condition, timeout=2.0):
    """Poll until `condition()` is true, failing the test rather than hanging."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_a_connection_error_resubscribes_and_delivery_continues(monkeypatch):
    """The core defect: one ConnectionError killed the subscriber for good.

    It must instead close that connection, re-subscribe on a fresh one, and go
    on delivering -- without the task ever exiting.
    """
    from libs.notifications import pubsub as pubsub_module

    monkeypatch.setattr(pubsub_module, "subscriber_backoff_seconds", lambda attempt: 0)

    redis = _ScriptedRedis(
        [ConnectionError("Connection closed by server.")],
        [_message(["u1"], {"_id": "n1", "title": "after reconnect"})],
    )
    registry = ConnectionRegistry()
    queue = _RecordingQueue()
    registry.add("u1", queue)
    stop_event = asyncio.Event()

    task = asyncio.create_task(
        pubsub_module.run_subscriber(redis, registry, stop_event)
    )
    await _run_until(lambda: queue.items)

    assert queue.items == [{"_id": "n1", "title": "after reconnect"}]
    assert not task.done(), "the subscriber task must not exit on a connection error"
    assert len(redis.connections) == 2, "a reconnect must use a fresh pubsub connection"
    assert redis.connections[0].closed, "the failed connection must be closed"
    assert redis.connections[1].subscribed == [CHANNEL], "it must re-subscribe"

    stop_event.set()
    await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
async def test_repeated_failures_keep_retrying_with_a_growing_wait(monkeypatch):
    """A long outage must not exhaust the subscriber into exiting, and must not
    hammer Redis at full speed either."""
    from libs.notifications import pubsub as pubsub_module

    requested_attempts = []

    def _record(attempt):
        requested_attempts.append(attempt)
        return 0

    monkeypatch.setattr(pubsub_module, "subscriber_backoff_seconds", _record)

    redis = _ScriptedRedis(*([[ConnectionError("down")]] * 6))
    stop_event = asyncio.Event()
    task = asyncio.create_task(
        pubsub_module.run_subscriber(redis, ConnectionRegistry(), stop_event)
    )
    await _run_until(lambda: len(requested_attempts) >= 5)

    assert not task.done(), "repeated failures must retry, never exit"
    # The attempt counter must actually climb, or the backoff never grows.
    assert requested_attempts[:5] == [0, 1, 2, 3, 4]

    stop_event.set()
    await asyncio.wait_for(task, timeout=2)


def test_the_backoff_grows_and_is_capped_and_jittered():
    """The wait itself: exponential, capped, and jittered so that N workers
    reconnecting after one Redis restart do not stampede in the same instant."""
    from libs.notifications.pubsub import (
        SUBSCRIBER_MAX_BACKOFF_SECONDS,
        subscriber_backoff_seconds,
    )

    assert 0.5 <= subscriber_backoff_seconds(0) <= 1.0
    assert min(subscriber_backoff_seconds(4) for _ in range(20)) >= subscriber_backoff_seconds(0) / 2
    for attempt in range(0, 25):
        assert subscriber_backoff_seconds(attempt) <= SUBSCRIBER_MAX_BACKOFF_SECONDS
    assert len({subscriber_backoff_seconds(10) for _ in range(50)}) > 1


@pytest.mark.asyncio
async def test_stop_event_set_during_a_retry_wait_ends_the_task_promptly():
    """Shutdown must never be made to wait out a backoff -- the retry wait has
    to be on stop_event, not a bare sleep."""
    from libs.notifications import pubsub as pubsub_module

    redis = _ScriptedRedis(*([[ConnectionError("down")]] * 4))
    stop_event = asyncio.Event()
    task = asyncio.create_task(
        pubsub_module.run_subscriber(redis, ConnectionRegistry(), stop_event)
    )
    # Let it fail once and settle into a real (uncapped, unpatched) retry wait.
    await _run_until(lambda: redis.connections and redis.connections[0].closed)

    started = asyncio.get_running_loop().time()
    stop_event.set()
    await asyncio.wait_for(task, timeout=2)
    elapsed = asyncio.get_running_loop().time() - started

    # The real first backoff is 0.5-1.0s; stopping must not wait it out.
    assert elapsed < 0.4, f"shutdown waited out the backoff ({elapsed:.2f}s)"


@pytest.mark.asyncio
async def test_cancellation_during_a_retry_wait_still_raises_cancelled_error():
    """Task 4 established that CancelledError propagates; the reconnect loop
    must catch Exception, never BaseException, or service shutdown breaks."""
    from libs.notifications import pubsub as pubsub_module

    redis = _ScriptedRedis(*([[ConnectionError("down")]] * 4))
    stop_event = asyncio.Event()
    task = asyncio.create_task(
        pubsub_module.run_subscriber(redis, ConnectionRegistry(), stop_event)
    )
    await _run_until(lambda: redis.connections and redis.connections[0].closed)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_a_reconnect_does_not_leave_two_subscriptions(monkeypatch):
    """PUBSUB NUMSUB must read 1 after a reconnect, never 2: the old connection
    has to be unsubscribed and closed before a new one subscribes."""
    from libs.notifications import pubsub as pubsub_module

    monkeypatch.setattr(pubsub_module, "subscriber_backoff_seconds", lambda attempt: 0)

    redis = _ScriptedRedis(
        [ConnectionError("down")],
        [ConnectionError("down again")],
        [_message(["u1"], {"_id": "n1"})],
        count_subscriptions=True,
    )
    registry = ConnectionRegistry()
    queue = _RecordingQueue()
    registry.add("u1", queue)
    stop_event = asyncio.Event()

    task = asyncio.create_task(
        pubsub_module.run_subscriber(redis, registry, stop_event)
    )
    await _run_until(lambda: queue.items)

    assert redis.max_live_subscriptions == 1, (
        f"two overlapping subscriptions ({redis.max_live_subscriptions}); "
        "PUBSUB NUMSUB would read more than 1"
    )

    stop_event.set()
    await asyncio.wait_for(task, timeout=2)
    assert redis.live_subscriptions == 0


@pytest.mark.asyncio
async def test_a_read_timeout_does_not_tear_down_a_healthy_subscription(monkeypatch):
    """The pub/sub client carries socket_timeout=5 (main.py). A read timeout on
    a channel that simply has no traffic is not a connection failure: it must
    not reconnect, and must not drop the subscription."""
    import redis.exceptions

    from libs.notifications import pubsub as pubsub_module

    def _explode(attempt):
        raise AssertionError("a benign read timeout must not trigger a reconnect")

    monkeypatch.setattr(pubsub_module, "subscriber_backoff_seconds", _explode)

    redis_client = _ScriptedRedis(
        [
            redis.exceptions.TimeoutError("Timeout reading from localhost:6380"),
            TimeoutError("asyncio read timeout"),
            _message(["u1"], {"_id": "n1", "title": "still alive"}),
        ]
    )
    registry = ConnectionRegistry()
    queue = _RecordingQueue()
    registry.add("u1", queue)
    stop_event = asyncio.Event()

    task = asyncio.create_task(
        pubsub_module.run_subscriber(redis_client, registry, stop_event)
    )
    await _run_until(lambda: queue.items)

    assert queue.items == [{"_id": "n1", "title": "still alive"}]
    assert len(redis_client.connections) == 1, "a read timeout must not reconnect"
    assert not redis_client.connections[0].closed

    stop_event.set()
    await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
async def test_a_connection_that_stayed_up_earns_a_fresh_backoff(monkeypatch):
    """A subscriber that ran healthily for a long time and then lost Redis must
    restart at the base delay, not inherit a large backoff from some unrelated
    outage earlier in the process's life. The counter resets only for a
    connection that actually survived (SUBSCRIBER_HEALTHY_SECONDS) -- one that
    subscribes and dies instantly does not qualify, which is what stops a
    flapping Redis from pinning the delay at the base value."""
    from libs.notifications import pubsub as pubsub_module

    requested_attempts = []
    monkeypatch.setattr(
        pubsub_module,
        "subscriber_backoff_seconds",
        lambda attempt: requested_attempts.append(attempt) or 0,
    )
    # Every connection counts as healthy the moment it subscribes.
    monkeypatch.setattr(pubsub_module, "SUBSCRIBER_HEALTHY_SECONDS", 0.0)

    redis = _ScriptedRedis(*([[ConnectionError("down")]] * 5))
    stop_event = asyncio.Event()
    task = asyncio.create_task(
        pubsub_module.run_subscriber(redis, ConnectionRegistry(), stop_event)
    )
    await _run_until(lambda: len(requested_attempts) >= 3)

    assert requested_attempts[:3] == [0, 0, 0], (
        "a healthy connection must reset the backoff each time"
    )

    stop_event.set()
    await asyncio.wait_for(task, timeout=2)


# --- Review finding: the registry must be able to report a per-user count --
# The /stream route needs this to cap how many concurrent streams one
# authenticated session can hold open; count() alone is process-wide and
# would let one user's connections lock out everybody else.


def test_registry_counts_streams_per_user():
    registry = ConnectionRegistry()
    registry.add("A", asyncio.Queue())
    registry.add("A", asyncio.Queue())
    registry.add("B", asyncio.Queue())

    assert registry.count_for("A") == 2
    assert registry.count_for("B") == 1
    assert registry.count_for("nobody") == 0


def test_per_user_count_drops_as_streams_are_removed():
    registry = ConnectionRegistry()
    q = asyncio.Queue()
    registry.add("A", q)
    registry.add("A", asyncio.Queue())

    registry.remove("A", q)

    assert registry.count_for("A") == 1
