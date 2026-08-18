"""
The page model: holding a page grants read/write/delete on it, unless the role is
flagged read-only, in which case it grants read alone.

`approve` is deliberately absent from PAGE_VERBS. Approving a bid and submitting it to
the exchange is a different authority from creating one; under the guard's rules only
admin and superadmin reach those routes. Adding "approve" here would collapse maker and
checker into one right for every role that holds a bid screen.
"""
import pytest

from libs.authz.page_model import (
    PAGE_VERBS,
    READ_ONLY_VERBS,
    permission_strings,
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
