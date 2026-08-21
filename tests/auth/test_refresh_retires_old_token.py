"""
A refresh must leave exactly one usable access token, not two.

Before this, `refresh_token()` minted a new access token and did nothing with the one it
replaced, so the old token stayed valid for the remainder of its thirty minutes. Two
access tokens for one session, and the superseded one could not be revoked because nobody
knew it existed.

Two properties carry the weight here:

* the new token is issued BEFORE the old one is blacklisted — reversing that would, on a
  minting failure, blacklist the caller's only working credential and log them out on a
  server error;
* the old token is blacklisted for its own remaining lifetime, not the service-wide seven
  days. This path runs ~48 times per user per day and the Redis key *is* the token, so
  seven-day retention would pile up hundreds of kilobyte-sized keys per active user in the
  same Redis the rate limiter and notifications use.
"""

import time

import jwt
import pytest

from services.auth.app.core.config import settings
from services.auth.app.services.auth_service import AuthService


def make_token(exp_offset_seconds):
    return jwt.encode(
        {"sub": "u1", "type": "access", "exp": int(time.time()) + exp_offset_seconds},
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


class _Recorder:
    def __init__(self):
        self.calls = []

    def invalidate_access_token(self, token, user_id, ttl_seconds=None):
        self.calls.append({"token": token, "user_id": user_id, "ttl": ttl_seconds})
        return True


@pytest.fixture
def recorder(monkeypatch):
    r = _Recorder()
    monkeypatch.setattr(
        "services.auth.app.services.auth_service.redis_blacklist_service", r
    )
    return r


class TestRetiringTheOldToken:
    def test_a_live_token_is_blacklisted(self, recorder):
        token = make_token(900)
        AuthService._retire_superseded_access_token(token, "u1")
        assert len(recorder.calls) == 1
        assert recorder.calls[0]["token"] == token
        assert recorder.calls[0]["user_id"] == "u1"

    def test_it_is_kept_only_for_its_remaining_life(self, recorder):
        """Not the seven-day service default — see the module docstring."""
        AuthService._retire_superseded_access_token(make_token(900), "u1")
        ttl = recorder.calls[0]["ttl"]
        assert ttl is not None, "must pass an explicit TTL"
        assert 0 < ttl <= 900
        assert ttl < settings.REDIS_BLACKLIST_TTL * 24 * 3600

    def test_an_already_expired_token_is_not_stored(self, recorder):
        """It cannot authenticate anything, so remembering it is pure waste."""
        AuthService._retire_superseded_access_token(make_token(-60), "u1")
        assert recorder.calls == []

    def test_a_missing_token_is_not_an_error(self, recorder):
        """Clients predating this field still refresh normally — during a rollout the old
        bundle is live in browsers, and refusing those would log every one of them out."""
        AuthService._retire_superseded_access_token(None, "u1")
        AuthService._retire_superseded_access_token("", "u1")
        assert recorder.calls == []

    def test_a_malformed_token_does_not_raise(self, recorder):
        """The caller already holds their new token by this point; failing here would turn
        a successful refresh into an error."""
        AuthService._retire_superseded_access_token("not.a.jwt", "u1")
        assert recorder.calls == []

    def test_a_foreign_signature_does_not_raise(self, recorder):
        forged = jwt.encode({"sub": "u1", "exp": int(time.time()) + 900},
                            "some-other-secret", algorithm="HS256")
        AuthService._retire_superseded_access_token(forged, "u1")
        assert recorder.calls == []


class TestOrdering:
    def test_the_new_token_is_minted_before_the_old_one_is_retired(self):
        """Reversed, a minting failure would blacklist the caller's only credential."""
        src = open("services/auth/app/services/auth_service.py").read()
        block = src[src.index("async def refresh_token"):src.index("async def logout")]
        mint = block.index("access_token = self.jwt_service.create_access_token")
        retire = block.index("_retire_superseded_access_token(superseded_access_token")
        assert mint < retire


class TestRequestContract:
    def test_the_field_is_optional(self):
        from services.auth.app.schemas.auth import RefreshTokenRequest
        assert RefreshTokenRequest(refresh_token="r").access_token is None

    def test_the_field_is_accepted_when_sent(self):
        from services.auth.app.schemas.auth import RefreshTokenRequest
        assert RefreshTokenRequest(refresh_token="r", access_token="a").access_token == "a"
