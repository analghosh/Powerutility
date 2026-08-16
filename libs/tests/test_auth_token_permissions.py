"""
The claims an EPMS token carries, and the two ways they used to come out empty.

Both bugs were invisible for as long as nothing checked permissions. Turning on the
processing route guards makes each of them an outage:

* `refresh_token` built the new access token from the raw user document. Permissions live
  on the role, not the user, so every refreshed token carried an empty permission list --
  the first refresh would have 403'd a user out of a session they were in the middle of.
* `get_user_with_permissions` granted the `*` wildcard to `admin` only. `superadmin`
  resolved to its menu grants, which are read-only, so the highest-privilege role in the
  system would have been denied every write.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from libs.database.mongo_client_factory import MongoClientFactory

# Importing the auth service package opens a synchronous Mongo connection at import time.
with patch.object(MongoClientFactory, "get_client", return_value=MagicMock()):
    from services.auth.app.services.auth_service import AuthService
    from services.auth.app.repository import user_repository as user_repository_module


class _User:
    def __init__(self, user_id="u1", username="trader1", roles=None, permissions=None):
        self.id = user_id
        self.username = username
        self.roles = roles if roles is not None else ["operator"]
        self.permissions = permissions if permissions is not None else []
        self.is_active = True

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "roles": self.roles,
            "permissions": self.permissions,
        }


class _Repo:
    """The user document has no permissions; the role-resolved view does."""

    def __init__(self, resolved=None):
        self.plain = _User()
        self.resolved = resolved
        self.resolved_calls = []

    async def get_user_by_id(self, user_id):
        return self.plain

    async def get_user_with_permissions_by_id(self, user_id):
        self.resolved_calls.append(user_id)
        return self.resolved


def _service(repo):
    service = AuthService.__new__(AuthService)
    service.user_repo = repo
    service.jwt_service = SimpleNamespace(
        verify_refresh_token=lambda token: {"sub": "u1"},
        create_access_token=lambda payload: f"token::{sorted(payload.get('permissions') or [])}",
    )
    service.audit_logger = MagicMock()
    return service


# --- refresh -------------------------------------------------------------


@pytest.mark.asyncio
async def test_refreshed_token_carries_the_roles_permissions():
    resolved = _User(permissions=["dam_block_bid:write", "dashboard:read"])
    repo = _Repo(resolved=resolved)

    result = await _service(repo).refresh_token("refresh", {})

    assert repo.resolved_calls == ["u1"], "refresh did not resolve permissions from the role"
    assert "dam_block_bid:write" in result["access_token"]


@pytest.mark.asyncio
async def test_refresh_falls_back_to_the_plain_user_when_resolution_fails():
    # Losing permissions is bad; losing the session entirely is worse.
    repo = _Repo(resolved=None)

    result = await _service(repo).refresh_token("refresh", {})

    assert result is not None
    assert "access_token" in result


@pytest.mark.asyncio
async def test_an_inactive_user_cannot_refresh():
    repo = _Repo(resolved=_User())
    repo.plain.is_active = False

    assert await _service(repo).refresh_token("refresh", {}) is None


# --- the superadmin wildcard ---------------------------------------------


def test_super_roles_covers_both_admin_names():
    from libs.authz.route_guard import SUPER_ROLES

    assert SUPER_ROLES == {"admin", "superadmin"}


def test_auth_repository_grants_the_wildcard_to_both_super_roles():
    """
    Guards the exact line that locked superadmins out: `if "admin" in user.roles`.

    Read from source rather than executed, because get_user_with_permissions needs a live
    database. The assertion is that the check is role-set based and mentions neither role
    by hand.
    """
    import inspect

    source = inspect.getsource(user_repository_module.UserRepository.get_user_with_permissions)
    assert 'if "admin" in user.roles' not in source, "the superadmin lockout has come back"
    assert "SUPER_ROLES" in source
