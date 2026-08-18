"""
`roles.is_read_only` caps a role at read on every page it holds. Existing role documents
in the live database have no such field, so it must default to False -- a missing field
must never silently turn an operational role read-only.
"""
import pytest

from services.auth.app.models.roles import RoleCreate, RoleInDB, RoleUpdate


def test_an_existing_role_document_without_the_field_is_not_read_only():
    role = RoleInDB(**{
        "_id": "694e8ac4e5342637ee485d8c",
        "name": "operator",
        "description": "",
        "permissions": [],
        "is_active": True,
    })
    assert role.is_read_only is False


def test_the_flag_round_trips_when_present():
    role = RoleInDB(**{
        "_id": "694e8ac4e5342637ee485d8e",
        "name": "viewer",
        "description": "",
        "permissions": [],
        "is_active": True,
        "is_read_only": True,
    })
    assert role.is_read_only is True


def test_role_create_defaults_to_not_read_only():
    assert RoleCreate(name="planner").is_read_only is False


def test_role_create_accepts_the_flag():
    assert RoleCreate(name="observer", is_read_only=True).is_read_only is True


def test_role_update_leaves_the_flag_alone_when_omitted():
    # None means "not supplied" -- an update that only renames must not clear the flag.
    assert RoleUpdate(name="observer").is_read_only is None


def test_role_update_can_clear_the_flag():
    assert RoleUpdate(is_read_only=False).is_read_only is False


# --- the seeded roles ----------------------------------------------------------------


def test_the_seeded_viewer_role_is_flagged_read_only():
    """
    `viewer` exists to be read-only. Seeding it without the flag makes a fresh deployment
    -- or any force_reload -- create it as a full read/write/delete role, which under the
    page model turns its 51 read permissions into 153 with 51 writes and 51 deletes.
    """
    from services.auth.app.core.seeder import DEFAULT_ROLES

    by_name = {r["name"]: r for r in DEFAULT_ROLES}
    assert by_name["viewer"].get("is_read_only") is True
    for name in ("admin", "operator", "user"):
        assert by_name[name].get("is_read_only", False) is False


@pytest.mark.asyncio
async def test_seed_roles_passes_the_flag_through_to_create_role():
    """The list is only half of it -- the seeder has to actually pass the field on."""
    from services.auth.app.core.seeder import DatabaseSeeder

    created = {}

    class FakeRoleRepo:
        async def get_role_by_name(self, name):
            return None

        async def create_role(self, name, description="", is_read_only=False, **_kw):
            created[name] = is_read_only
            return type("R", (), {"name": name})()

    seeder = DatabaseSeeder.__new__(DatabaseSeeder)
    seeder.role_repository = FakeRoleRepo()

    assert await seeder.seed_roles() is True
    assert created["viewer"] is True
    assert created["admin"] is False
    assert created["operator"] is False
    assert created["user"] is False
