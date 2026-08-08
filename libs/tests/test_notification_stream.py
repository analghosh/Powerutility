import asyncio
import json
import time

import pytest

import services.processing.app.v2.api.router.notification_routes as notification_routes
from libs.notifications.pubsub import ConnectionRegistry
from services.processing.app.v2.api.router.notification_routes import (
    HEARTBEAT_SECONDS,
    MAX_STREAM_SECONDS,
    QUEUE_MAXSIZE,
    compute_stream_deadline,
    format_heartbeat,
    format_sse,
    is_revoked,
    new_stream_queue,
    stream_events,
)


def test_sse_frame_has_event_data_and_a_blank_line_terminator():
    frame = format_sse("notification", {"title": "t"})
    assert frame == 'event: notification\ndata: {"title": "t"}\n\n'


def test_sse_data_is_single_line_json_so_the_frame_cannot_be_split():
    frame = format_sse("sync", {"items": [{"body": "line one\nline two"}]})
    body = frame.split("data: ", 1)[1]
    # Real newline count: 1 after the data line + 1 for the blank-line frame
    # terminator = 2. The embedded newline in the payload is JSON-escaped to
    # the two characters backslash+n (never a real \n -- JSON always escapes
    # control characters in strings), so it does not add to this count.
    assert body.count("\n") == 2
    assert json.loads(body.strip())["items"][0]["body"] == "line one\nline two"


def test_heartbeat_is_a_comment_the_browser_ignores():
    assert format_heartbeat() == ": ping\n\n"


def test_heartbeat_interval_leaves_margin_under_nginx_60s_default():
    assert HEARTBEAT_SECONDS == 25
    assert HEARTBEAT_SECONDS * 2 < 60


@pytest.mark.asyncio
async def test_revoked_token_is_detected():
    class _Redis:
        async def sismember(self, key, member):
            assert key == "jwt_blacklist"
            return member == "revoked-token"

    assert await is_revoked(_Redis(), "revoked-token") is True
    assert await is_revoked(_Redis(), "live-token") is False


@pytest.mark.asyncio
async def test_revocation_check_fails_open_when_redis_is_down():
    """Matches existing jwt_validator.py:213-214 behaviour. Documented, not new."""

    class _BrokenRedis:
        async def sismember(self, key, member):
            raise ConnectionError("redis down")

    assert await is_revoked(_BrokenRedis(), "any") is False


# --- Fakes for driving stream_events() directly, without a real server -----


class _FakeRequest:
    def __init__(self):
        self.disconnected = False

    async def is_disconnected(self):
        return self.disconnected


class _FakeRepository:
    def __init__(self, items=None, unread=0):
        self._items = items if items is not None else []
        self._unread = unread

    async def list_for_user(self, user_id, limit=50, skip=0):
        return self._items

    async def unread_count(self, user_id):
        return self._unread


class _RevocableRedis:
    """sismember reports True only for tokens explicitly revoked via .revoke()."""

    def __init__(self):
        self._revoked = set()

    def revoke(self, token):
        self._revoked.add(token)

    async def sismember(self, key, member):
        assert key == "jwt_blacklist"
        return member in self._revoked


# --- Finding 1: revocation must be re-checked on a time basis, not only ----
# --- when the queue happens to go idle long enough to time out.        ----


@pytest.mark.asyncio
async def test_revocation_is_rechecked_on_a_time_basis_even_when_the_queue_never_goes_idle(
    monkeypatch,
):
    """Regression test: a stream fed notifications faster than the heartbeat
    interval must still notice a revoked token within about one heartbeat
    interval. Gating the recheck on the asyncio.wait_for timeout branch means
    a busy stream never re-checks -- exactly the case the check exists for."""
    monkeypatch.setattr(notification_routes, "HEARTBEAT_SECONDS", 0.1)

    queue: asyncio.Queue = asyncio.Queue()
    registry = ConnectionRegistry()
    registry.add("u1", queue)
    repository = _FakeRepository()
    request = _FakeRequest()
    redis = _RevocableRedis()

    gen = notification_routes.stream_events(
        request,
        redis,
        registry,
        repository,
        user_id="u1",
        token="jwt-abc",
        deadline=time.time() + 3600,
        queue=queue,
    )

    sync_frame = await gen.asend(None)
    assert sync_frame.startswith("event: sync")

    # Keep the queue busy for well over one heartbeat interval, in gaps small
    # enough that asyncio.wait_for's timeout branch is (almost) never hit.
    # Right at a heartbeat-interval boundary a near-zero asyncio.wait_for
    # timeout can occasionally race an already-queued item (harmless: the
    # item isn't lost, it's picked up on the next pass), so a heartbeat frame
    # is an acceptable output here too -- the point of this loop is only that
    # the stream keeps running (does not stop early), not the exact frame mix.
    for _ in range(8):
        await queue.put({"title": "n"})
        frame = await gen.asend(None)
        assert frame.startswith("event: notification") or frame == ": ping\n\n"
        await asyncio.sleep(0.02)

    # Revoke, then keep the stream busy and confirm it still closes -- proving
    # the recheck is not gated on the queue going idle.
    redis.revoke("jwt-abc")
    stream_closed = False
    for _ in range(30):
        await queue.put({"title": "n"})
        try:
            frame = await gen.asend(None)
        except StopAsyncIteration:
            stream_closed = True
            break
        assert frame.startswith("event: notification") or frame == ": ping\n\n"
        await asyncio.sleep(0.02)

    assert stream_closed, "stream never closed after revocation on a busy queue"
    assert registry.queues_for("u1") == []


