"""
Two user-management routes that returned success without doing the work.

DELETE /users/{id} called an `async def` repository method without awaiting it. The
coroutine is truthy, so the failure check passed, the audit log recorded
`success=True`, and the caller got HTTP 200 -- while no write ever reached Mongo.
Offboarding was a no-op nobody could see.

PUT /users/{id} accepted `username`. Renaming the seeded super-admin frees the name,
and the startup seeder mints a fresh super-admin under it on the next restart, so a
rename is a persistence backdoor rather than a cosmetic edit.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from libs.database.mongo_client_factory import MongoClientFactory

# Importing the auth routes package instantiates AuthService at module level, which
# opens a synchronous Mongo connection during import. Stub the factory so this suite
# runs without a database.
with patch.object(MongoClientFactory, "get_client", return_value=MagicMock()):
    from services.auth.app.api.v1.routes import user_management
    from services.auth.app.repository import user_repository as user_repository_module

delete_user = user_management.delete_user
update_user = user_management.update_user
UserUpdateRequest = user_management.UserUpdateRequest
UserCreateRequest = user_management.UserCreateRequest


def _stored_user(user_id="u1", username="trader1"):
    return SimpleNamespace(
        id=user_id,
        username=username,
        email="trader1@example.com",
        is_active=True,
        is_verified=False,
        roles=["operator"],
        permissions=["dashboard:read"],
        profile={"first_name": "Trader"},
        created_at=None,
        last_login=None,
    )


class _FakeUserRepo:
    def __init__(self, user=None, delete_result=True, update_result=True):
        self._user = user if user is not None else _stored_user()
        self._delete_result = delete_result
        self._update_result = update_result
        self.deleted_ids = []
        self.update_payloads = []

    async def get_user_by_id(self, user_id):
        return self._user

    async def delete_user(self, user_id):
        self.deleted_ids.append(user_id)
        return self._delete_result

    async def update_user(self, user_id, update_data, modified_by=None):
        self.update_payloads.append(dict(update_data))
        return self._update_result


def _install(monkeypatch, repo):
    # The route imports UserRepository inside the handler, so patching the module
    # attribute is enough -- no import-order games needed.
    monkeypatch.setattr(user_repository_module, "UserRepository", lambda: repo)


def _caller():
    return SimpleNamespace(user_id="admin-1")


def _request():
    return SimpleNamespace(client=None)


# --- DELETE /users/{id} ----------------------------------------------------


@pytest.mark.asyncio
async def test_delete_actually_reaches_the_repository(monkeypatch):
    repo = _FakeUserRepo()
    _install(monkeypatch, repo)

    result = await delete_user(
        "u1", request=_request(), current_user=_caller(), audit_logger=MagicMock()
    )

    # An un-awaited coroutine never runs its body, so this list stays empty.
    assert repo.deleted_ids == ["u1"]
    assert result["user_id"] == "u1"


@pytest.mark.asyncio
async def test_delete_surfaces_a_failed_write_instead_of_returning_200(monkeypatch):
    repo = _FakeUserRepo(delete_result=False)
    _install(monkeypatch, repo)

    with pytest.raises(HTTPException) as excinfo:
        await delete_user(
            "u1", request=_request(), current_user=_caller(), audit_logger=MagicMock()
        )

    assert excinfo.value.status_code == 500


@pytest.mark.asyncio
async def test_delete_still_404s_for_an_unknown_user(monkeypatch):
    repo = _FakeUserRepo()
    repo._user = None
    _install(monkeypatch, repo)

    with pytest.raises(HTTPException) as excinfo:
        await delete_user(
            "nope", request=_request(), current_user=_caller(), audit_logger=MagicMock()
        )

    assert excinfo.value.status_code == 404
    assert repo.deleted_ids == []


# --- PUT /users/{id} -------------------------------------------------------


def test_username_is_not_an_updatable_field():
    assert "username" not in UserUpdateRequest.model_fields


def test_a_username_in_the_body_is_ignored_rather_than_rejected():
    # Clients that still post the current username must not start getting 422s.
    payload = UserUpdateRequest(username="superadmin", email="t@example.com")

    assert payload.model_dump() == {
        "email": "t@example.com",
        "roles": None,
        "permissions": None,
        "profile": None,
        "is_active": None,
        "is_verified": None,
    }


@pytest.mark.asyncio
async def test_update_never_writes_a_username(monkeypatch):
    repo = _FakeUserRepo()
    _install(monkeypatch, repo)

    await update_user(
        "u1",
        UserUpdateRequest(username="attacker", profile={"first_name": "New"}),
        current_user=_caller(),
        audit_logger=MagicMock(),
    )

    assert repo.update_payloads, "update_user was never called"
    for payload in repo.update_payloads:
        assert "username" not in payload
    assert repo.update_payloads[-1]["profile"] == {"first_name": "New"}


class TestProfileIsTyped:
    """
    VAPT F15. `profile` was `Dict[str, Any]` and the update path wrote it into Mongo
    verbatim, so any key a caller invented was stored on the user document. The typed
    model drops what it does not declare; the two names the application uses survive.
    """

    def test_unknown_keys_are_dropped_from_an_update(self):
        request = UserUpdateRequest(profile={
            "first_name": "Asha",
            "last_name": "Rao",
            "is_super_admin": True,
            "injected": {"anything": "at all"},
        })
        assert request.profile.model_dump(exclude_none=True) == {
            "first_name": "Asha",
            "last_name": "Rao",
        }

    def test_unknown_keys_are_dropped_from_a_create(self):
        request = UserCreateRequest(
            username="trader9",
            password="Str0ng!Password",
            profile={"first_name": "Asha", "roles": ["admin"]},
        )
        assert not hasattr(request.profile, "roles")
        assert request.profile.first_name == "Asha"

    def test_a_partial_update_does_not_blank_the_other_name(self):
        """exclude_none, so sending only first_name leaves last_name alone in Mongo."""
        request = UserUpdateRequest(profile={"first_name": "Asha"})
        assert request.profile.model_dump(exclude_none=True) == {"first_name": "Asha"}

    def test_the_profile_stays_optional(self):
        assert UserUpdateRequest().profile is None
