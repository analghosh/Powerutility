"""Tests for libs.notifications.single_bid_context.

The DAM/RTM single-bid approval screen lets a checker approve some rows and
reject others in the same batch (`/submit-pre-exchange-data`). The service
(new_dam_bidding_services.py:3408-3450) already narrows its response to an
`updated` list containing only references whose `modified_count > 0` -- but
that list carries no maker username, schedule date, or trustworthy status
grouping on its own: those live on the approval documents in the per-market
Mongo collections (`dam_single_bid_data_for_approval`, etc.), keyed by
`_id`, and must be joined in by `reference_number == str(doc["_id"])`.

group_single_bid_decisions() does that join and buckets the result by
lowercased status so the caller can emit one notification per status group
(a mixed batch must not tell every maker/checker the same status, and an
untouched "pending" row must never be announced as a decision -- see the
block-bid analogue in test_block_bid_notification_emit.py, which is where
the vendor's original bug -- coercing every non-"Approved" status into
"Rejected" -- was found and removed). This module is the shared, pure
version of that logic so all four market controllers can use one tested
implementation instead of copy-pasting it.
"""

from bson import ObjectId

from libs.notifications.single_bid_context import (
    group_single_bid_decisions,
    maker_usernames_for,
)


def _doc(_id, created_by, schedule_date="2026-08-09", block_no=1, bid_status="approved"):
    return {
        "_id": _id,
        "created_by": created_by,
        "schedule_date": schedule_date,
        "block_no": block_no,
        "bid_status": bid_status,
        "revision_no": 0,
    }


def test_groups_by_lowercased_status_with_correct_counts_and_schedule_date():
    id1, id2, id3 = ObjectId(), ObjectId(), ObjectId()
    updated = [
        {"reference_number": str(id1), "bid_status": "approved", "block_no": 1},
        {"reference_number": str(id2), "bid_status": "approved", "block_no": 2},
        {"reference_number": str(id3), "bid_status": "rejected", "block_no": 3},
    ]
    docs = [
        _doc(id1, "maker_a"),
        _doc(id2, "maker_b"),
        _doc(id3, "maker_a"),
    ]

    groups = group_single_bid_decisions(updated, docs)

    assert groups["approved"]["bid_count"] == 2
    assert groups["rejected"]["bid_count"] == 1
    assert groups["approved"]["schedule_date"] == "2026-08-09"


def test_maker_usernames_are_deduplicated_within_a_status_group_preserving_order():
    """Same maker submitted two of the three approved bids in the batch: their
    username must appear once in the group, in the order first seen, not once per
    bid (that would over-notify and would scramble a stable ordering for the UI)."""
    id1, id2, id3 = ObjectId(), ObjectId(), ObjectId()
    updated = [
        {"reference_number": str(id1), "bid_status": "approved", "block_no": 1},
        {"reference_number": str(id2), "bid_status": "approved", "block_no": 2},
        {"reference_number": str(id3), "bid_status": "approved", "block_no": 3},
    ]
    docs = [
        _doc(id1, "maker_a"),
        _doc(id2, "maker_b"),
        _doc(id3, "maker_a"),
    ]

    groups = group_single_bid_decisions(updated, docs)

    assert groups["approved"]["maker_usernames"] == ["maker_a", "maker_b"]


def test_a_reference_with_no_matching_document_contributes_nothing():
    """If the approval doc can't be found (deleted, wrong collection, whatever),
    that reference must silently drop out rather than creating a group with an
    empty schedule_date -- which would render as "a bid for no date"."""
    id1 = ObjectId()
    unmatched_reference = str(ObjectId())
    updated = [
        {"reference_number": str(id1), "bid_status": "approved", "block_no": 1},
        {"reference_number": unmatched_reference, "bid_status": "approved", "block_no": 2},
    ]
    docs = [_doc(id1, "maker_a")]

    groups = group_single_bid_decisions(updated, docs)

    assert groups["approved"]["bid_count"] == 1
    assert groups["approved"]["maker_usernames"] == ["maker_a"]


def test_non_decision_statuses_are_dropped_not_coerced_into_a_decision():
    """Regression guard for the vendor's own bug (see module docstring): a row
    left at "pending" -- because the checker only acted on some rows in the
    batch -- must never be relabelled as approved or rejected, and must not
    appear as a group of its own. Same for "pending_withdrawal" and "" (no
    status at all)."""
    id1, id2, id3, id4 = ObjectId(), ObjectId(), ObjectId(), ObjectId()
    updated = [
        {"reference_number": str(id1), "bid_status": "approved", "block_no": 1},
        {"reference_number": str(id2), "bid_status": "pending", "block_no": 2},
        {"reference_number": str(id3), "bid_status": "pending_withdrawal", "block_no": 3},
        {"reference_number": str(id4), "bid_status": "", "block_no": 4},
    ]
    docs = [
        _doc(id1, "maker_a"),
        _doc(id2, "maker_b"),
        _doc(id3, "maker_c"),
        _doc(id4, "maker_d"),
    ]

    groups = group_single_bid_decisions(updated, docs)

    assert set(groups.keys()) == {"approved"}
    assert groups["approved"]["bid_count"] == 1


def test_approved_withdrawal_is_its_own_recognised_decision_status():
    id1 = ObjectId()
    updated = [{"reference_number": str(id1), "bid_status": "approved_withdrawal", "block_no": 1}]
    docs = [_doc(id1, "maker_a")]

    groups = group_single_bid_decisions(updated, docs)

    assert groups["approved_withdrawal"]["bid_count"] == 1
    assert groups["approved_withdrawal"]["maker_usernames"] == ["maker_a"]


def test_status_matching_is_case_insensitive_and_keys_come_out_lowercased():
    id1 = ObjectId()
    updated = [{"reference_number": str(id1), "bid_status": "Approved", "block_no": 1}]
    docs = [_doc(id1, "maker_a")]

    groups = group_single_bid_decisions(updated, docs)

    assert "Approved" not in groups
    assert groups["approved"]["bid_count"] == 1


def test_schedule_date_is_the_first_non_empty_value_seen_stringified():
    """One doc in the group has no schedule_date; the group must still surface a
    real date instead of picking the empty one just because it came from the
    doc matching the first `updated` entry, and the date must come back as a
    plain string even when the doc field wasn't stored as one."""
    id1, id2 = ObjectId(), ObjectId()
    updated = [
        {"reference_number": str(id1), "bid_status": "approved", "block_no": 1},
        {"reference_number": str(id2), "bid_status": "approved", "block_no": 2},
    ]
    docs = [
        _doc(id1, "maker_a", schedule_date=""),
        _doc(id2, "maker_b", schedule_date="2026-08-10"),
    ]

    groups = group_single_bid_decisions(updated, docs)

    assert groups["approved"]["schedule_date"] == "2026-08-10"


def test_empty_input_returns_an_empty_dict():
    assert group_single_bid_decisions([], []) == {}


def test_maker_usernames_for_dedups_created_by_preserving_first_seen_order():
    updated = [
        {"reference_number": "a", "created_by": "maker_a"},
        {"reference_number": "b", "created_by": "maker_b"},
        {"reference_number": "c", "created_by": "maker_a"},
    ]

    assert maker_usernames_for(updated) == ["maker_a", "maker_b"]


def test_maker_usernames_for_empty_input_returns_empty_list():
    assert maker_usernames_for([]) == []
