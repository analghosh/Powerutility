"""
The exchange API key must never leave the processing service in plaintext.

`iex_api_detail_list` holds live IEX trading credentials. `GET /iex-api-details/`
carries authentication but no authorization, so every logged-in account -- including
read-only `viewer` -- can call it. It used to decrypt `api_key` on the way out.

Masking the response creates a second obligation: the edit form posts the record
back unchanged, so the service has to recognise its own placeholder and refuse to
store it. Overwriting a stored key with the mask would break exchange auth silently,
at bid time.

The mask is dots because the client's api_key field filters out everything outside
[A-Za-z0-9._-]; an asterisk mask sanitises to "" and blocks every credential edit.
"""
import re

import pytest
from fastapi import HTTPException

from services.processing.app.v2.services.iex_api_detail_service import (
    API_KEY_MASK,
    IexApiDetailService,
    is_masked_api_key,
)


class _StubRepository:
    """Stands in for IexApiDetailRepository -- no Mongo, no event loop binding."""

    def __init__(self, details=None, existing=None):
        self._details = details or []
        self._existing = existing
        self.update_payload = None

    async def get_all_details(self, include_deleted: bool = False):
        return [dict(d) for d in self._details]

    async def find_by_id(self, id, include_deleted: bool = False):
        return self._existing

    async def check_duplicate(self, data, exclude_id=None):
        return None

    async def update_detail(self, id, payload):
        self.update_payload = payload
        return None  # modified_count path not exercised here

    async def deactivate_other_environment(self, exchange, exclude_id=None):
        return None


def _service(repository):
    """Build the service without running __init__, which constructs repositories."""
    service = IexApiDetailService.__new__(IexApiDetailService)
    service.repository = repository
    service.exchange_config_repo = None
    return service


# --- the mask itself -------------------------------------------------------


def test_placeholder_is_recognised_as_masked():
    assert is_masked_api_key(API_KEY_MASK)


def test_the_mask_survives_the_clients_api_key_character_filter():
    # client/src/utils/inputValidation.js strips /[^A-Za-z0-9._-]/g from this field and
    # validates the result against the same set. A mask outside it sanitises to "" and
    # the edit form then blocks every save with "API Key is required".
    assert re.sub(r"[^A-Za-z0-9._-]", "", API_KEY_MASK) == API_KEY_MASK


def test_common_mask_shapes_are_recognised():
    for value in ("********", "****", "••••••••", "........", "  ****  "):
        assert is_masked_api_key(value), value


def test_a_real_key_is_not_treated_as_masked():
    for value in ("sk_live_9f2a", "AbC123", "key*with*stars"):
        assert not is_masked_api_key(value), value


def test_empty_values_are_not_masked_keys():
    # Empty means "no key supplied", which the caller already handles separately.
    for value in (None, "", "   "):
        assert not is_masked_api_key(value)


# --- get_all ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_all_never_returns_the_stored_key():
    repo = _StubRepository([{"_id": "1", "api_key": "encrypted-ciphertext"}])

    details = await _service(repo).get_all()

    assert details[0]["api_key"] == API_KEY_MASK
    assert "encrypted-ciphertext" not in str(details)


@pytest.mark.asyncio
async def test_get_all_flags_whether_a_key_is_configured():
    repo = _StubRepository([
        {"_id": "1", "api_key": "encrypted-ciphertext"},
        {"_id": "2", "api_key": ""},
        {"_id": "3"},
    ])

    details = await _service(repo).get_all()

    assert [d["has_api_key"] for d in details] == [True, False, False]
    assert [d["api_key"] for d in details] == [API_KEY_MASK, "", ""]


# --- update ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_does_not_overwrite_the_stored_key_with_the_mask():
    repo = _StubRepository(existing={"_id": "1", "exchange": "IEX", "environment": "PROD"})

    await _service(repo).update("1", {"api_key": API_KEY_MASK, "portfolio_id": "P1"})

    assert "api_key" not in repo.update_payload
    assert repo.update_payload["portfolio_id"] == "P1"


@pytest.mark.asyncio
async def test_update_still_stores_a_real_key_and_stores_it_encrypted():
    repo = _StubRepository(existing={"_id": "1", "exchange": "IEX", "environment": "PROD"})

    await _service(repo).update("1", {"api_key": "brand-new-key"})

    assert repo.update_payload["api_key"] != "brand-new-key"
    assert repo.update_payload["api_key"]


# --- create ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rejects_the_mask_instead_of_storing_it():
    repo = _StubRepository()

    with pytest.raises(HTTPException) as excinfo:
        await _service(repo).create({"api_key": API_KEY_MASK, "asset_id": "A1"})

    assert excinfo.value.status_code == 400
