"""Per-user filter defaults for the analytics dashboard.

The store is a convenience, and the tests are about the two ways a convenience turns
into a defect:

1. **A preference is replayed into a form.** Anything stored is handed straight back
   to the filter panel, so the write path validates what may be stored and refuses
   everything else — an unvalidated blob replayed into a form is a payload.

2. **A preference must never be authority.** A stored dataset code is a suggestion;
   `/query` re-checks the registry and the grant on every request. That is what makes
   the read path free to be lenient — a code the registry has since lost is dropped
   and reported rather than making the whole row unreadable.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.repository import \
    analytics_preference_repository as prefs  # noqa: E402

LIVE_CODE = "SG_TOTAL"
OTHER_CODE = "DECLARED_CAPACITY_TOTAL"


@pytest.fixture
def repo():
    repository = prefs.AnalyticsPreferenceRepository()
    repository.collection = MagicMock(
        find_one=AsyncMock(return_value=None),
        update_one=AsyncMock(),
        delete_one=AsyncMock(return_value=MagicMock(deleted_count=1)),
    )
    return repository


# --------------------------------------------------------------------- validate

def test_validate_keeps_a_known_dataset_code():
    assert prefs.validate({"datasets": [LIVE_CODE]})["datasets"] == [LIVE_CODE]


def test_validate_refuses_a_dataset_the_registry_does_not_know():
    # At write time the caller picked from the catalogue this same process serves, so
    # an unknown code means the client sent something the screen cannot have offered.
    with pytest.raises(prefs.InvalidPreference):
        prefs.validate({"datasets": ["NOT_A_DATASET"]})


def test_validate_refuses_a_field_the_filter_panel_would_not_replay():
    with pytest.raises(prefs.InvalidPreference):
        prefs.validate({"datasets": [], "sneaky": {"$ne": None}})


def test_validate_refuses_an_unknown_format():
    with pytest.raises(prefs.InvalidPreference):
        prefs.validate({"format": "pdf"})


def test_validate_deduplicates_and_preserves_order():
    out = prefs.validate({"datasets": [OTHER_CODE, LIVE_CODE, OTHER_CODE]})
    assert out["datasets"] == [OTHER_CODE, LIVE_CODE]


def test_validate_caps_the_number_of_plants():
    with pytest.raises(prefs.InvalidPreference):
        prefs.validate({"plants": [f"P{i}" for i in range(prefs.MAX_PLANTS + 1)]})


def test_validate_refuses_a_plant_id_long_enough_to_be_a_payload():
    with pytest.raises(prefs.InvalidPreference):
        prefs.validate({"plants": ["x" * (prefs.MAX_ID_LENGTH + 1)]})


def test_validate_defaults_the_format_rather_than_storing_none():
    assert prefs.validate({})["format"] == "csv"


# --------------------------------------------------------------------- sanitize

def test_sanitize_drops_a_dataset_the_registry_has_lost_and_reports_it():
    preference, dropped = prefs.sanitize(
        {"datasets": [LIVE_CODE, "RETIRED_IN_A_RELEASE"]})
    assert preference["datasets"] == [LIVE_CODE]
    assert dropped == ["RETIRED_IN_A_RELEASE"]


def test_sanitize_of_a_missing_row_is_the_empty_preference():
    preference, dropped = prefs.sanitize(None)
    assert preference["datasets"] == [] and preference["format"] == "csv"
    assert dropped == []


def test_sanitize_repairs_a_format_that_is_no_longer_supported():
    preference, _ = prefs.sanitize({"format": "pdf"})
    assert preference["format"] == "csv"


# ------------------------------------------------------------------- repository

@pytest.mark.asyncio
async def test_get_returns_none_rather_than_raising_when_mongo_is_down(repo):
    # A preference store outage must not take the dashboard down with it: an empty
    # result and a failed read mean the same thing to the caller.
    repo.collection.find_one = AsyncMock(side_effect=RuntimeError("no mongo"))
    assert await repo.get("u1") is None


@pytest.mark.asyncio
async def test_save_upserts_on_the_user_id_and_stamps_the_time(repo):
    stored = await repo.save("u1", {"datasets": [LIVE_CODE], "format": "xlsx"})
    assert stored["saved_at"] is not None

    repo.collection.update_one.assert_awaited_once()
    filt, update = repo.collection.update_one.await_args.args[:2]
    assert filt == {"user_id": "u1"}
    assert update["$set"]["datasets"] == [LIVE_CODE]
    assert update["$set"]["format"] == "xlsx"
    assert repo.collection.update_one.await_args.kwargs["upsert"] is True


@pytest.mark.asyncio
async def test_save_refuses_before_touching_mongo(repo):
    with pytest.raises(prefs.InvalidPreference):
        await repo.save("u1", {"datasets": ["NOT_A_DATASET"]})
    repo.collection.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_clear_deletes_only_this_users_row(repo):
    assert await repo.clear("u1") is True
    repo.collection.delete_one.assert_awaited_once_with({"user_id": "u1"})


@pytest.mark.asyncio
async def test_clearing_what_was_never_set_is_not_an_error(repo):
    repo.collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=0))
    assert await repo.clear("u1") is False
