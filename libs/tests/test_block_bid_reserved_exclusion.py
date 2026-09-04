"""Reserved block bids must never reach the exchange.

The approval screen marks each block bid either "Bid" or "Reserved". Only "Bid"
rows are submitted; Reserved ones are held back deliberately. Before this guard
existed, get_approved_bids matched on status alone, so Reserved rows were sent to
IEX and — because the $group keeps one document per time block — a Reserved
revision could also displace the real Bid row for the same block.
"""
import asyncio
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.repository.block_bid_current_data_repository import (  # noqa: E402
    NOT_RESERVED_MATCH,
    BlockBidCurrentDataRepository,
)

RESERVED_PATTERN = NOT_RESERVED_MATCH["selected_for"]["$not"]["$regex"]


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, length=None):
        return self._docs


class _FakeCollection:
    """Records the pipeline it is handed and replays a canned result."""

    def __init__(self, docs=None):
        self.pipelines = []
        self._docs = docs or []

    def aggregate(self, pipeline):
        self.pipelines.append(pipeline)
        return _FakeCursor(self._docs)


def _repo_with(collection):
    repo = object.__new__(BlockBidCurrentDataRepository)
    repo.collection_name = "block_bid_current_data"
    type(repo).collection = property(lambda self: collection)
    return repo


@pytest.mark.parametrize(
    "value",
    ["Reserved", "reserved", "RESERVED", " Reserved ", "\treserved\n"],
)
def test_every_spelling_of_reserved_is_excluded(value):
    assert re.match(RESERVED_PATTERN, value, re.IGNORECASE)


@pytest.mark.parametrize("value", ["Bid", "bid", "", "Reserved bid", "unreserved"])
def test_nothing_else_is_excluded(value):
    assert not re.match(RESERVED_PATTERN, value, re.IGNORECASE)


def test_get_approved_bids_excludes_reserved_before_grouping():
    collection = _FakeCollection()
    repo = _repo_with(collection)
    try:
        asyncio.run(repo.get_approved_bids("2026-08-18", market_type="DAM"))
    finally:
        del type(repo).collection

    pipeline = collection.pipelines[0]
    match = pipeline[0]["$match"]

    assert match["status"] == "Approved"
    assert match["market_type"] == "DAM"
    assert match["selected_for"] == NOT_RESERVED_MATCH["selected_for"]

    # The exclusion has to run before the $group, otherwise a Reserved revision can
    # still win the $first slot of a time block that has a real Bid row.
    group_index = next(i for i, stage in enumerate(pipeline) if "$group" in stage)
    assert group_index > 0


def test_bulk_update_status_does_not_flip_reserved_rows():
    collection = _FakeCollection()
    repo = _repo_with(collection)
    try:
        asyncio.run(
            repo.bulk_update_status("2026-08-18", "Approved", "Submitted", market_type="DAM")
        )
    finally:
        del type(repo).collection

    match = collection.pipelines[0][0]["$match"]
    assert match["selected_for"] == NOT_RESERVED_MATCH["selected_for"]


def test_rows_written_before_the_field_existed_are_still_submitted():
    """A missing selected_for must not be read as Reserved.

    Mongo's {$not: {$regex: ...}} matches documents that lack the field, which is
    why the filter is written as an exclusion rather than selected_for == "Bid".
    """
    assert "$not" in NOT_RESERVED_MATCH["selected_for"]
    assert NOT_RESERVED_MATCH["selected_for"].get("$eq") is None
