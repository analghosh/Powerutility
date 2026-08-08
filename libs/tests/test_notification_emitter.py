import json

import pytest

from libs.notifications.emitter import NotificationEmitter, get_emitter, notify, set_emitter


class _Repo:
    def __init__(self):
        self.written = None

    async def insert_many_for(self, notifications):
        self.written = notifications
        return [f"id{i}" for i, _ in enumerate(notifications)]


class _Redis:
    def __init__(self):
        self.published = []

    async def publish(self, channel, payload):
        self.published.append((channel, payload))


class _Directory:
    def __init__(self, ids):
        self.ids = ids

    async def users_with_permission(self, permission):
        return list(self.ids)

    async def all_active_user_ids(self):
        return list(self.ids)


def _emitter(ids, repo=None, redis=None):
    return NotificationEmitter(
        repository=repo or _Repo(),
        directory=_Directory(ids),
        redis_client=redis or _Redis(),
    )


def _context():
    return {
        "market_type": "DAM",
        "approval_market": "dam",
        "approval_path": "/dam/block-bid-approval",
        "bid_count": 3,
        "schedule_date": "2026-08-09",
    }


@pytest.fixture(autouse=True)
def _reset_emitter_singleton():
    """The module-level singleton must never leak between tests."""
    set_emitter(None)
    yield
    set_emitter(None)


@pytest.mark.asyncio
async def test_writes_one_document_per_recipient():
    repo = _Repo()
    emitter = _emitter(["u1", "u2", "u3"], repo=repo)

    await emitter.notify(
        "block_bid.submitted_for_approval", _context(), {"user_id": "u9", "username": "A"}
    )

    assert [n.user_id for n in repo.written] == ["u1", "u2", "u3"]


@pytest.mark.asyncio
async def test_the_actor_never_notifies_themselves():
    repo = _Repo()
    emitter = _emitter(["u1", "u2"], repo=repo)

    await emitter.notify(
        "block_bid.submitted_for_approval", _context(), {"user_id": "u1", "username": "A"}
    )

    assert [n.user_id for n in repo.written] == ["u2"]


@pytest.mark.asyncio
async def test_one_notification_per_action_not_per_bid():
    """96 blocks must produce one document per recipient, carrying the count."""
    repo = _Repo()
    emitter = _emitter(["u1"], repo=repo)
    context = _context()
    context["bid_count"] = 96

    await emitter.notify(
        "block_bid.submitted_for_approval", context, {"user_id": "u9", "username": "A"}
    )

    assert len(repo.written) == 1
    assert "96" in repo.written[0].body
    assert repo.written[0].payload["bid_count"] == 96


@pytest.mark.asyncio
async def test_actor_name_falls_back_to_username_then_system():
    repo = _Repo()
    await _emitter(["u1"], repo=repo).notify(
        "block_bid.submitted_for_approval", _context(), {"user_id": "u9"}
    )
    assert "System" in repo.written[0].body


@pytest.mark.asyncio
async def test_publishes_once_per_recipient_naming_only_that_recipient():
    """Each recipient gets their own publish call, not one broadcast naming all of them.

    A single shared broadcast is what caused the original bug: every recipient but
    the first received a payload carrying someone else's document _id.
    """
    redis = _Redis()
    await _emitter(["u1", "u2"], redis=redis).notify(
        "block_bid.submitted_for_approval", _context(), {"user_id": "u9", "username": "A"}
    )

    assert len(redis.published) == 2
    for _channel, raw_payload in redis.published:
        envelope = json.loads(raw_payload)
        assert len(envelope["user_ids"]) == 1


@pytest.mark.asyncio
async def test_each_recipients_publish_carries_their_own_document_id():
    """insert_many_for preserves order: ids[i] belongs to recipients[i]. Each
    recipient's push must carry that id, not another recipient's."""
    repo = _Repo()
    redis = _Redis()
    emitter = _emitter(["u1", "u2", "u3"], repo=repo, redis=redis)

    ids = await emitter.notify(
        "block_bid.submitted_for_approval", _context(), {"user_id": "u9", "username": "A"}
    )

    assert ids == ["id0", "id1", "id2"]
    assert len(redis.published) == 3

    published_id_by_user = {}
    for _channel, raw_payload in redis.published:
        envelope = json.loads(raw_payload)
        assert len(envelope["user_ids"]) == 1
        published_id_by_user[envelope["user_ids"][0]] = envelope["notification"]["_id"]

    assert published_id_by_user == {"u1": "id0", "u2": "id1", "u3": "id2"}


@pytest.mark.asyncio
async def test_one_recipients_publish_failure_does_not_block_the_rest_or_lose_the_write():
    class _PartiallyBrokenRedis:
        def __init__(self, broken_user_id):
            self.broken_user_id = broken_user_id
            self.published = []

        async def publish(self, channel, payload):
            envelope = json.loads(payload)
            if self.broken_user_id in envelope["user_ids"]:
                raise ConnectionError("redis down for this recipient")
            self.published.append((channel, payload))

    repo = _Repo()
    redis = _PartiallyBrokenRedis(broken_user_id="u2")
    ids = await _emitter(["u1", "u2", "u3"], repo=repo, redis=redis).notify(
        "block_bid.submitted_for_approval", _context(), {"user_id": "u9", "username": "A"}
    )

    # The Mongo write is unaffected by any recipient's publish failure.
    assert len(repo.written) == 3
    assert ids == ["id0", "id1", "id2"]

    # u2's publish blew up, but u1 and u3 were still attempted and delivered.
    published_users = set()
    for _channel, raw_payload in redis.published:
        published_users.add(json.loads(raw_payload)["user_ids"][0])
    assert published_users == {"u1", "u3"}


@pytest.mark.asyncio
async def test_empty_audience_writes_nothing_and_publishes_nothing():
    repo, redis = _Repo(), _Redis()
    await _emitter([], repo=repo, redis=redis).notify(
        "block_bid.submitted_for_approval", _context(), {"user_id": "u9", "username": "A"}
    )

    assert repo.written in (None, [])
    assert redis.published == []


@pytest.mark.asyncio
async def test_a_redis_failure_does_not_lose_the_mongo_write():
    class _BrokenRedis:
        async def publish(self, channel, payload):
            raise ConnectionError("redis down")

    repo = _Repo()
    ids = await _emitter(["u1"], repo=repo, redis=_BrokenRedis()).notify(
        "block_bid.submitted_for_approval", _context(), {"user_id": "u9", "username": "A"}
    )

    assert len(repo.written) == 1
    assert ids == ["id0"]


@pytest.mark.asyncio
async def test_module_level_notify_is_a_noop_without_a_configured_emitter():
    assert get_emitter() is None

    result = await notify(
        "block_bid.submitted_for_approval", _context(), {"user_id": "u9", "username": "A"}
    )

    assert result == []
    assert get_emitter() is None


@pytest.mark.asyncio
async def test_module_level_notify_delegates_to_the_configured_emitter():
    repo = _Repo()
    configured = _emitter(["u1"], repo=repo)

    set_emitter(configured)
    assert get_emitter() is configured

    ids = await notify(
        "block_bid.submitted_for_approval", _context(), {"user_id": "u9", "username": "A"}
    )

    assert ids == ["id0"]
    assert [n.user_id for n in repo.written] == ["u1"]
