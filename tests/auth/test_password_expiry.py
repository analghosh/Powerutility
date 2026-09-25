"""
Password expiry: warn from day 80, block from day 90.

Every user document has carried ``password_changed_at`` and ``password_expires_at``
since the auth service was written, and ``PASSWORD_EXPIRY_DAYS = 90`` is in the
config, but nothing ever read them: a password set in December still logged in
in September. These tests pin the policy PTC asked for:

    age < 80        normal access
    80 <= age < 90  access, plus "your password will expire in N days"
    age >= 90       no session; the only thing the user can do is change it

and the two defects found on the way: ``/change-password`` accepted any new
password (the strength policy was never called) and the reuse check compared a
freshly salted bcrypt hash against stored ones, so it could never match.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import bcrypt
import pytest
from bson import ObjectId
from fastapi import HTTPException

from services.auth.app.core.dto import UserDTO, AuthUserDTO
from services.auth.app.services.auth_service import AuthService
from services.auth.app.services.jwt_service import jwt_service
from services.auth.app.services.password_policy import evaluate_password_age

NOW = datetime(2026, 9, 24, 12, 0, 0)
POLICY = dict(expiry_days=90, warning_days=10, mode="enforce", exempt_roles=["superadmin"], clock_start=None)


def age(days, roles=("user",), **overrides):
    return evaluate_password_age(
        NOW - timedelta(days=days), list(roles), now=NOW, **{**POLICY, **overrides}
    )


# --------------------------------------------------------------------------
# The policy itself
# --------------------------------------------------------------------------

@pytest.mark.parametrize("days", [0, 1, 45, 79])
def test_under_80_days_is_normal_access(days):
    result = age(days)
    assert result.status == "ok"
    assert not result.blocks_access
    assert result.to_client() is None


@pytest.mark.parametrize("days,remaining", [(80, 10), (85, 5), (89, 1)])
def test_80_to_89_days_warns_with_days_left(days, remaining):
    result = age(days)
    assert result.status == "warning"
    assert result.days_remaining == remaining
    assert not result.blocks_access
    info = result.to_client()
    assert info["status"] == "warning" and info["days_remaining"] == remaining
    assert info["expires_at"] == (NOW - timedelta(days=days) + timedelta(days=90)).isoformat()


@pytest.mark.parametrize("days", [90, 91, 273])
def test_90_days_and_over_blocks_access(days):
    result = age(days)
    assert result.status == "expired"
    assert result.days_remaining == 0
    assert result.blocks_access


def test_boundary_is_measured_in_whole_days():
    # 89 days and 23 hours is still day 89: one day left, not expired.
    result = evaluate_password_age(NOW - timedelta(days=89, hours=23), ["user"], now=NOW, **POLICY)
    assert result.status == "warning" and result.days_remaining == 1


def test_superadmin_is_exempt_per_brd():
    assert age(400, roles=["superadmin"]).status == "exempt"
    assert not age(400, roles=["superadmin"]).blocks_access


def test_exempt_roles_are_configurable():
    assert age(400, roles=["superadmin"], exempt_roles=[]).blocks_access
    assert age(400, roles=["admin"], exempt_roles=["admin"]).status == "exempt"


def test_warn_mode_reports_expiry_but_never_blocks():
    result = age(120, mode="warn")
    assert result.status == "expired"
    assert not result.blocks_access
    assert result.to_client()["status"] == "expired"


def test_off_mode_says_nothing():
    result = age(400, mode="off")
    assert result.status == "ok" and result.to_client() is None


def test_clock_start_gives_everyone_a_fresh_cycle_from_rollout():
    # Changed a year ago, but the clock only started 85 days ago -> warning, not blocked.
    result = age(365, clock_start=NOW - timedelta(days=85))
    assert result.status == "warning" and result.days_remaining == 5
    # A change made after the clock start is measured from the change itself.
    assert age(10, clock_start=NOW - timedelta(days=85)).status == "ok"


def test_missing_changed_at_falls_back_to_created_at():
    result = evaluate_password_age(None, ["user"], created_at=NOW - timedelta(days=95), now=NOW, **POLICY)
    assert result.blocks_access


def test_no_dates_at_all_is_not_blocked():
    assert evaluate_password_age(None, ["user"], now=NOW, **POLICY).status == "ok"


@pytest.mark.parametrize("configured,admin_exempt", [
    ("superadmin", False),
    ("superadmin, admin", True),
    ('["superadmin", "admin"]', True),
])
def test_exempt_roles_accept_the_env_string_forms(configured, admin_exempt):
    assert age(400, roles=["superadmin"], exempt_roles=configured).status == "exempt"
    assert (age(400, roles=["admin"], exempt_roles=configured).status == "exempt") is admin_exempt


# --------------------------------------------------------------------------
# Settings: a mistyped .env line must not stop the auth service starting
# --------------------------------------------------------------------------

def _settings_with(monkeypatch, **env):
    from services.auth.app.core.config import Settings
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings()


def test_blank_clock_start_in_env_means_unset(monkeypatch):
    assert _settings_with(monkeypatch, PASSWORD_EXPIRY_CLOCK_START="").PASSWORD_EXPIRY_CLOCK_START is None
    assert _settings_with(monkeypatch, PASSWORD_EXPIRY_CLOCK_START="  ").PASSWORD_EXPIRY_CLOCK_START is None


def test_clock_start_date_in_env_is_read(monkeypatch):
    s = _settings_with(monkeypatch, PASSWORD_EXPIRY_CLOCK_START="2026-10-01")
    assert s.PASSWORD_EXPIRY_CLOCK_START == datetime(2026, 10, 1)


def test_exempt_roles_in_env_may_be_plain_text(monkeypatch):
    # A JSON-typed list setting would refuse "superadmin" and take the service down.
    s = _settings_with(monkeypatch, PASSWORD_EXPIRY_EXEMPT_ROLES="superadmin")
    assert age(400, roles=["superadmin"], exempt_roles=s.PASSWORD_EXPIRY_EXEMPT_ROLES).status == "exempt"


def test_expiry_mode_in_env_is_case_insensitive_and_blank_means_enforce(monkeypatch):
    assert _settings_with(monkeypatch, PASSWORD_EXPIRY_MODE=" Warn ").PASSWORD_EXPIRY_MODE == "warn"
    assert _settings_with(monkeypatch, PASSWORD_EXPIRY_MODE="").PASSWORD_EXPIRY_MODE == "enforce"


# --------------------------------------------------------------------------
# Fixtures for the service-level tests
# --------------------------------------------------------------------------

def _hash(pw):
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=4))


def _user_doc(days_old, roles=("user",), password="Old@Pass123", history=()):
    changed = datetime.now() - timedelta(days=days_old)
    return {
        "_id": ObjectId(),
        "username": "ageuser",
        "email": None,
        "is_active": True,
        "is_verified": True,
        "roles": list(roles),
        "permissions": [],
        "profile": {"first_name": "Age", "last_name": "User"},
        "created_at": changed - timedelta(days=30),
        "updated_at": changed,
        "last_login": None,
        "login_attempts": 0,
        "locked_until": None,
        "password_changed_at": changed,
        "password_expires_at": changed + timedelta(days=90),
        "password_history": list(history),
        "password_hash": _hash(password),
        "mfa_enabled": False,
        "mfa_setup_complete": False,
    }


class FakeRepo:
    """The slice of UserRepository the login and change paths touch."""

    def __init__(self, doc):
        self.doc = doc
        self.updated = None

    async def authenticate_user(self, username, password):
        if bcrypt.checkpw(password.encode(), self.doc["password_hash"]):
            return AuthUserDTO.from_user_dto(UserDTO.from_db_document(self.doc))
        return None

    async def get_user_with_permissions(self, username):
        return AuthUserDTO.from_user_dto(UserDTO.from_db_document(self.doc))

    async def get_user_with_permissions_by_id(self, user_id):
        return await self.get_user_with_permissions(self.doc["username"])

    async def get_user_by_id(self, user_id):
        return UserDTO.from_db_document(self.doc) if str(self.doc["_id"]) == user_id else None

    async def find_one(self, query, *args, **kwargs):
        return self.doc

    async def update_password(self, user_id, new_password):
        self.updated = new_password
        self.doc["password_hash"] = _hash(new_password)
        self.doc["password_changed_at"] = datetime.now()
        return True


@pytest.fixture
def blacklist():
    fake = MagicMock()
    fake.is_temporary_token_blacklisted.return_value = False
    fake.is_access_token_blacklisted.return_value = False
    fake.is_refresh_token_blacklisted.return_value = False
    with patch("services.auth.app.services.auth_service.redis_blacklist_service", fake), \
         patch("services.auth.app.services.redis_blacklist.redis_blacklist_service", fake):
        yield fake


def _service(doc):
    svc = AuthService()
    svc.user_repo = FakeRepo(doc)
    svc.audit_logger = MagicMock()
    return svc


REQ = {"ip_address": "127.0.0.1", "user_agent": "pytest"}


def _policy_settings(**overrides):
    values = dict(
        PASSWORD_EXPIRY_DAYS=90,
        PASSWORD_EXPIRY_WARNING_DAYS=10,
        PASSWORD_EXPIRY_MODE="enforce",
        PASSWORD_EXPIRY_EXEMPT_ROLES=["superadmin"],
        PASSWORD_EXPIRY_CLOCK_START=None,
    )
    values.update(overrides)
    return patch.multiple("services.auth.app.services.password_policy.settings", **values)


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_fresh_password_gets_a_session_and_no_warning(blacklist):
    with _policy_settings():
        result = await _service(_user_doc(10)).login("ageuser", "Old@Pass123", REQ)
    assert result["tokens"]["access_token"]
    assert result.get("password_expiry") is None


@pytest.mark.asyncio
async def test_login_day_85_gets_a_session_and_a_five_day_warning(blacklist):
    with _policy_settings():
        result = await _service(_user_doc(85)).login("ageuser", "Old@Pass123", REQ)
    assert result["tokens"]["access_token"]
    assert result["password_expiry"]["status"] == "warning"
    assert result["password_expiry"]["days_remaining"] == 5


@pytest.mark.asyncio
async def test_login_day_95_gets_no_session_only_a_change_token(blacklist):
    with _policy_settings():
        result = await _service(_user_doc(95)).login("ageuser", "Old@Pass123", REQ)
    assert "tokens" not in result
    assert result["password_expired"] is True
    assert result["password_change_token"]
    assert result["expires_in"] > 0


@pytest.mark.asyncio
async def test_login_expired_superadmin_is_not_blocked(blacklist):
    with _policy_settings():
        result = await _service(_user_doc(400, roles=["superadmin"])).login("ageuser", "Old@Pass123", REQ)
    assert result["tokens"]["access_token"]


@pytest.mark.asyncio
async def test_login_wrong_password_on_expired_account_reveals_nothing(blacklist):
    with _policy_settings():
        result = await _service(_user_doc(95)).login("ageuser", "Wrong@Pass123", REQ)
    assert result is None


@pytest.mark.asyncio
async def test_mfa_user_is_asked_for_the_code_before_learning_about_expiry(blacklist):
    doc = _user_doc(95)
    doc.update(mfa_enabled=True, mfa_setup_complete=True)
    with _policy_settings():
        result = await _service(doc).login("ageuser", "Old@Pass123", REQ)
    assert "temporary_token" in result and "password_change_token" not in result


@pytest.mark.asyncio
async def test_mfa_user_with_expired_password_is_blocked_after_the_code(blacklist):
    doc = _user_doc(95)
    doc.update(mfa_enabled=True, mfa_setup_complete=True, mfa_secret="enc")
    temp = jwt_service.create_temporary_token({"user_id": str(doc["_id"]), "username": "ageuser"})
    verified = {"rate_limited": False, "verified": True}
    with _policy_settings(), patch(
        "services.auth.app.services.auth_service.mfa_service.verify_totp_with_rate_limit",
        return_value=verified,
    ):
        result = await _service(doc).verify_login(temp, "123456", REQ)
    assert "tokens" not in result
    assert result["password_expired"] is True and result["password_change_token"]
    # the MFA step's own token is spent, so it cannot be replayed for a session
    blacklist.invalidate_temporary_token.assert_called_once()


@pytest.mark.asyncio
async def test_mfa_user_in_warning_window_gets_session_and_warning(blacklist):
    doc = _user_doc(82)
    doc.update(mfa_enabled=True, mfa_setup_complete=True, mfa_secret="enc")
    temp = jwt_service.create_temporary_token({"user_id": str(doc["_id"]), "username": "ageuser"})
    with _policy_settings(), patch(
        "services.auth.app.services.auth_service.mfa_service.verify_totp_with_rate_limit",
        return_value={"rate_limited": False, "verified": True},
    ):
        result = await _service(doc).verify_login(temp, "123456", REQ)
    assert result["tokens"]["access_token"]
    assert result["password_expiry"]["days_remaining"] == 8


# --------------------------------------------------------------------------
# The change token cannot be used as a session anywhere
# --------------------------------------------------------------------------

def test_change_token_is_not_an_access_token_anywhere(blacklist):
    token = jwt_service.create_password_change_token("64b000000000000000000001", "ageuser")
    payload = jwt_service.verify_token(token)
    # No sub / user_id: the processing middleware keys on those, and the shared
    # validator in libs requires sub, so neither accepts this token as a session.
    assert "sub" not in payload and "user_id" not in payload
    assert jwt_service.verify_access_token(token) is None
    assert jwt_service.verify_temporary_token(token) is None
    assert jwt_service.verify_password_change_token(token)["password_change_user_id"] == "64b000000000000000000001"

    from libs.core.jwt_validator import JWTValidator
    validator = JWTValidator.__new__(JWTValidator)
    validator.secret_key = jwt_service.secret_key
    validator.algorithm = jwt_service.algorithm
    validator.is_token_blacklisted = lambda t: False
    payload, err = validator.validate_token_with_details(token)
    assert payload is None and err


def test_access_token_is_not_a_change_token(blacklist):
    access = jwt_service.create_access_token({"sub": "x", "username": "u"})
    assert jwt_service.verify_password_change_token(access) is None


# --------------------------------------------------------------------------
# Changing an expired password
# --------------------------------------------------------------------------

async def _expired_change(doc, current, new, token=None):
    svc = _service(doc)
    token = token or jwt_service.create_password_change_token(str(doc["_id"]), doc["username"])
    with _policy_settings():
        return svc, await svc.change_expired_password(token, current, new, REQ)


@pytest.mark.asyncio
async def test_expired_change_issues_a_session_and_resets_the_cycle(blacklist):
    doc = _user_doc(95)
    svc, result = await _expired_change(doc, "Old@Pass123", "Fresh@Pass456")
    assert result["tokens"]["access_token"]
    assert result.get("password_expiry") is None
    assert svc.user_repo.updated == "Fresh@Pass456"
    blacklist.invalidate_temporary_token.assert_called_once()


@pytest.mark.asyncio
async def test_expired_change_token_is_single_use(blacklist):
    blacklist.is_temporary_token_blacklisted.return_value = True
    with pytest.raises(HTTPException) as exc:
        await _expired_change(_user_doc(95), "Old@Pass123", "Fresh@Pass456")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_expired_change_rejects_a_session_token(blacklist):
    doc = _user_doc(95)
    access = jwt_service.create_access_token({"sub": str(doc["_id"]), "username": "ageuser"})
    with pytest.raises(HTTPException) as exc:
        await _expired_change(doc, "Old@Pass123", "Fresh@Pass456", token=access)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_expired_change_needs_the_current_password(blacklist):
    with pytest.raises(HTTPException) as exc:
        await _expired_change(_user_doc(95), "Wrong@Pass123", "Fresh@Pass456")
    assert exc.value.status_code == 400
    assert "current password" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_expired_change_enforces_the_strength_policy(blacklist):
    with pytest.raises(HTTPException) as exc:
        await _expired_change(_user_doc(95), "Old@Pass123", "weakpass")
    assert exc.value.status_code == 400
    assert "uppercase" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_expired_change_refuses_the_same_password(blacklist):
    with pytest.raises(HTTPException) as exc:
        await _expired_change(_user_doc(95), "Old@Pass123", "Old@Pass123")
    assert exc.value.status_code == 400
    assert "different" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_expired_change_refuses_a_recent_password_in_either_stored_format(blacklist):
    # The vendor wrote history two ways: raw hashes (update_password) and
    # {"password_hash", "changed_at"} dicts (admin_reset_password).
    history = [_hash("Older@Pass111"), {"password_hash": _hash("Oldest@Pass000"), "changed_at": NOW}]
    for reused in ("Older@Pass111", "Oldest@Pass000"):
        with pytest.raises(HTTPException) as exc:
            await _expired_change(_user_doc(95, history=history), "Old@Pass123", reused)
        assert exc.value.status_code == 400
        assert "last 5" in exc.value.detail


@pytest.mark.asyncio
async def test_history_only_counts_the_last_five_passwords(blacklist):
    # current + 4 previous = 5. The 5th-previous password may be used again.
    history = [_hash(f"Hist@Pass{i}00") for i in range(5)]  # oldest first
    _, result = await _expired_change(_user_doc(95, history=history), "Old@Pass123", "Hist@Pass000")
    assert result["tokens"]["access_token"]
    with pytest.raises(HTTPException):
        await _expired_change(_user_doc(95, history=history), "Old@Pass123", "Hist@Pass100")


# --------------------------------------------------------------------------
# Voluntary change (profile page, or "change now" from the warning)
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_voluntary_change_now_enforces_strength(blacklist):
    doc = _user_doc(85)
    svc = _service(doc)
    with pytest.raises(HTTPException) as exc:
        await svc.change_password(str(doc["_id"]), "Old@Pass123", "short", REQ)
    assert exc.value.status_code == 400
    assert svc.user_repo.updated is None


@pytest.mark.asyncio
async def test_voluntary_change_succeeds_with_a_good_password(blacklist):
    doc = _user_doc(85)
    svc = _service(doc)
    assert await svc.change_password(str(doc["_id"]), "Old@Pass123", "Fresh@Pass456", REQ) is True
    assert svc.user_repo.updated == "Fresh@Pass456"


# --------------------------------------------------------------------------
# A session that outlives the password
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refresh_stops_once_the_password_has_expired(blacklist):
    doc = _user_doc(95)
    refresh = jwt_service.create_refresh_token({"sub": str(doc["_id"]), "username": "ageuser"})
    with _policy_settings():
        assert await _service(doc).refresh_token(refresh, REQ) is None


@pytest.mark.asyncio
async def test_refresh_still_works_during_the_warning_window(blacklist):
    doc = _user_doc(85)
    refresh = jwt_service.create_refresh_token({"sub": str(doc["_id"]), "username": "ageuser"})
    with _policy_settings():
        assert (await _service(doc).refresh_token(refresh, REQ))["access_token"]


# --------------------------------------------------------------------------
# Repository: the stored history must be the PREVIOUS password
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_password_moves_the_old_hash_into_history():
    from services.auth.app.repository.user_repository import UserRepository

    doc = _user_doc(95, history=[_hash(f"Hist@Pass{i}00") for i in range(6)])
    old_hash = doc["password_hash"]
    repo = UserRepository.__new__(UserRepository)
    repo.find_one = AsyncMock(return_value=doc)
    repo.update = AsyncMock(return_value=True)

    with patch("services.auth.app.repository.user_repository.settings",
               SimpleNamespace(BCRYPT_ROUNDS=4, PASSWORD_HISTORY_COUNT=5, PASSWORD_EXPIRY_DAYS=90)):
        assert await repo.update_password(str(doc["_id"]), "Fresh@Pass456")

    written = repo.update.await_args.args[1]
    assert bcrypt.checkpw(b"Fresh@Pass456", written["password_hash"])
    assert written["password_history"][-1]["password_hash"] == old_hash
    assert len(written["password_history"]) == 5
    assert (datetime.now() - written["password_changed_at"]).total_seconds() < 5
