"""
Authorization on the two `/mfa/admin/*` endpoints.

Both were unreachable. The gate read

    if "admin" not in roles and "<perm>" not in permissions: 403

and neither `mfa:emergency_disable` nor `mfa:reset_rate_limit` existed in the permissions
catalogue, so the second clause could never fire; while a superadmin carries
`permissions=["*"]`, which a literal `in` test does not satisfy — so the platform owner was
refused the one endpoint that recovers a user who has lost their authenticator.

The check now goes through `libs.authz.route_guard.is_allowed`, the same primitive the
processing service's route guards use.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.auth.app.api.v1.routes.mfa import (
    _ACTION_EMERGENCY_DISABLE,
    _ACTION_RESET_RATE_LIMIT,
    _require_mfa_admin,
)
from libs.core.authz.permissions_catalog import DEFAULT_PERMISSIONS_CATALOG


def _principal(roles=None, permissions=None):
    return SimpleNamespace(
        id="507f1f77bcf86cd799439011",
        username="probe",
        roles=roles or [],
        permissions=permissions or [],
    )


ACTIONS = [_ACTION_EMERGENCY_DISABLE, _ACTION_RESET_RATE_LIMIT]


@pytest.mark.parametrize("action", ACTIONS)
class TestWhoIsAllowed:
    def test_admin_role_passes(self, action):
        _require_mfa_admin(_principal(roles=["admin"]), action)

    def test_superadmin_role_passes(self, action):
        """The case the old gate got wrong."""
        _require_mfa_admin(_principal(roles=["superadmin"]), action)

    def test_superadmin_wildcard_permission_passes(self, action):
        """A superadmin resolves to permissions=["*"] in the auth repository."""
        _require_mfa_admin(_principal(roles=["superadmin"], permissions=["*"]), action)

    def test_mixed_case_role_passes(self, action):
        _require_mfa_admin(_principal(roles=["SuperAdmin"]), action)

    def test_the_explicit_permission_passes(self, action):
        _require_mfa_admin(_principal(roles=["operator"], permissions=[f"mfa:{action}"]), action)

    def test_the_mfa_wildcard_passes(self, action):
        _require_mfa_admin(_principal(roles=["operator"], permissions=["mfa:*"]), action)

    def test_a_plain_user_is_refused(self, action):
        with pytest.raises(HTTPException) as exc_info:
            _require_mfa_admin(_principal(roles=["user"], permissions=["dashboard:read"]), action)
        assert exc_info.value.status_code == 403
        assert f"mfa:{action}" in exc_info.value.detail

    def test_an_unrelated_mfa_permission_is_not_enough(self, action):
        """`mfa:setup` is held by ordinary users; it must not confer admin recovery."""
        with pytest.raises(HTTPException) as exc_info:
            _require_mfa_admin(
                _principal(roles=["user"], permissions=["mfa:setup", "mfa:disable"]), action
            )
        assert exc_info.value.status_code == 403

    def test_a_principal_with_nothing_is_refused(self, action):
        with pytest.raises(HTTPException):
            _require_mfa_admin(_principal(), action)

    def test_the_other_actions_permission_is_not_enough(self, action):
        other = next(a for a in ACTIONS if a != action)
        with pytest.raises(HTTPException):
            _require_mfa_admin(
                _principal(roles=["operator"], permissions=[f"mfa:{other}"]), action
            )


class TestCatalogueConsistency:
    """
    A permission the code checks but the catalogue does not offer cannot be granted through
    the Roles & Permissions screen, which is how the previous gate became unreachable.
    """

    @pytest.mark.parametrize("action", ACTIONS)
    def test_the_permission_exists_in_the_catalogue(self, action):
        assert f"mfa:{action}" in DEFAULT_PERMISSIONS_CATALOG

    @pytest.mark.parametrize("action", ACTIONS)
    def test_the_seeder_keeps_the_catalogue_a_superset(self, action):
        from tools.resync_role_permissions import NON_MENU_PERMISSIONS

        assert f"mfa:{action}" in NON_MENU_PERMISSIONS

    def test_the_seeder_grants_them_to_no_ordinary_role(self):
        """
        Removing somebody's second factor is not a default delegation. admin and superadmin
        reach these endpoints on their role, so nothing needs granting.

        Under the page model (libs/authz/page_model.PAGE_VERBS) a page grant can only ever
        produce read/write/delete on a menu's own resource -- there is no per-role matrix
        left that could special-case an mfa action onto an ordinary role's permissions.
        """
        from libs.authz.page_model import PAGE_VERBS

        assert "emergency_disable" not in PAGE_VERBS
        assert "reset_rate_limit" not in PAGE_VERBS
