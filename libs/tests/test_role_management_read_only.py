"""
The Role Management API must carry is_read_only in both directions, and flipping it must
re-sync that role's permissions immediately -- otherwise the role keeps its old strings
until somebody happens to add or remove one of its menus.

update_role's re-sync must be unconditional and its result checked, not gated on a
before/after comparison of is_read_only and not discarded. _sync_role_permissions
swallows its own exceptions and reports False rather than raising, so a gated or
ignored call lets a role persist is_read_only=True while still holding its old
read/write/delete permission strings, and a 200 response hides it from the caller.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.auth.app.api.v1.routes import role_management
from services.auth.app.repository import role_repository as role_repository_module
from services.auth.app.repository import menu_role_repository as menu_role_repository_module

RoleCreateRequest = role_management.RoleCreateRequest
RoleResponse = role_management.RoleResponse
RoleUpdateRequest = role_management.RoleUpdateRequest
update_role = role_management.update_role


def test_create_request_defaults_to_not_read_only():
    assert RoleCreateRequest(name="planner", description="").is_read_only is False


def test_create_request_accepts_the_flag():
    req = RoleCreateRequest(name="observer", description="", is_read_only=True)
    assert req.is_read_only is True


def test_update_request_omitting_the_flag_means_leave_it_alone():
    assert RoleUpdateRequest(name="observer").is_read_only is None


def test_update_request_can_set_and_clear_the_flag():
    assert RoleUpdateRequest(is_read_only=True).is_read_only is True
    assert RoleUpdateRequest(is_read_only=False).is_read_only is False


def test_response_carries_the_flag():
    resp = RoleResponse(
        id="1", name="viewer", description="", permissions=[],
        is_read_only=True, is_active=True,
    )
    assert resp.is_read_only is True


def test_response_defaults_the_flag_for_older_role_documents():
    resp = RoleResponse(
        id="1", name="operator", description="", permissions=[], is_active=True,
    )
    assert resp.is_read_only is False


# --- PUT /roles/{id}: unconditional re-sync, result checked ----------------


def _stored_role(role_id="r1", name="viewer", is_read_only=False):
    return SimpleNamespace(
        id=role_id,
        name=name,
        description="",
        permissions=["dashboard:read"],
        is_read_only=is_read_only,
        is_active=True,
        created_at=None,
        updated_at=None,
    )


class _FakeRoleRepo:
    """Mutates the stored role on a successful write, like the real Mongo update
    would, so a test can tell a stale before/after comparison from a genuine one."""

    def __init__(self, role, update_result=True):
        self._role = role
        self._update_result = update_result
        self.update_payloads = []

    async def get_role_by_id(self, role_id):
        return self._role

    async def get_role_by_name(self, name):
        return None

    async def update_role(self, role_id, update_data):
        self.update_payloads.append(dict(update_data))
        if self._update_result:
            for key, value in update_data.items():
                setattr(self._role, key, value)
        return self._update_result


class _FakeMenuRoleRepo:
    def __init__(self, sync_result=True):
        self._sync_result = sync_result
        self.synced_role_ids = []

    async def _sync_role_permissions(self, role_id):
        self.synced_role_ids.append(role_id)
        return self._sync_result


def _install(monkeypatch, role_repo, menu_repo):
    # Both repositories are imported inside the route handler, so patching the module
    # attribute is enough -- no import-order games needed.
    monkeypatch.setattr(role_repository_module, "RoleRepository", lambda: role_repo)
    monkeypatch.setattr(menu_role_repository_module, "MenuRoleRepository", lambda: menu_repo)


def _caller():
    return SimpleNamespace(user_id="admin-1")


@pytest.mark.asyncio
async def test_update_resyncs_even_when_the_flag_is_not_touched(monkeypatch):
    # A rename-only update must still re-sync -- the sync is unconditional, not gated
    # on is_read_only having changed.
    role_repo = _FakeRoleRepo(_stored_role(is_read_only=True))
    menu_repo = _FakeMenuRoleRepo(sync_result=True)
    _install(monkeypatch, role_repo, menu_repo)

    await update_role("r1", RoleUpdateRequest(name="renamed-only"), current_user=_caller())

    assert menu_repo.synced_role_ids == ["r1"]


@pytest.mark.asyncio
async def test_update_surfaces_a_failed_resync_instead_of_returning_200(monkeypatch):
    # _sync_role_permissions swallows its own exceptions and returns False -- it never
    # raises -- so the route has to check the return value itself.
    role_repo = _FakeRoleRepo(_stored_role(is_read_only=False))
    menu_repo = _FakeMenuRoleRepo(sync_result=False)
    _install(monkeypatch, role_repo, menu_repo)

    with pytest.raises(HTTPException) as excinfo:
        await update_role("r1", RoleUpdateRequest(is_read_only=True), current_user=_caller())

    assert excinfo.value.status_code == 500
    # The sync was attempted, not skipped -- the write itself still went through.
    assert menu_repo.synced_role_ids == ["r1"]
    assert role_repo._role.is_read_only is True


@pytest.mark.asyncio
async def test_a_failed_resync_can_be_healed_by_saving_again(monkeypatch):
    # Comparing the request against the already-updated stored value (the old
    # flag_changed logic) makes a retry permanently evaluate "unchanged" and skip the
    # sync forever. An unconditional re-sync has no such trap.
    role_repo = _FakeRoleRepo(_stored_role(is_read_only=False))
    menu_repo = _FakeMenuRoleRepo(sync_result=False)
    _install(monkeypatch, role_repo, menu_repo)

    with pytest.raises(HTTPException):
        await update_role("r1", RoleUpdateRequest(is_read_only=True), current_user=_caller())

    assert role_repo._role.is_read_only is True  # the write landed despite the failure

    menu_repo._sync_result = True
    await update_role("r1", RoleUpdateRequest(is_read_only=True), current_user=_caller())

    assert menu_repo.synced_role_ids == ["r1", "r1"]
