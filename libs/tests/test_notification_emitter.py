import pytest

from libs.notifications.emitter import NotificationEmitter


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
async def test_publishes_once_naming_all_recipients():
    redis = _Redis()
    await _emitter(["u1", "u2"], redis=redis).notify(
        "block_bid.submitted_for_approval", _context(), {"user_id": "u9", "username": "A"}
    )

    assert len(redis.published) == 1


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
