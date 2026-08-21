"""Which block bids a Submit to Exchange picks up, and which reach the Bid Status screen.

Two defects reported on 2026-08-21, both in
`block_bid_current_data_repository`, both about the mongo pipeline rather than
any UI code:

1. "Submit to Exchange has to be clicked twice." `get_approved_bids` collapsed to
   one document per (trade_session, time_block) with $first on revision_no. On
   block bids revision_no is NOT a revision: `insert_block_bid` assigns it as
   max+1 for that pair, and no code path ever edits a block bid, so every insert
   is a separate bid the trader placed. One click therefore submitted one bid per
   block and left its siblings Approved. Measured on the UAT copy: 11 DAM time
   blocks carry more than one bid and one carries 11.

2. Reserved bids appeared under "Block Bid Submitted" on the Bid Status screen.
   `get_block_bid_data_by_date_and_market_type` applied no selected_for filter,
   unlike every submit path in the same file.

Both fixes are opt-in flags so the fenced RTM call sites keep their behaviour.
These tests exercise the pipeline/query construction, which is where the defects
live — they need no mongo.
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.repository import (  # noqa: E402
    block_bid_current_data_repository as repo_mod,
)

BlockBidCurrentDataRepository = repo_mod.BlockBidCurrentDataRepository
NOT_RESERVED_MATCH = repo_mod.NOT_RESERVED_MATCH


# ------------------------------------------------------- the flags exist ---

def test_the_three_methods_take_the_opt_in_flags():
    reserved = inspect.signature(
        BlockBidCurrentDataRepository.get_block_bid_data_by_date_and_market_type
    ).parameters
    assert "include_reserved" in reserved
    assert reserved["include_reserved"].default is True, "RTM must keep today's behaviour"

    approved = inspect.signature(BlockBidCurrentDataRepository.get_approved_bids).parameters
    assert "latest_revision_only" in approved
    assert approved["latest_revision_only"].default is True, "RTM must keep today's behaviour"

    bulk = inspect.signature(BlockBidCurrentDataRepository.bulk_update_status).parameters
    assert "latest_revision_only" in bulk
    assert bulk["latest_revision_only"].default is True, "RTM must keep today's behaviour"


# --------------------------------------- 1. one click submits every bid ----

def _source(fn):
    return inspect.getsource(fn)


def test_collapsing_by_time_block_is_conditional_now():
    """The $group that dropped sibling bids must sit behind the flag."""
    src = _source(BlockBidCurrentDataRepository.get_approved_bids)

    assert "if latest_revision_only:" in src
    group_at = src.index('"$group"')
    guard_at = src.index("if latest_revision_only:")
    assert guard_at < group_at, "$group must be inside the flag branch"


def test_the_reserved_exclusion_is_never_behind_the_flag():
    """Reserved bids must stay excluded from submission whichever way the flag goes."""
    src = _source(BlockBidCurrentDataRepository.get_approved_bids)

    match_at = src.index('"$match"')
    guard_at = src.index("if latest_revision_only:")
    assert src.index("NOT_RESERVED_MATCH") < guard_at, "exclusion must be in the unconditional $match"
    assert match_at < guard_at


def test_bulk_update_status_takes_the_same_flag_so_the_two_cannot_drift():
    """If these two disagree, bids reach the exchange and stay Approved."""
    approved_src = _source(BlockBidCurrentDataRepository.get_approved_bids)
    bulk_src = _source(BlockBidCurrentDataRepository.bulk_update_status)

    for src in (approved_src, bulk_src):
        assert "if latest_revision_only:" in src
        assert "NOT_RESERVED_MATCH" in src


def test_the_day_ahead_submit_path_asks_for_every_approved_bid():
    """The whole point: one click, all bids."""
    from services.processing.app.v2.services.block_bid_services import BlockBidServices

    src = inspect.getsource(BlockBidServices.submit_dam_to_exchange)

    # Check the two call sites themselves, not comment prose that mentions the flag.
    calls = [
        line.strip()
        for line in src.splitlines()
        if "latest_revision_only=False" in line and not line.strip().startswith("#")
    ]
    assert len(calls) == 2, (
        f"get_approved_bids and bulk_update_status must both opt out; found {calls}"
    )
    assert any("market_type=market, latest_revision_only=False" in c for c in calls)
    assert any('"Approved", "Submitted"' in c for c in calls)


def test_rtm_submit_is_left_alone():
    """RTM is fenced. Deleting this test is a decision, not a cleanup."""
    from services.processing.app.v2.services.block_bid_services import BlockBidServices

    src = inspect.getsource(BlockBidServices.submit_to_exchange)

    assert 'get_approved_bids(biding_date, market_type="RTM")' in src
    assert "latest_revision_only" not in src


# ------------------------------- 2. Reserved bids off the Bid Status page ---

def test_reserved_rows_are_excluded_only_when_asked():
    src = _source(BlockBidCurrentDataRepository.get_block_bid_data_by_date_and_market_type)

    assert "if not include_reserved:" in src
    assert "NOT_RESERVED_MATCH" in src


def test_the_exclusion_tolerates_casing_whitespace_and_legacy_rows():
    """A bare {"selected_for": "Bid"} would blank the column for historical dates."""
    pattern = NOT_RESERVED_MATCH["selected_for"]["$not"]["$regex"]
    options = NOT_RESERVED_MATCH["selected_for"]["$not"]["$options"]

    assert "$not" in NOT_RESERVED_MATCH["selected_for"], "must be a negation, so missing fields match"
    assert "i" in options, "selected_for is stored verbatim and unvalidated"
    assert r"\s*" in pattern, "tolerate surrounding whitespace"

    import re

    rx = re.compile(pattern, re.IGNORECASE)
    for reserved in ("Reserved", "reserved", "  RESERVED  "):
        assert rx.match(reserved), f"{reserved!r} should be recognised as reserved"
    for kept in ("Bid", "bid", ""):
        assert not rx.match(kept), f"{kept!r} should survive the filter"


def test_all_three_day_ahead_bid_status_builders_opt_out():
    """iDAM, GDAM and HPDAM each hold their own copy of the payload builder."""
    base = Path(__file__).resolve().parents[2] / "services/processing/app/v2/services"

    for name in ("new_dam_bidding_services.py", "new_gdam_bidding_services.py",
                 "new_hpdam_bidding_services.py"):
        text = (base / name).read_text()
        call_at = text.index("get_block_bid_data_by_date_and_market_type")
        window = text[call_at:call_at + 300]
        assert "include_reserved=False" in window, f"{name} still sends Reserved rows"


def test_rtm_bid_status_is_left_alone():
    """RTM is fenced — its Bid Status page keeps showing what it shows today."""
    base = Path(__file__).resolve().parents[2] / "services/processing/app/v2/services"
    text = (base / "new_rtm_bidding_services.py").read_text()

    call_at = text.index("get_block_bid_data_by_date_and_market_type")
    window = text[call_at:call_at + 300]
    assert "include_reserved" not in window
