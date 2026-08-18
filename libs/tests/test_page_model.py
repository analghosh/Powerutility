"""
The page model: holding a page grants read/write/delete on it, unless the role is
flagged read-only, in which case it grants read alone.

`approve` is deliberately absent from PAGE_VERBS. Approving a bid and submitting it to
the exchange is a different authority from creating one; under the guard's rules only
admin and superadmin reach those routes. Adding "approve" here would collapse maker and
checker into one right for every role that holds a bid screen.

The administrative resources (ADMIN_RESOURCES) are the model's one exception: holding the
Users, Roles or Administration page grants `read` and nothing more, for every role.
"""
import pytest

from libs.authz.page_model import (
    ADMIN_RESOURCES,
    PAGE_VERBS,
    READ_ONLY_VERBS,
    permission_strings,
    verbs_for_resource,
    verbs_for_role,
)


def test_page_verbs_are_read_write_delete():
    assert PAGE_VERBS == ("read", "write", "delete")


def test_approve_is_not_granted_by_holding_a_page():
    assert "approve" not in PAGE_VERBS
    assert "approve" not in READ_ONLY_VERBS


def test_a_read_only_role_gets_read_alone():
    assert verbs_for_role(True) == ("read",)


def test_an_ordinary_role_gets_the_full_page_verbs():
    assert verbs_for_role(False) == PAGE_VERBS


def test_permission_strings_pair_every_resource_with_every_verb():
    assert permission_strings(["dashboard"], False) == [
        "dashboard:read",
        "dashboard:write",
        "dashboard:delete",
    ]


def test_a_read_only_role_gets_one_string_per_resource():
    assert permission_strings(["dam_single_bid", "dashboard"], True) == [
        "dam_single_bid:read",
        "dashboard:read",
    ]


def test_duplicate_resources_collapse():
    # "Setup" and "Administration" are different menus that both resolve to `admin`.
    assert permission_strings(["admin", "admin"], True) == ["admin:read"]


def test_order_is_stable_so_diffs_stay_readable():
    first = permission_strings(["b", "a"], False)
    second = permission_strings(["b", "a"], False)
    assert first == second
    assert first[0] == "b:read"


def test_empty_resource_names_are_dropped():
    assert permission_strings(["dashboard", "", None], True) == ["dashboard:read"]


# --- administrative resources ------------------------------------------------------
#
# The page model mints permission strings that `global_authz_guard` satisfies by plain
# membership: DELETE /api/v1/users/{id} resolves to exactly `users:delete`. Granting an
# ordinary role the Users page therefore handed it user administration -- measured on the
# migrated database, `user` (740 live accounts), `operator`, `checker` and `test123` all
# received users:delete and admin:delete, and a plain `user` account deleted another user.
# That guard has no AUTHZ_MODE switch, so the warn/soak/enforce rollout gives no cover.


def test_the_administrative_resources_are_the_ones_the_auth_guard_protects():
    assert ADMIN_RESOURCES == frozenset({"users", "roles", "admin", "menu", "menus"})


def test_an_ordinary_role_holding_the_users_page_gets_read_only():
    assert permission_strings(["users"], False) == ["users:read"]


def test_an_ordinary_role_holding_the_roles_page_gets_read_only():
    assert permission_strings(["roles"], False) == ["roles:read"]


def test_an_ordinary_role_holding_the_admin_page_gets_read_only():
    assert permission_strings(["admin"], False) == ["admin:read"]


def test_an_ordinary_role_holding_the_menu_page_gets_read_only():
    # Latent, not live: no seeded menu slugs to "menu", and menu:create is super-only.
    # But global_authz_guard derives "menu" for the auth service's menu router
    # (services/auth/app/main.py + libs/core/authz/config.py), so DELETE
    # /api/v1/menu/{id} needs exactly menu:delete -- capped here the same as users/roles.
    assert permission_strings(["menu"], False) == ["menu:read"]


@pytest.mark.parametrize("resource", sorted(ADMIN_RESOURCES))
def test_no_administrative_write_or_delete_is_ever_minted(resource):
    for is_read_only in (False, True):
        perms = permission_strings([resource], is_read_only)
        assert f"{resource}:write" not in perms
        assert f"{resource}:delete" not in perms


def test_the_restriction_is_per_resource_not_per_call():
    # A role holding both an administrative page and an operational one keeps full rights
    # on the operational page. Capping the whole call would break every real role.
    assert permission_strings(["users", "dam_single_bid"], False) == [
        "users:read",
        "dam_single_bid:read",
        "dam_single_bid:write",
        "dam_single_bid:delete",
    ]


def test_a_read_only_role_is_unaffected_by_the_administrative_cap():
    assert permission_strings(["users", "dashboard"], True) == [
        "users:read",
        "dashboard:read",
    ]


def test_verbs_for_resource_caps_administrative_pages_only():
    assert verbs_for_resource("users", False) == READ_ONLY_VERBS
    assert verbs_for_resource("dashboard", False) == PAGE_VERBS
    assert verbs_for_resource("dashboard", True) == READ_ONLY_VERBS
