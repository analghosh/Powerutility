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
