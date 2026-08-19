"""
Tests for in-process access-token validation.

The point of `LocalTokenValidator` is to stop calling the auth service on every request
without giving up anything it checked. So these tests are written against that contract:
each of the four checks `auth_service.get_current_user` performs has a test that proves a
token is still refused, and the two failure modes (Redis down, Mongo down) have tests that
pin which way each one fails. A regression here does not look like a crash — it looks like
a deactivated account continuing to work.
"""

import time

import jwt
import pytest

from libs.core.local_auth import LocalTokenValidator

SECRET = "unit-test-secret-key-long-enough-to-be-realistic"
UID = "6a6f5e396b69d230693c215d"


class _Settings:
    JWT_SECRET_KEY = SECRET
    JWT_ALGORITHM = "HS256"


class _FakeUsers:
    """Motor-ish collection: one document, or an exception to raise."""

    def __init__(self, doc=None, raises=None, key=UID):
        self._doc = doc
        self._raises = raises
        self._key = key
        self.lookups = []

    async def find_one(self, query, projection=None):
        if self._raises:
            raise self._raises
        wanted = query["_id"]
        self.lookups.append(wanted)
        return self._doc if str(wanted) == str(self._key) else None


class _FakeRedis:
    def __init__(self, revoked=(), raises=None):
        self._revoked = set(revoked)
        self._raises = raises

    async def exists(self, key):
        if self._raises:
            raise self._raises
        return 1 if key in self._revoked else 0


def make_token(secret=SECRET, **overrides):
    payload = {
        "sub": UID,
        "username": "trader",
        "email": "trader@example.com",
        "roles": ["user"],
        "permissions": ["dam_single_bid:read"],
        "token_version": 0,
        "type": "access",
        "exp": int(time.time()) + 900,
    }
    payload.update(overrides)
    return jwt.encode(payload, secret, algorithm="HS256")


def build(doc=None, revoked=(), redis_raises=None, mongo_raises=None, settings=_Settings):
    v = LocalTokenValidator(settings=settings)
    if doc is None:
        doc = {"is_active": True, "is_deleted": False, "token_version": 0}
    v._users = _FakeUsers(doc, raises=mongo_raises)
    v._redis = _FakeRedis(revoked, raises=redis_raises)
    return v


class TestAcceptance:
    @pytest.mark.asyncio
    async def test_a_good_token_is_accepted(self):
        payload, reason = await build().validate(make_token())
        assert reason is None
        assert payload["sub"] == UID
        assert payload["permissions"] == ["dam_single_bid:read"]

    @pytest.mark.asyncio
    async def test_a_token_ahead_of_the_account_is_accepted(self):
        """Only a token BEHIND the account's version is stale."""
        doc = {"is_active": True, "is_deleted": False, "token_version": 2}
        payload, reason = await build(doc).validate(make_token(token_version=2))
        assert reason is None


class TestSignatureAndShape:
    @pytest.mark.asyncio
    async def test_expired_token_rejected(self):
        _, reason = await build().validate(make_token(exp=int(time.time()) - 5))
        assert reason == "token expired"

    @pytest.mark.asyncio
    async def test_wrong_signature_rejected(self):
        _, reason = await build().validate(make_token(secret="a-different-secret-entirely"))
        assert reason and "invalid token" in reason

    @pytest.mark.asyncio
    async def test_refresh_token_cannot_authenticate_an_api_call(self):
        _, reason = await build().validate(make_token(type="refresh"))
        assert reason and "wrong token type" in reason

    @pytest.mark.asyncio
    async def test_temporary_mfa_token_cannot_authenticate_an_api_call(self):
        _, reason = await build().validate(make_token(type="temporary"))
        assert reason and "wrong token type" in reason

    @pytest.mark.asyncio
    async def test_token_without_a_subject_rejected(self):
        tok = jwt.encode({"type": "access", "exp": int(time.time()) + 900}, SECRET, algorithm="HS256")
        _, reason = await build().validate(tok)
        assert reason == "token carries no subject"

    @pytest.mark.asyncio
    async def test_missing_secret_refuses_rather_than_accepts(self, monkeypatch):
        """No secret anywhere must fail closed, not fall through to accepting."""
        class _NoSecret:
            JWT_SECRET_KEY = ""
            JWT_ALGORITHM = "HS256"

        # tests/conftest.py injects a JWT_SECRET_KEY into the environment, and the
        # environment is the documented second source after settings.
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        v = build(settings=_NoSecret())
        _, reason = await v.validate(make_token())
        assert reason == "server misconfigured"


