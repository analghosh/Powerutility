"""
The Role Management API must carry is_read_only in both directions, and flipping it must
re-sync that role's permissions immediately -- otherwise the role keeps its old strings
until somebody happens to add or remove one of its menus.
"""
from services.auth.app.api.v1.routes.role_management import (
    RoleCreateRequest,
    RoleResponse,
    RoleUpdateRequest,
)


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
