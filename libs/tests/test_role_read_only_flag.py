"""
`roles.is_read_only` caps a role at read on every page it holds. Existing role documents
in the live database have no such field, so it must default to False -- a missing field
must never silently turn an operational role read-only.
"""
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
