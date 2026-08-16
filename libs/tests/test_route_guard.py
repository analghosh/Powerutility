"""
The authorization guard that closes VAPT F6.

The processing service authenticated every request and authorized none of them. These
tests pin the three decisions that make the guard safe to switch on:

* the action is derived from the HTTP method, so a route added tomorrow is covered;
* checker-side endpoints demand `approve`, which no ordinary role holds, so a maker
  cannot push a bid to the exchange;
* `admin` and `superadmin` are allowed through on their role, because `roles.permissions`
  in the live database is a stale snapshot and gating the owners on it locks them out.
"""
import pytest
from fastapi import HTTPException

from libs.authz.route_guard import (
    ACTION_APPROVE,
    ACTION_DELETE,
    ACTION_READ,
    ACTION_WRITE,
    MODE_OFF,
    MODE_WARN,
    action_for,
    get_mode,
    is_allowed,
    require,
)


class _Request:
    """Minimal stand-in: the guard only reads method, url.path and state.current_user."""

    class _URL:
        def __init__(self, path):
            self.path = path

    class _State:
        pass

    def __init__(self, method, path, user=None):
        self.method = method
        self.url = self._URL(path)
        self.state = self._State()
        if user is not None:
            self.state.current_user = user


def _user(roles=(), permissions=()):
    return {"user_id": "u1", "roles": list(roles), "permissions": list(permissions)}


async def _run(dependency, request):
    return await dependency(request)


# --- method to action ------------------------------------------------------


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_reads_are_reads(method):
    assert action_for(method, "/api/v2/iex-api-details/") == ACTION_READ


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH"])
def test_mutations_are_writes(method):
    assert action_for(method, "/api/v2/iex-api-details/1") == ACTION_WRITE


def test_delete_is_its_own_action():
    assert action_for("DELETE", "/api/v2/price-groups/1") == ACTION_DELETE


def test_cancelling_staged_data_is_a_write_not_a_master_delete():
    # Clearing a staging area is day-to-day work; it must not need `delete` on the screen.
    assert action_for("DELETE", "/api/v2/dam-scheduling/staged/cancel") == ACTION_WRITE


# --- the approve boundary --------------------------------------------------


@pytest.mark.parametrize("path", [
    "/api/v2/block-bid/submit-to-exchange",
    "/api/v2/block-bid/update-status",
    "/api/v2/limit-values/approve/64f",
    "/api/v2/limit-values/approve-all",
    "/api/v2/limit-values/approve-batch",
    "/api/v2/limit-values/reject/64f",
    "/api/v2/limit-values/reject-batch",
    "/api/v2/dam-bidding/submit-for-pre-exchange",
    "/api/v2/rtm-bidding/submit-pre-exchange-data",
])
def test_checker_endpoints_require_approve(path):
    assert action_for("POST", path) == ACTION_APPROVE


def test_submitting_for_approval_is_the_makers_write_not_an_approval():
    # "/approve" is deliberately not a substring of "/submit-for-approval".
    assert action_for("POST", "/api/v2/dam-bidding/submit-for-approval") == ACTION_WRITE


def test_reading_an_approval_list_is_still_a_read():
    assert action_for("GET", "/api/v2/limit-values/approve/64f") == ACTION_READ


# --- permission evaluation -------------------------------------------------


def test_exact_permission_allows():
    assert is_allowed(_user(permissions=["dam_block_bid:write"]), "dam_block_bid", "write")


def test_wrong_action_denies():
    assert not is_allowed(_user(permissions=["dam_block_bid:write"]), "dam_block_bid", "approve")


def test_wrong_resource_denies():
    assert not is_allowed(_user(permissions=["dam_block_bid:write"]), "iex_api_detail", "write")


def test_resource_wildcard_allows_every_action():
    assert is_allowed(_user(permissions=["masters:*"]), "masters", "delete")


def test_global_wildcard_allows_everything():
    assert is_allowed(_user(permissions=["*"]), "anything", "approve")


def test_any_of_several_resources_is_enough():
    # limit-values serves the DAM, RTM and OCF screens from one router.
    user = _user(permissions=["rtm_limit_values:write"])
    assert is_allowed(user, ("dam_limit_values", "rtm_limit_values", "masters"), "write")


def test_superadmin_passes_on_role_even_with_stale_permissions():
    # The live database gave superadmin a read-only permission set for the whole
    # deployment's life. Without this, enabling the guard locks the owners out.
    assert is_allowed(_user(roles=["superadmin"], permissions=["dashboard:read"]),
                      "iex_api_detail", "delete")


def test_admin_passes_on_role():
    assert is_allowed(_user(roles=["Admin"], permissions=[]), "iex_api_detail", "delete")


def test_no_user_is_never_allowed():
    assert not is_allowed(None, "dashboard", "read")
    assert not is_allowed({}, "dashboard", "read")


def test_non_string_permissions_do_not_crash_the_guard():
    assert not is_allowed({"roles": [None, 1], "permissions": [None, 2]}, "dashboard", "read")


# --- the dependency --------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_permission_is_a_403_naming_what_was_needed():
    dep = require("iex_api_detail")
    request = _Request("PUT", "/api/v2/iex-api-details/1", _user(permissions=["iex_api_detail:read"]))

    with pytest.raises(HTTPException) as excinfo:
        await _run(dep, request)

    assert excinfo.value.status_code == 403
    assert "iex_api_detail:write" in excinfo.value.detail


@pytest.mark.asyncio
async def test_holding_the_permission_passes():
    dep = require("iex_api_detail")
    request = _Request("PUT", "/api/v2/iex-api-details/1", _user(permissions=["iex_api_detail:write"]))

    assert await _run(dep, request) is None


@pytest.mark.asyncio
async def test_read_permission_alone_still_allows_reads():
    dep = require("iex_api_detail")
    request = _Request("GET", "/api/v2/iex-api-details/", _user(permissions=["iex_api_detail:read"]))

    assert await _run(dep, request) is None


@pytest.mark.asyncio
async def test_absent_user_context_is_a_401_not_a_silent_pass():
    dep = require("dashboard")
    request = _Request("GET", "/api/v2/dashboard/", user=None)

    with pytest.raises(HTTPException) as excinfo:
        await _run(dep, request)

    assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_skip_suffix_lets_a_self_authenticating_route_through():
    # The SSE stream authenticates with a ticket and never gets a JWT user context.
    dep = require("dashboard", skip_path_suffixes=("/notifications/stream",))
    request = _Request("GET", "/api/v2/notifications/stream", user=None)

    assert await _run(dep, request) is None


@pytest.mark.asyncio
async def test_warn_mode_allows_and_does_not_raise(monkeypatch):
    monkeypatch.setenv("AUTHZ_MODE", MODE_WARN)
    dep = require("iex_api_detail")
    request = _Request("DELETE", "/api/v2/iex-api-details/1", _user(permissions=[]))

    assert await _run(dep, request) is None


@pytest.mark.asyncio
async def test_off_mode_skips_the_check_entirely(monkeypatch):
    monkeypatch.setenv("AUTHZ_MODE", MODE_OFF)
    dep = require("iex_api_detail")
    request = _Request("DELETE", "/api/v2/iex-api-details/1", user=None)

    assert await _run(dep, request) is None


def test_an_unknown_mode_falls_back_to_enforcing(monkeypatch):
    monkeypatch.setenv("AUTHZ_MODE", "loose")
    assert get_mode() == "enforce"


def test_default_mode_is_enforce(monkeypatch):
    monkeypatch.delenv("AUTHZ_MODE", raising=False)
    assert get_mode() == "enforce"
