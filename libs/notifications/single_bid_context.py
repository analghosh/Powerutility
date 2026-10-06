"""Pure helpers for building single-bid decision notification context.

`/submit-pre-exchange-data` (new_dam_bidding_services.py:3408-3450) already
narrows its result to an `updated` list of the references it actually
changed (`modified_count > 0`). Each entry looks like:

    {"reference_number": "<ObjectId str>", "bid_status": "approved",
     "Remarks": "...", "action_by": "...", "block_no": 3}

That list alone has no maker username or schedule date -- those live on the
approval documents in the per-market Mongo collections
(`dam_single_bid_data_for_approval`, `rtm_single_bid_data_for_approval`, ...),
keyed by `_id`. There is no `market_type` field on those documents; market is
known from the call site, not from anything this module touches.

group_single_bid_decisions() joins `updated` to those documents and buckets
the result by lowercased status, so a batch that approves some bids and
rejects others (the checker screen submits every visible row, touched or
not) produces one notification per status group instead of one notification
per row, and instead of mislabelling the whole batch with a single status.

This module is intentionally free of Mongo, I/O, and the event loop -- it is
meant to be the one tested implementation all four market controllers
(DAM/GDAM/HPDAM/RTM single-bid approval) share, rather than four copies of
the same join-and-group logic.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

# Statuses that represent an actual checker decision. Anything else --
# "pending" (untouched row resubmitted by the screen), "pending_withdrawal"
# (withdrawal requested but not yet decided), or "" (no status at all) -- is
# dropped, never coerced. The vendor's own line
# (`status_label = "Approved" if status == "Approved" else "Rejected"`)
# coerced every non-"Approved" status into "Rejected", which relabelled an
# untouched Pending row as a rejection. See test_block_bid_notification_emit.py
# for where that bug was found and removed on the block-bid path; this module
# exists so the single-bid path never repeats it.
DECISION_STATUSES = frozenset({"approved", "rejected", "approved_withdrawal"})


def group_single_bid_decisions(
    updated: Iterable[Dict[str, Any]],
    docs: Iterable[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Join `updated` references to their approval documents and group by status.

    Returns a dict keyed by lowercased status. Each value is:
        {"bid_count": int, "maker_usernames": list[str], "schedule_date": str}

    A reference number with no matching document contributes nothing -- not
    even an entry with an empty schedule_date, which would render downstream
    as "a bid for no date". A status outside DECISION_STATUSES is dropped
    entirely, so an untouched row never becomes a phantom notification group.
    """
    # Index documents by their string _id once, so the join below is O(1) per
    # reference instead of an O(n) scan per reference.
    docs_by_id = {str(doc["_id"]): doc for doc in docs}

    groups: Dict[str, Dict[str, Any]] = {}

    for entry in updated:
        status = str(entry.get("bid_status", "")).strip().lower()
        if status not in DECISION_STATUSES:
            # Not a decision (pending / pending_withdrawal / empty) -- drop it
            # rather than coercing it into approved/rejected.
            continue

        doc = docs_by_id.get(str(entry.get("reference_number", "")))
        if doc is None:
            # No matching approval document -- nothing to attribute a maker or
            # schedule date to, so this reference contributes nothing.
            continue

        group = groups.setdefault(
            status,
            {"bid_count": 0, "maker_usernames": [], "schedule_date": ""},
        )
        group["bid_count"] += 1

        maker_username = doc.get("created_by")
        if maker_username and maker_username not in group["maker_usernames"]:
            # Dedup, preserving first-seen order -- the same maker can own
            # several bids in one status group and must be notified once.
            group["maker_usernames"].append(maker_username)

        if not group["schedule_date"]:
            schedule_date = doc.get("schedule_date")
            if schedule_date:
                # First non-empty schedule_date wins; stringified because the
                # stored value is not guaranteed to already be a str.
                group["schedule_date"] = str(schedule_date)

    return groups


def maker_usernames_for(updated: Iterable[Dict[str, Any]]) -> List[str]:
    """Deduplicated `created_by` values from `updated`, order preserved.

    Used by the submit-for-approval path, which has no status dimension of
    its own (every entry there is a fresh submission, not a decision) but
    still needs a maker list built the same way.
    """
    seen: List[str] = []
    for entry in updated:
        username = entry.get("created_by")
        if username and username not in seen:
            seen.append(username)
    return seen
