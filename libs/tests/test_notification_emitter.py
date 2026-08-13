import asyncio
import json
import time

import pytest

import libs.notifications.emitter as emitter_module
from libs.notifications.emitter import NotificationEmitter, get_emitter, notify, set_emitter


class _Repo:
    def __init__(self):
        self.written = None

    async def insert_many_for(self, notifications):
        self.written = notifications
        return [f"id{i}" for i, _ in enumerate(notifications)]


class _Redis:
    """Records published messages regardless of how they were sent.

    The emitter batches every recipient into one pipeline, so `published` is
    filled by the pipeline's buffered publishes. It still records a direct
    .publish() too, so a regression back to per-recipient round trips would keep
    these tests meaningful rather than silently recording nothing.
    """

    def __init__(self):
        self.published = []

    async def publish(self, channel, payload):
        self.published.append((channel, payload))

    def pipeline(self, transaction=False):
        outer = self

        class _Pipe:
            def publish(self, channel, payload):
                outer.published.append((channel, payload))
                return self

            async def execute(self):
                return [1] * len(outer.published)

        return _Pipe()


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
        "market_name": "iDAM",
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
async def test_a_broken_redis_does_not_lose_the_write_or_fail_the_caller():
    """Rewritten when publishing moved to a single pipeline.

    This test previously asserted PER-RECIPIENT isolation: one recipient's failed
    publish still left the others delivered. Batching every recipient into one
    round trip gives that up -- a failing execute drops the live push for the whole
    batch. The trade is deliberate: publish failures are connection-level, not
    per-recipient, so the isolation protected against a failure mode that does not
    occur, at a measured 75.3ms versus 6.5ms per bid decision.

    What must NOT change is the guarantee that actually matters: Redis failing
    never loses the durable write and never propagates to the caller, because the
    caller is a user approving a bid.
    """
    class _BrokenPipelineRedis:
        def pipeline(self, transaction=False):
            class _Pipe:
                def publish(self, channel, payload):
                    return self

                async def execute(self):
                    raise ConnectionError("redis down")

            return _Pipe()

    repo = _Repo()
    ids = await _emitter(["u1", "u2", "u3"], repo=repo, redis=_BrokenPipelineRedis()).notify(
        "block_bid.submitted_for_approval", _context(), {"user_id": "u9", "username": "A"}
    )

    assert len(repo.written) == 3
    assert ids == ["id0", "id1", "id2"]


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


class _PipelineRedis:
    """Records whether publishes were buffered into one pipeline or issued one
    at a time, so the test can assert round trips rather than just outcomes."""

    def __init__(self, execute_error=None):
        self.buffered = []
        self.execute_calls = 0
        self.direct_publish_calls = 0
        self._execute_error = execute_error

    def pipeline(self, transaction=False):
        outer = self

        class _Pipe:
            def publish(self, channel, payload):
                outer.buffered.append((channel, payload))
                return self

            async def execute(self):
                outer.execute_calls += 1
                if outer._execute_error is not None:
                    raise outer._execute_error
                return [1] * len(outer.buffered)

        return _Pipe()

    async def publish(self, channel, payload):  # pragma: no cover - must not be hit
        self.direct_publish_calls += 1


@pytest.mark.asyncio
async def test_all_recipients_are_published_in_a_single_round_trip():
    """One pipeline, not one round trip per recipient.

    Measured against the real stack with 116 recipients: 116 sequential
    `await publish(...)` calls cost 75.3ms, versus 6.5ms pipelined -- and that
    happens inside the approving user's request, so it was ~72% of the notification
    work on every bid decision. The cost grows with the audience, and grows again
    in PROD where Redis is a network hop rather than localhost.
    """
    repo = _Repo()
    redis = _PipelineRedis()

    ids = await _emitter(["u1", "u2", "u3"], repo=repo, redis=redis).notify(
        "block_bid.submitted_for_approval", _context(), {"user_id": "u9", "username": "A"}
    )

    assert redis.execute_calls == 1, "one pipeline execute for the whole audience"
    assert redis.direct_publish_calls == 0, "no per-recipient round trips"
    assert len(redis.buffered) == 3

    # Each recipient must still carry THEIR OWN document _id -- that is why this
    # is a pipeline of distinct payloads and not one broadcast message.
    published_id_by_user = {
        json.loads(raw)["user_ids"][0]: json.loads(raw)["notification"]["_id"]
        for _channel, raw in redis.buffered
    }
    assert published_id_by_user == {"u1": "id0", "u2": "id1", "u3": "id2"}
    assert ids == ["id0", "id1", "id2"]


