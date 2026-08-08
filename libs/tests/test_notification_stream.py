import asyncio
import json
import time

import pytest

from fastapi import HTTPException

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
    """The key checked must match what the auth service's logout path
    actually writes (services/auth/app/services/redis_blacklist.py:
    invalidate_access_token() -> SETEX blacklist:access:{token}), against a
    SEPARATE Redis connection (the blacklist client, not notification_redis).
    Hardcoded literal, not built via notification_routes.blacklist_key(), so
    this test is grounded in the real contract rather than the module
    testing itself. Real redis-py EXISTS returns an int, not a bool."""

    class _Redis:
        async def exists(self, key):
            assert key == "blacklist:access:revoked-token"
            return 1

    assert await is_revoked(_Redis(), "revoked-token") is True


@pytest.mark.asyncio
async def test_live_token_is_not_revoked():
    class _Redis:
        async def exists(self, key):
            assert key == "blacklist:access:live-token"
            return 0

    assert await is_revoked(_Redis(), "live-token") is False


@pytest.mark.asyncio
async def test_revocation_check_fails_open_when_redis_is_down():
    """Matches existing jwt_validator.py:213-214 behaviour. Documented, not new."""

    class _BrokenRedis:
        async def exists(self, key):
            raise ConnectionError("redis down")

    assert await is_revoked(_BrokenRedis(), "any") is False


@pytest.mark.asyncio
async def test_revocation_check_fails_open_when_blacklist_client_is_none():
    """Startup (main.py's lifespan) degrades the blacklist client to None,
    rather than raising, when it cannot be constructed -- notifications-wide
    fault tolerance choice from Task 9. is_revoked must treat that the same
    as a raising client, not error out with AttributeError."""
    assert await is_revoked(None, "any") is False


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
    """Stands in for the blacklist client (not notification_redis) passed to
    stream_events(). .exists() reports true only for tokens explicitly
    revoked via .revoke(); the key-format assertion keeps every test that
    uses this fake honest against the real blacklist:access:{token} contract."""

    def __init__(self):
        self._revoked = set()

    def revoke(self, token):
        self._revoked.add(token)

    async def exists(self, key):
        assert key.startswith("blacklist:access:")
        token = key[len("blacklist:access:"):]
        return int(token in self._revoked)


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


class _AlwaysReadyQueue:
    """A .get() that resolves before asyncio.wait_for's timeout branch can
    ever be taken, regardless of the timeout value -- including exactly 0.

    A plain coroutine-based fake (or a real asyncio.Queue with an item
    pre-loaded) is not good enough here: asyncio.wait_for has a fast path for
    timeout <= 0 that checks `fut.done()` immediately, before a freshly
    created Task has ever had a chance to run its first step. That is what
    made the round-1 regression test's timing race real rather than
    theoretical -- a near-zero wait_budget could occasionally beat an
    already-queued item. Returning an *already-completed* Future sidesteps
    Task scheduling entirely, so delivery always wins, deterministically.
    """

    def __init__(self, item=None):
        self._item = item if item is not None else {"title": "n"}

    def get(self):
        fut = asyncio.get_running_loop().create_future()
        fut.set_result(self._item)
        return fut


@pytest.mark.asyncio
async def test_revocation_is_rechecked_even_when_delivery_always_wins_the_race(
    monkeypatch,
):
    """Gap-1 fix from round 2: the sibling busy-queue test above can pass
    even if the time-based `due_for_check` gating alone is reverted to the
    old except-branch-only form, because its near-zero wait_budget only
    *sometimes* loses the race against an already-queued item. This test
    removes that race entirely -- delivery always wins, every iteration --
    so the only thing that can make the stream close is the time-based
    check firing on its own schedule."""
    monkeypatch.setattr(notification_routes, "HEARTBEAT_SECONDS", 0.05)

    queue = _AlwaysReadyQueue()
    registry = ConnectionRegistry()
    registry.add("u1", queue)
    repository = _FakeRepository()
    request = _FakeRequest()
    redis = _RevocableRedis()
    redis.revoke("jwt-abc")  # already revoked before the stream opens

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

    await gen.asend(None)  # sync frame

    start = time.monotonic()
    stream_closed = False
    # Bounded by real elapsed time, not an iteration count: delivery never
    # waits, so how many iterations fit in one heartbeat interval depends on
    # this machine's per-iteration overhead, not a number chosen up front.
    while time.monotonic() - start < 2.0:
        try:
            await gen.asend(None)
        except StopAsyncIteration:
            stream_closed = True
            break

    assert stream_closed, (
        "stream never closed even though the token was revoked and delivery "
        "always won the race -- the time-based revocation check did not fire"
    )
    assert registry.queues_for("u1") == []


# --- Gap 2: a session with no usable token must not open a stream ----------


class _FakeAppState:
    def __init__(self):
        self.notification_redis = _RevocableRedis()
        self.notification_registry = ConnectionRegistry()
        self.notification_repository = _FakeRepository()
        # /stream reads this unconditionally (before the ticket is even
        # checked), so it must exist even though this test never revokes
        # anything.
        self.notification_blacklist_redis = _RevocableRedis()


class _FakeApp:
    def __init__(self):
        self.state = _FakeAppState()


class _FakeStreamRequest:
    """Enough of a Request to drive the /stream route handler directly."""

    def __init__(self):
        self.app = _FakeApp()
        self.disconnected = False

    async def is_disconnected(self):
        return self.disconnected


@pytest.mark.asyncio
async def test_stream_route_rejects_a_session_with_no_usable_token(monkeypatch):
    """A stream whose session carries no token can never be checked against
    the revocation blacklist (is_revoked's `if token and ...` guard skips the
    check entirely), so it must be refused up front rather than allowed to
    open as an unrevocable stream. Covers both an empty-string token and a
    session that omits the "token" key altogether."""

    async def _consume_with_empty_token(redis_client, ticket):
        return {"user_id": "u1", "token": "", "exp": time.time() + 3600}

    async def _consume_with_missing_token(redis_client, ticket):
        return {"user_id": "u1", "exp": time.time() + 3600}

    for fake_consume in (_consume_with_empty_token, _consume_with_missing_token):
        monkeypatch.setattr(notification_routes, "consume_ticket", fake_consume)
        request = _FakeStreamRequest()

        with pytest.raises(HTTPException) as exc_info:
            await notification_routes.stream(request, ticket="whatever")

        assert exc_info.value.status_code == 401
        # No queue should have been registered for a rejected session.
        assert request.app.state.notification_registry.queues_for("u1") == []


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
