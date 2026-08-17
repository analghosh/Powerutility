"""
Service-to-service credentials, and the guard's fixed-action override.

The scheduler shipped with authentication commented out — "AuthMiddleware removed to allow
internal trigger without authentication" — because five call sites in the processing
service call it with no credentials. Switching it back on without giving those callers
something to present would have broken RTM scheduling and the cron orchestrator silently,
which on this platform reads as a missed bid window rather than an error.
"""
import pytest
from fastapi import HTTPException

from libs.authz.route_guard import ACTION_READ, ACTION_WRITE, is_allowed, require
from libs.authz.service_auth import INTERNAL_TOKEN_HEADER, internal_headers


class _Request:
    class _URL:
        def __init__(self, path):
            self.path = path

    class _State:
        pass

    def __init__(self, method, path, user=None, headers=None):
        self.method = method
        self.url = self._URL(path)
        self.headers = headers or {}
        self.state = self._State()
        if user is not None:
            self.state.current_user = user


# --- the header helper ----------------------------------------------------


def test_a_configured_token_produces_the_header():
    assert internal_headers("s3cret") == {INTERNAL_TOKEN_HEADER: "s3cret"}


def test_no_token_produces_no_header_rather_than_an_empty_one():
    # A service with no secret configured must fail loudly with a 401, not send an empty
    # header that could match an empty expected value.
    assert internal_headers(None) == {}
    assert internal_headers("") == {}


def test_extra_headers_are_preserved():
    assert internal_headers("s3cret", {"X-Correlation-ID": "abc"}) == {
        "X-Correlation-ID": "abc",
        INTERNAL_TOKEN_HEADER: "s3cret",
    }


def test_the_header_name_is_shared_with_the_middleware():
    # Both sides import this constant; if they drift, every internal call 401s.
    from libs.middleware import auth_middleware

    assert auth_middleware.INTERNAL_TOKEN_HEADER == INTERNAL_TOKEN_HEADER


# --- the service context --------------------------------------------------


def test_a_service_context_passes_any_guard():
    service = {"user_id": "internal-service", "roles": ["service"], "permissions": ["*"]}
    assert is_allowed(service, "admin", ACTION_WRITE)
    assert is_allowed(service, ("dam_block_bid",), "approve")


def test_the_service_role_alone_grants_nothing():
    # The wildcard is what grants access, not the role name — so a token that somehow
    # carried roles=["service"] without permissions is powerless.
    assert not is_allowed({"roles": ["service"], "permissions": []}, "admin", ACTION_WRITE)


# --- the fixed-action override --------------------------------------------


@pytest.mark.asyncio
async def test_a_pinned_action_ignores_the_method():
    # The scheduler pins `write`: a GET there returns the entire job inventory, and nearly
    # every role holds some admin:read from the Administration menu.
    dep = require("admin", action=ACTION_WRITE)
    reader = {"roles": ["user"], "permissions": ["admin:read"]}

    with pytest.raises(HTTPException) as excinfo:
        await dep(_Request("GET", "/jobs/all", reader))

    assert excinfo.value.status_code == 403
    assert "admin:write" in excinfo.value.detail


@pytest.mark.asyncio
async def test_a_pinned_action_still_admits_the_holder():
    dep = require("admin", action=ACTION_WRITE)
    assert await dep(_Request("GET", "/jobs/all", {"roles": ["x"], "permissions": ["admin:write"]})) is None


@pytest.mark.asyncio
async def test_without_a_pin_the_method_still_decides():
    dep = require("admin")
    reader = {"roles": ["user"], "permissions": ["admin:read"]}

    assert await dep(_Request("GET", "/jobs/all", reader)) is None
    with pytest.raises(HTTPException):
        await dep(_Request("POST", "/jobs/all", reader))


@pytest.mark.asyncio
async def test_the_internal_service_context_passes_the_pinned_guard():
    # This is the case that keeps processing -> scheduler working.
    dep = require("admin", action=ACTION_WRITE)
    service = {"user_id": "internal-service", "roles": ["service"], "permissions": ["*"]}
    assert await dep(_Request("POST", "/api/v1/scheduler/scheduler/jobs/x/trigger", service)) is None


def test_read_and_write_are_distinct_constants():
    assert ACTION_READ != ACTION_WRITE
