"""
The menu_roles grant-row models.

A grant row means "this role has this page"; the verbs come from roles.is_read_only via
libs/authz/page_model. The permissions list survives as a compatibility artifact, which
is why the defaults below are what they are -- and why the *update* model must stay
optional: on a partial update, None means "not supplied", which is not the same request
as ["read"].
"""
from services.auth.app.models.menu_role import MenuRoleCreate, MenuRoleUpdate


def test_menu_role_update_treats_a_missing_permissions_list_as_not_supplied():
    assert MenuRoleUpdate().permissions is None
    assert MenuRoleUpdate(permissions=None).permissions is None
    assert MenuRoleUpdate(permissions=["read"]).permissions == ["read"]


def test_a_new_grant_row_still_defaults_to_read():
    # Only the update model is optional. A row being created is ["read"].
    assert MenuRoleCreate(menu_id="m1", role_id="r1").permissions == ["read"]
