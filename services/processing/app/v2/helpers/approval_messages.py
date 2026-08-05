"""Success wording for the day-ahead bid approve/reject endpoints.

DAM, GDAM and HPDAM used to answer "All bids updated successfully" no matter
what happened — approving a single time block from the Select Action dropdown
said the same thing as approving all 96. These helpers turn the statuses that
were actually written into the sentence the approver sees.

Shared by the three day-ahead services because /dam/dam-bid-approval splits one
Confirm across all three endpoints; they have to agree on the wording.

Not wired into RTM or block bid, which carry the same original string. Block
bid would also need a different field name — it stores "status": "Approved"
rather than "bid_status": "approved".
"""

_APPROVED = "approved"
_REJECTED = "rejected"


def action_verb(records):
    """Past-tense verb describing what was done to `records`.

    Returns "approved", "rejected", "approved/rejected" when the batch is
    mixed, or "updated" when the statuses are something else entirely.
    """
    statuses = {
        str(record.get("bid_status", "")).strip().lower()
        for record in records or []
    }
    statuses.discard("")

    if not statuses or not statuses <= {_APPROVED, _REJECTED}:
        return "updated"
    if statuses == {_APPROVED}:
        return _APPROVED
    if statuses == {_REJECTED}:
        return _REJECTED
    return f"{_APPROVED}/{_REJECTED}"


def success_message(updated):
    """Message for the all-succeeded path.

    Always leads with the count. "All" is deliberately absent: the payload only
    carries the blocks the approver acted on, so "All" would claim the whole day
    was handled even when two of ninety-six were.
    """
    count = len(updated or [])
    verb = action_verb(updated)
    if count == 0:
        # An empty payload used to report "All bids updated successfully".
        return "No bids to update"
    if count == 1:
        return f"1 bid {verb} successfully"
    return f"{count} bids {verb} successfully"


def partial_message(updated, skipped_count):
    """Message for the some-succeeded, some-already-processed path."""
    return (
        f"Partial update: {len(updated or [])} "
        f"{action_verb(updated)}, {skipped_count} skipped"
    )