# --- Finding 2: a missing/zero/unparseable exp must still bound lifetime ---


def test_missing_exp_falls_back_to_a_bounded_lifetime():
    before = time.time()
    deadline = compute_stream_deadline(None)
    assert before < deadline <= before + MAX_STREAM_SECONDS + 1


def test_zero_exp_falls_back_to_a_bounded_lifetime():
    before = time.time()
    deadline = compute_stream_deadline(0)
    assert before < deadline <= before + MAX_STREAM_SECONDS + 1


def test_non_numeric_exp_does_not_raise_and_falls_back_to_a_bounded_lifetime():
    before = time.time()
    deadline = compute_stream_deadline("not-a-number")
    assert before < deadline <= before + MAX_STREAM_SECONDS + 1


def test_a_valid_exp_is_honoured_as_is():
    assert compute_stream_deadline(1893456000) == 1893456000.0


@pytest.mark.asyncio
async def test_stream_ends_once_the_lifetime_cap_is_passed():
    queue: asyncio.Queue = asyncio.Queue()
    registry = ConnectionRegistry()
    registry.add("u1", queue)
    repository = _FakeRepository()
    request = _FakeRequest()
    redis = _RevocableRedis()

    gen = notification_routes.stream_events(
        request,
        redis,
        registry,
        repository,
        user_id="u1",
        token="",
        deadline=time.time() - 1,  # already expired
        queue=queue,
    )

    await gen.asend(None)  # sync frame
    with pytest.raises(StopAsyncIteration):
        await gen.asend(None)

    assert registry.queues_for("u1") == []


# --- Finding 4: cover the generator's own behaviour directly ---------------


@pytest.mark.asyncio
async def test_registry_remove_is_called_when_the_generator_is_closed_early():
    queue: asyncio.Queue = asyncio.Queue()
    registry = ConnectionRegistry()
    registry.add("u1", queue)
    repository = _FakeRepository()
    request = _FakeRequest()
    redis = _RevocableRedis()

    gen = notification_routes.stream_events(
        request,
        redis,
        registry,
        repository,
        user_id="u1",
        token="",
        deadline=time.time() + 3600,
        queue=queue,
    )

    await gen.asend(None)  # sync frame; generator now parked awaiting the queue
    await gen.aclose()  # simulates the client disconnecting mid-wait

    assert registry.queues_for("u1") == []


@pytest.mark.asyncio
async def test_first_frame_is_the_sync_frame_carrying_items_and_unread_count():
    queue: asyncio.Queue = asyncio.Queue()
    registry = ConnectionRegistry()
    registry.add("u1", queue)
    repository = _FakeRepository(items=[{"_id": "1", "title": "hi"}], unread=3)
    request = _FakeRequest()
    redis = _RevocableRedis()

    gen = notification_routes.stream_events(
        request,
        redis,
        registry,
        repository,
        user_id="u1",
        token="",
        deadline=time.time() + 3600,
        queue=queue,
    )

    frame = await gen.asend(None)
    assert frame.startswith("event: sync\n")
    body = frame.split("data: ", 1)[1]
    payload = json.loads(body.strip())
    assert payload == {"items": [{"_id": "1", "title": "hi"}], "unreadCount": 3}

    await gen.aclose()


# --- Finding 3: the queue handed to the registry must be bounded -----------


def test_the_stream_queue_is_bounded():
    queue = new_stream_queue()
    assert queue.maxsize == QUEUE_MAXSIZE
    assert QUEUE_MAXSIZE > 0
