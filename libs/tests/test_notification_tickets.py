import json

import pytest

from services.processing.app.v2.api.router.notification_routes import (
    TICKET_TTL_SECONDS,
    consume_ticket,
    mint_ticket,
    ticket_key,
)


class _FakeRedis:
    """GETDEL semantics: read and delete atomically."""

    def __init__(self):
        self.store = {}
        self.setex_calls = []

    async def setex(self, key, ttl, value):
        self.setex_calls.append((key, ttl))
        self.store[key] = value

    async def getdel(self, key):
        return self.store.pop(key, None)


@pytest.mark.asyncio
async def test_ticket_is_random_and_stored_with_a_30_second_ttl():
    redis = _FakeRedis()
    ticket = await mint_ticket(redis, "u1", "jwt-abc", 1234567890)

    assert len(ticket) >= 32
    assert redis.setex_calls == [(ticket_key(ticket), TICKET_TTL_SECONDS)]
    assert TICKET_TTL_SECONDS == 30


@pytest.mark.asyncio
async def test_stored_payload_retains_the_token_for_revocation_checks():
    redis = _FakeRedis()
    ticket = await mint_ticket(redis, "u1", "jwt-abc", 999)

    payload = json.loads(redis.store[ticket_key(ticket)])
    assert payload == {"user_id": "u1", "token": "jwt-abc", "exp": 999}


@pytest.mark.asyncio
async def test_two_mints_never_collide():
    redis = _FakeRedis()
    tickets = {await mint_ticket(redis, "u1", "t", 1) for _ in range(50)}
    assert len(tickets) == 50


@pytest.mark.asyncio
async def test_consume_returns_the_payload_once_then_never_again():
    redis = _FakeRedis()
    ticket = await mint_ticket(redis, "u1", "jwt-abc", 999)

    assert (await consume_ticket(redis, ticket))["user_id"] == "u1"
    assert await consume_ticket(redis, ticket) is None


@pytest.mark.asyncio
async def test_unknown_ticket_returns_none():
    assert await consume_ticket(_FakeRedis(), "never-issued") is None


@pytest.mark.asyncio
async def test_corrupt_payload_returns_none_rather_than_raising():
    redis = _FakeRedis()
    redis.store[ticket_key("t1")] = "not json"
    assert await consume_ticket(redis, "t1") is None
