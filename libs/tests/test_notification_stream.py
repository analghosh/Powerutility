import json

import pytest

from services.processing.app.v2.api.router.notification_routes import (
    HEARTBEAT_SECONDS,
    format_heartbeat,
    format_sse,
    is_revoked,
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
