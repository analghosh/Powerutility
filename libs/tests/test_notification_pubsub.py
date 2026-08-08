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
async def test_subscribe_failure_still_runs_cleanup():
    """If pubsub.subscribe raises, pubsub.close is still called."""
    from libs.notifications.pubsub import run_subscriber

    class _FailingPubSub:
        def __init__(self):
            self.close_called = False

        async def subscribe(self, channel):
            raise RuntimeError("subscribe failed")

        async def unsubscribe(self, channel):
            pass

        async def close(self):
            self.close_called = True

    class _FakeRedis:
        def __init__(self):
            self.pubsub_obj = _FailingPubSub()

        def pubsub(self):
            return self.pubsub_obj

    redis = _FakeRedis()
    registry = ConnectionRegistry()
    stop_event = asyncio.Event()

    with pytest.raises(RuntimeError, match="subscribe failed"):
        await run_subscriber(redis, registry, stop_event)

    assert redis.pubsub_obj.close_called


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
