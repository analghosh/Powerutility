"""The analytics reads depend on indexes that do not exist.

exchange_serve and exchange_serve_historical carry only _id, so every ACP/ACV query
is a collection scan. The vendor's index bootstrap cannot be reused: it aborts on the
users.email unique conflict (813 null emails) and has never completed anywhere.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from migrations import analytics_indexes_2026_09_17 as mig  # noqa: E402


def test_every_exchange_collection_is_covered():
    covered = {s["collection"] for s in mig.ANALYTICS_INDEXES}
    assert "exchange_serve" in covered
    assert "exchange_serve_historical" in covered


def test_exchange_index_leads_with_scheduled_at():
    """A range scan on the date must be able to use the index prefix."""
    spec = next(s for s in mig.ANALYTICS_INDEXES if s["collection"] == "exchange_serve")
    assert spec["keys"][0] == ("scheduled_at", 1)
    assert ("market_type", 1) in spec["keys"]
    assert ("time_block", 1) in spec["keys"]
    assert ("px_name", 1) in spec["keys"]


def test_every_spec_has_an_explicit_name():
    for s in mig.ANALYTICS_INDEXES:
        assert s["name"], f"{s['collection']} index needs an explicit name"


def test_no_index_is_declared_unique():
    """These are read-path indexes. A stray unique would reject legitimate writes."""
    for s in mig.ANALYTICS_INDEXES:
        assert s.get("unique", False) is False


@pytest.mark.asyncio
async def test_apply_creates_each_index_in_background():
    db = MagicMock()
    collections = {}

    def _get(name):
        collections.setdefault(name, MagicMock(create_index=AsyncMock(return_value=name)))
        return collections[name]

    db.__getitem__.side_effect = _get

    created = await mig.apply(db)

    assert len(created) == len(mig.ANALYTICS_INDEXES)
    for spec in mig.ANALYTICS_INDEXES:
        # A collection can carry several specs (analytics_export_jobs has three),
        # so match on the named call rather than only the most recent one.
        calls = collections[spec["collection"]].create_index.call_args_list
        matching = [c for c in calls if c.kwargs.get("name") == spec["name"]]
        assert matching, f"{spec['collection']}.{spec['name']} was never created"
        assert matching[0].kwargs["background"] is True
        assert matching[0].args[0] == spec["keys"]


@pytest.mark.asyncio
async def test_apply_is_idempotent_when_the_index_already_exists():
    from pymongo.errors import OperationFailure

    db = MagicMock()
    coll = MagicMock(create_index=AsyncMock(side_effect=OperationFailure("already exists")))
    db.__getitem__.return_value = coll

    created = await mig.apply(db)  # must not raise

    assert created == []
