"""
Ingestion page access, after replacing the hardcoded dictionary (VAPT F1).

The report said there was no server-side permission check on upload. There was one, on
every route in the service — it just compared role names against a dictionary written into
`auth_client.py`, listing roles like `config_manager` and `data_analyst` that exist nowhere
in EPMS, while accepting the caller's token and ignoring it. Two copies of that dictionary
lived in the same file with different contents.

These tests pin the translation: the new check must grant exactly who the old dictionary
granted, so swapping the mechanism does not quietly change who can ingest data.
"""
from unittest.mock import MagicMock, patch

import pytest

from libs.database.mongo_client_factory import MongoClientFactory

with patch.object(MongoClientFactory, "get_client", return_value=MagicMock()):
    from services.ingestion.app.services.auth_client import PAGE_RESOURCES, AuthClient

# What the deleted dictionary granted.
OLD_BEHAVIOUR = {
    "Data Ingestion": {"admin", "operator"},
    "Provider Management": {"admin"},          # config_manager is not a real role
    "System Administration": {"admin"},
    "User Management": {"admin"},
}

# What each role holds after the matrix seeding, for the resources those pages use.
PERMISSIONS = {
    "admin": ["*"],
    "superadmin": ["*"],
    "operator": ["upload_csv:read", "upload_csv:write", "scada_data:read",
                 "scada_data:write", "dc_sg_template:read", "dc_sg_template:write",
                 "admin:read", "users:read", "reports:read"],
    "user": ["upload_csv:read", "scada_data:read", "dc_sg_template:read",
             "admin:read", "users:read", "reports:read"],
    "viewer": ["upload_csv:read", "admin:read", "users:read", "reports:read"],
}


def _client():
    return AuthClient.__new__(AuthClient)


async def _may(role, page):
    return await _client().check_page_access(
        [role], page, token="ignored", user_permissions=PERMISSIONS[role]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("page,allowed", OLD_BEHAVIOUR.items())
@pytest.mark.parametrize("role", ["admin", "operator", "user", "viewer"])
async def test_the_new_check_matches_the_dictionary_it_replaced(page, allowed, role):
    assert await _may(role, page) is (role in allowed), f"{role} on {page}"


@pytest.mark.asyncio
async def test_an_ordinary_user_still_cannot_ingest():
    # 739 of the accounts hold `user`. This is the line that matters.
    assert await _may("user", "Data Ingestion") is False


@pytest.mark.asyncio
async def test_an_operator_can_still_ingest():
    # Breaking this would stop the desk loading data — the reason the translation had to
    # preserve the old grants rather than tighten them.
    assert await _may("operator", "Data Ingestion") is True


@pytest.mark.asyncio
async def test_the_token_argument_is_no_longer_load_bearing_but_still_accepted():
    # Kept in the signature so no route file had to change.
    assert await _client().check_page_access(
        ["admin"], "Data Ingestion", token="", user_permissions=["*"]
    ) is True


@pytest.mark.asyncio
async def test_an_unknown_page_falls_back_to_administrator():
    assert await _may("admin", "Some Future Page") is True
    assert await _may("operator", "Some Future Page") is False


@pytest.mark.asyncio
async def test_no_permissions_means_no_access():
    assert await _client().check_page_access(["user"], "Data Ingestion", "", []) is False


@pytest.mark.asyncio
async def test_accessible_pages_agrees_with_the_per_page_check():
    for role in ["admin", "operator", "user", "viewer"]:
        listed = set(await _client().get_user_accessible_pages(
            [role], token="", user_permissions=PERMISSIONS[role]))
        for page in PAGE_RESOURCES:
            assert (page in listed) is await _may(role, page), f"{role} / {page}"


def test_every_page_maps_to_at_least_one_resource():
    assert PAGE_RESOURCES
    for page, resources in PAGE_RESOURCES.items():
        assert resources, f"{page} maps to nothing — it would fall back to admin silently"


def test_the_invented_role_names_are_gone():
    import inspect
    from services.ingestion.app.services import auth_client as module

    source = inspect.getsource(module)
    for invented in ("config_manager", "data_analyst", "manager"):
        assert f'"{invented}"' not in source, f"{invented} is not a role in this system"