class TestRevocation:
    """One test per way a session is meant to end early."""

    @pytest.mark.asyncio
    async def test_logout_blacklist_rejects(self):
        tok = make_token()
        v = build(revoked={f"blacklist:access:{tok}"})
        _, reason = await v.validate(tok)
        assert reason == "token revoked by logout"

    @pytest.mark.asyncio
    async def test_deactivated_account_rejects(self):
        doc = {"is_active": False, "is_deleted": False, "token_version": 0}
        _, reason = await build(doc).validate(make_token())
        assert reason == "account inactive"

    @pytest.mark.asyncio
    async def test_deleted_account_rejects(self):
        doc = {"is_active": True, "is_deleted": True, "token_version": 0}
        _, reason = await build(doc).validate(make_token())
        assert reason == "account deleted"

    @pytest.mark.asyncio
    async def test_missing_account_rejects(self):
        v = LocalTokenValidator(settings=_Settings)
        v._users = _FakeUsers(None)
        v._redis = _FakeRedis()
        _, reason = await v.validate(make_token())
        assert reason == "account not found"

    @pytest.mark.asyncio
    async def test_token_predating_a_rights_change_rejects(self):
        """password reset / role change / deactivation all bump token_version."""
        doc = {"is_active": True, "is_deleted": False, "token_version": 3}
        _, reason = await build(doc).validate(make_token(token_version=1))
        assert reason and "superseded" in reason

    @pytest.mark.asyncio
    async def test_legacy_token_without_a_version_is_superseded_by_a_bump(self):
        doc = {"is_active": True, "is_deleted": False, "token_version": 1}
        tok = jwt.encode(
            {"sub": UID, "type": "access", "exp": int(time.time()) + 900},
            SECRET, algorithm="HS256",
        )
        _, reason = await build(doc).validate(tok)
        assert reason and "superseded" in reason


class TestFailureModes:
    @pytest.mark.asyncio
    async def test_redis_outage_fails_open(self):
        """A Redis outage must not log out the platform. Revocation is the smaller loss."""
        _, reason = await build(redis_raises=ConnectionError("redis down")).validate(make_token())
        assert reason is None

    @pytest.mark.asyncio
    async def test_mongo_outage_fails_closed(self):
        """Failing open here would let a deactivated account keep working."""
        _, reason = await build(mongo_raises=ConnectionError("mongo down")).validate(make_token())
        assert reason == "user lookup failed"


class TestUserLookup:
    @pytest.mark.asyncio
    async def test_objectid_is_tried_first_for_a_24_char_id(self):
        """`_id` is an ObjectId for every account auth creates; a string-first lookup
        meant a guaranteed miss and two round trips on every request."""
        from bson import ObjectId

        v = build()
        v._users = _FakeUsers({"is_active": True, "token_version": 0}, key=ObjectId(UID))
        _, reason = await v.validate(make_token())
        assert reason is None
        assert isinstance(v._users.lookups[0], ObjectId)
        assert len(v._users.lookups) == 1

    @pytest.mark.asyncio
    async def test_string_id_still_found(self):
        """The collection genuinely holds both shapes."""
        v = build()
        v._users = _FakeUsers({"is_active": True, "token_version": 0}, key="plain-string-id")
        _, reason = await v.validate(make_token(sub="plain-string-id"))
        assert reason is None


class TestConfigResolution:
    def test_settings_beat_environment(self, monkeypatch):
        """pydantic loads .env but never writes it to os.environ, so settings come first."""
        monkeypatch.setenv("JWT_SECRET_KEY", "from-environment")
        assert LocalTokenValidator(settings=_Settings)._secret() == SECRET

    def test_environment_used_when_settings_lack_the_field(self, monkeypatch):
        class _Bare:
            pass

        monkeypatch.setenv("JWT_SECRET_KEY", "from-environment")
        assert LocalTokenValidator(settings=_Bare())._secret() == "from-environment"

    def test_default_used_when_neither_supplies_it(self, monkeypatch):
        class _Bare:
            pass

        monkeypatch.delenv("JWT_ALGORITHM", raising=False)
        assert LocalTokenValidator(settings=_Bare())._algorithm() == "HS256"
