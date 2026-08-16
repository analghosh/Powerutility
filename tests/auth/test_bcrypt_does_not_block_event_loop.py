"""bcrypt must never run on the event loop.

bcrypt is CPU-bound C code with no await in it. Called directly from an `async def`
it holds the loop for its full duration -- ~250ms per verification at
BCRYPT_ROUNDS=12 -- and the auth service can serve nothing else meanwhile.

Measured against the real stack before this was fixed: 60 concurrent logins took
15-20s, and `GET /health` -- a route that touches nothing -- went from 1-3ms to
14,101ms for the duration. Worse, every other service validates every request by
HTTP call to this one, hits its 10s httpx timeout, and fails CLOSED with 401. A
login rush therefore 401s the whole platform.

These tests pin the behaviour rather than the implementation: they assert that a
concurrent task keeps getting scheduled while bcrypt runs. Any offloading
mechanism passes; running bcrypt inline fails.
"""

import asyncio
import time

import pytest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

with patch("pymongo.MongoClient"):
    with patch("redis.Redis"):
        from services.auth.app.repository.user_repository import UserRepository


# Long enough that an inline call is unmistakably blocking, short enough to keep
# the suite fast. Real bcrypt at 12 rounds costs roughly this much anyway.
FAKE_BCRYPT_SECONDS = 0.3
TICK_SECONDS = 0.01

# An event loop that is never blocked ticks ~30 times over FAKE_BCRYPT_SECONDS.
# One that is blocked ticks 0 or 1. The threshold sits far from both so the test
# does not flake on a loaded CI box.
MIN_EXPECTED_TICKS = 5


async def _count_ticks_until(stop: asyncio.Event) -> int:
    """Increment once per TICK_SECONDS until told to stop.

    This is the probe. If the loop is free it advances steadily; if bcrypt is
    holding the loop it cannot run at all.
    """
    ticks = 0
    while not stop.is_set():
        await asyncio.sleep(TICK_SECONDS)
        ticks += 1
    return ticks


def _slow_blocking(*_args, **_kwargs):
    """Stand-in for bcrypt: burns wall-clock synchronously, exactly as bcrypt does."""
    time.sleep(FAKE_BCRYPT_SECONDS)
    return False  # a failed password check; keeps the caller off the DTO path


@pytest.fixture
def user_repo():
    # BaseRepository resolves a live Motor database in its constructor, so the
    # database getter is what has to be stubbed -- not `MongoClient`, which this
    # module does not import (several older tests in tests/auth patch that name
    # and error out at collection time).
    with patch(
        "services.auth.app.core.database.get_auth_database", return_value=MagicMock()
    ):
        repo = UserRepository()

    repo.find_one = AsyncMock(
        return_value={
            "_id": "507f1f77bcf86cd799439011",
            "username": "someone",
            "password_hash": b"$2b$12$notarealhashbutthatisfine",
            "is_active": True,
            "is_deleted": False,
            "locked_until": None,
            "login_attempts": 0,
        }
    )
    repo._increment_login_attempts = AsyncMock()
    repo._reset_login_attempts = AsyncMock()
    repo.update_last_login = AsyncMock()
    return repo


async def _ticks_during(coro_factory) -> int:
    """Run coro_factory() and report how many times a concurrent task was scheduled."""
    stop = asyncio.Event()
    probe = asyncio.create_task(_count_ticks_until(stop))
    await asyncio.sleep(0)  # let the probe reach its first await before we start

    await coro_factory()

    stop.set()
    return await probe


@pytest.mark.asyncio
async def test_authenticate_user_does_not_block_the_event_loop(user_repo):
    """The login path is the hot one: it runs on every sign-in."""
    with patch(
        "services.auth.app.repository.user_repository.bcrypt.checkpw",
        side_effect=_slow_blocking,
    ):
        ticks = await _ticks_during(
            lambda: user_repo.authenticate_user("someone", "whatever")
        )

    assert ticks >= MIN_EXPECTED_TICKS, (
        f"event loop was blocked during password verification: only {ticks} ticks "
        f"in {FAKE_BCRYPT_SECONDS}s. bcrypt.checkpw is running inline instead of "
        f"being offloaded to a thread."
    )


@pytest.mark.asyncio
async def test_create_user_does_not_block_the_event_loop(user_repo):
    """Hashing blocks exactly as verification does; an admin bulk-creating users
    would stall the service the same way a login rush does."""
    user_repo.get_user_by_username = AsyncMock(return_value=None)
    user_repo.get_user_by_email = AsyncMock(return_value=None)
    user_repo.create = AsyncMock(return_value="507f1f77bcf86cd799439011")
    user_repo.find_by_id = AsyncMock(return_value=None)

    with patch(
        "services.auth.app.repository.user_repository.bcrypt.hashpw",
        side_effect=_slow_blocking,
    ), patch(
        "services.auth.app.repository.user_repository.bcrypt.gensalt",
        return_value=b"salt",
    ):
        async def call():
            # Whatever this raises afterwards is irrelevant -- the assertion is
            # about the loop staying responsive while hashpw runs.
            try:
                await user_repo.create_user("someone", "whatever")
            except Exception:
                pass

        ticks = await _ticks_during(call)

    assert ticks >= MIN_EXPECTED_TICKS, (
        f"event loop was blocked during password hashing: only {ticks} ticks in "
        f"{FAKE_BCRYPT_SECONDS}s. bcrypt.hashpw is running inline."
    )


@pytest.mark.asyncio
async def test_authenticate_user_still_returns_none_on_wrong_password(user_repo):
    """Offloading must not change the outcome -- a wrong password still fails,
    and still counts against the lockout threshold."""
    with patch(
        "services.auth.app.repository.user_repository.bcrypt.checkpw",
        return_value=False,
    ):
        result = await user_repo.authenticate_user("someone", "wrong")

    assert result is None
    user_repo._increment_login_attempts.assert_awaited_once()