@pytest.mark.asyncio
async def test_a_pipeline_failure_does_not_lose_the_write_or_raise():
    """Redis failing must never fail the bid decision that triggered it.

    Pipelining trades per-recipient isolation for one round trip: a failing
    execute drops the LIVE push for the whole batch. Nothing is lost -- the Mongo
    write is already durable, so every recipient still sees the notification on
    their next sync frame or page load.
    """
    repo = _Repo()
    redis = _PipelineRedis(execute_error=ConnectionError("redis down"))

    ids = await _emitter(["u1", "u2"], repo=repo, redis=redis).notify(
        "block_bid.submitted_for_approval", _context(), {"user_id": "u9", "username": "A"}
    )

    assert len(repo.written) == 2
    assert ids == ["id0", "id1"]


# --- Review finding: a hung Redis must not stall the bid request ----------


@pytest.mark.asyncio
async def test_a_hung_redis_publish_does_not_stall_the_caller(monkeypatch):
    """notify() is awaited inline between the service call and the HTTP
    response on every bid write path (the four market controllers and
    block_bid_services). The publish client carries socket_timeout=5 with
    retry_on_timeout=True, so a Redis that accepts the connection but never
    answers -- a BGSAVE stall, a failover, a packet-dropping partition -- adds
    5-10s to EVERY submit and approve, and a mixed block-bid decision issues
    several notify calls in a row. That lands squarely on gate closure, and
    the frontend's own axios timeout then fires on requests that actually
    succeeded, inviting duplicate resubmits.

    The Mongo write above the publish is the durability anchor, so abandoning
    a slow publish costs at most a live push -- which the next sync frame
    repairs -- and never the notification itself.
    """
    monkeypatch.setattr(emitter_module, "PUBLISH_TIMEOUT_SECONDS", 0.05)

    class _HungRedis:
        def pipeline(self, transaction=False):
            class _Pipe:
                def publish(self, channel, payload):
                    return self

                async def execute(self):
                    await asyncio.sleep(5)

            return _Pipe()

    repo = _Repo()
    started = time.monotonic()
    ids = await _emitter(["u1", "u2"], repo=repo, redis=_HungRedis()).notify(
        "block_bid.submitted_for_approval", _context(), {"user_id": "u9", "username": "A"}
    )
    elapsed = time.monotonic() - started

    assert elapsed < 1, f"notify() blocked the caller for {elapsed:.2f}s"
    assert ids == ["id0", "id1"], "the durable write must still be returned"
    assert len(repo.written) == 2


@pytest.mark.asyncio
async def test_the_publish_budget_is_far_above_a_healthy_round_trip():
    """116 recipients pipelined measured 6.5ms against the real stack. The
    budget must leave room for PROD, where Redis is a network hop, without
    ever approaching the 5s socket timeout it exists to pre-empt."""
    assert 0.1 <= emitter_module.PUBLISH_TIMEOUT_SECONDS <= 2.0


@pytest.mark.asyncio
async def test_a_healthy_publish_is_still_delivered_in_full():
    """Bounding the publish must not drop pushes on a working Redis."""
    repo = _Repo()
    redis = _PipelineRedis()

    ids = await _emitter(["u1", "u2", "u3"], repo=repo, redis=redis).notify(
        "block_bid.submitted_for_approval", _context(), {"user_id": "u9", "username": "A"}
    )

    assert redis.execute_calls == 1
    assert len(redis.buffered) == 3
    assert ids == ["id0", "id1", "id2"]
