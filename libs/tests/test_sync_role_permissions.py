"""
_sync_role_permissions() rebuilds roles.permissions from the pages a role holds.

The repository constructors reach for Mongo, so the collaborators are stubbed and only
the decision logic is exercised. What matters here is the change of source: verbs used to
come from each menu_roles row (always ["read"], because the UI hardcodes it) and now come
from the role's is_read_only flag.
"""
import pytest

from services.auth.app.repository.menu_role_repository import MenuRoleRepository


class _Menu:
    def __init__(self, name):
        self.name = name


class _Role:
    def __init__(self, name, is_read_only=False):
        self.name = name
        self.is_read_only = is_read_only


class _MenuRole:
    def __init__(self, menu_id, permissions):
        self.menu_id = menu_id
        self.permissions = permissions


@pytest.fixture
def repo(monkeypatch):
    """A MenuRoleRepository with its three database collaborators replaced."""
    inst = MenuRoleRepository.__new__(MenuRoleRepository)
    inst._written = None

    menus = {
        "m1": _Menu("DAM Single Bid"),
        "m2": _Menu("Dashboard"),
        "m3": _Menu("Setup"),           # resolves to `admin`
        "m4": _Menu("Administration"),  # also resolves to `admin`
    }
    state = {"role": _Role("operator"), "grants": []}

    async def fake_get_menu_roles_by_role_id(role_id):
        return state["grants"]

    class FakeMenuRepo:
        async def get_menu_by_id(self, menu_id):
            return menus.get(menu_id)

    class FakeRoleRepo:
        async def get_role_by_id(self, role_id):
            return state["role"]

        async def set_role_permissions(self, role_id, permissions):
            inst._written = permissions
            return True

    inst.get_menu_roles_by_role_id = fake_get_menu_roles_by_role_id

    import services.auth.app.repository.menu_repository as menu_mod
    import services.auth.app.repository.role_repository as role_mod
    monkeypatch.setattr(menu_mod, "MenuRepository", FakeMenuRepo)
    monkeypatch.setattr(role_mod, "RoleRepository", FakeRoleRepo)

    inst._state = state
    return inst


@pytest.mark.asyncio
async def test_an_ordinary_role_gets_write_even_though_the_row_says_read(repo):
    repo._state["role"] = _Role("operator", is_read_only=False)
    repo._state["grants"] = [_MenuRole("m1", ["read"])]

    assert await repo._sync_role_permissions("r1") is True
    assert repo._written == [
        "dam_single_bid:read",
        "dam_single_bid:write",
        "dam_single_bid:delete",
    ]


@pytest.mark.asyncio
async def test_a_read_only_role_gets_read_even_though_the_row_says_write(repo):
    repo._state["role"] = _Role("viewer", is_read_only=True)
    repo._state["grants"] = [_MenuRole("m1", ["read", "write", "delete", "approve"])]

    assert await repo._sync_role_permissions("r1") is True
    assert repo._written == ["dam_single_bid:read"]


@pytest.mark.asyncio
async def test_no_role_receives_approve_from_holding_a_page(repo):
    repo._state["role"] = _Role("operator", is_read_only=False)
    repo._state["grants"] = [_MenuRole("m1", ["read"]), _MenuRole("m2", ["read"])]

    await repo._sync_role_permissions("r1")
    assert not any(p.endswith(":approve") for p in repo._written)


@pytest.mark.asyncio
async def test_two_menus_sharing_a_resource_name_collapse(repo):
    repo._state["role"] = _Role("superadmin", is_read_only=False)
    repo._state["grants"] = [_MenuRole("m3", ["read"]), _MenuRole("m4", ["read"])]

    await repo._sync_role_permissions("r1")
    assert repo._written == ["admin:read", "admin:write", "admin:delete"]


@pytest.mark.asyncio
async def test_a_missing_role_document_is_treated_as_not_read_only(repo):
    # get_role_by_id returns None for a role that was deleted mid-sync. Failing closed
    # here would silently strip an operational role down to read.
    repo._state["role"] = None
    repo._state["grants"] = [_MenuRole("m2", ["read"])]

    await repo._sync_role_permissions("r1")
    assert repo._written == ["dashboard:read", "dashboard:write", "dashboard:delete"]


@pytest.mark.asyncio
async def test_a_role_with_no_pages_gets_no_permissions(repo):
    repo._state["role"] = _Role("empty", is_read_only=False)
    repo._state["grants"] = []

    await repo._sync_role_permissions("r1")
    assert repo._written == []
