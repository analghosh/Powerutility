"""What "Submit to Exchange" tells the approving user is the POST result, nothing else.

The approval screen used to report the order status IEX assigned afterwards:
`submit_dam_to_exchange` read `dam_bid_details[-1].OrderStatus` from a follow-up
get_bid_details call and returned "DAM Bid Rejected in exchange!", and both submit
paths wrote `status: "exchange_rejected"` back onto `block_bid_current_data`. A bid
that was submitted successfully and then rejected by the exchange therefore read on
the approval screen as though the submission had not happened.

The exchange's verdict now belongs to the Bid Status screen's Block Bid Status column
(`exchangeStatus`), and the approval screen answers only "did it reach the exchange".
"""
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.services import block_bid_services  # noqa: E402
from services.processing.app.v2.services.block_bid_services import (  # noqa: E402
    exchange_refusal_reason,
)


def test_the_exchange_reason_is_repeated_verbatim():
    reason = exchange_refusal_reason(
        {"Status": "Failure", "StatusMessage": "Bid Quota Exceeded for the Portfolio"}
    )
    assert reason == "Bid Quota Exceeded for the Portfolio"


def test_status_is_used_when_the_exchange_sends_no_message():
    assert exchange_refusal_reason({"Status": "Failure"}) == "Failure"


def test_an_empty_message_does_not_shadow_the_status():
    assert exchange_refusal_reason({"Status": "Failure", "StatusMessage": ""}) == "Failure"


@pytest.mark.parametrize("malformed", [None, "", [], "Failure", 0])
def test_a_reply_that_is_not_an_object_still_yields_a_sentence(malformed):
    assert exchange_refusal_reason(malformed) == "submission refused"


def test_a_reply_with_neither_field_still_yields_a_sentence():
    assert exchange_refusal_reason({"TransactionId": None}) == "submission refused"


# --- The regression this function must not reintroduce -------------------------
#
# Day-ahead only. `submit_to_exchange` (RTM) carries the same defect and is left
# alone on purpose — the client scoped this work to iDAM/GDAM/HPDAM. See the
# test below that pins that decision.

DAM_SUBMIT_SOURCE = inspect.getsource(block_bid_services.BlockBidServices.submit_dam_to_exchange)
RTM_SUBMIT_SOURCE = inspect.getsource(block_bid_services.BlockBidServices.submit_to_exchange)


def test_the_day_ahead_path_writes_no_exchange_verdict_into_status():
    """`status` records what EPMS did; `exchangeStatus` records what IEX did."""
    assert "exchange_rejected" not in DAM_SUBMIT_SOURCE


def test_the_day_ahead_path_still_records_the_exchange_verdict_separately():
    """Dropping the mirror must not drop the field the Bid Status column reads."""
    assert "exchangeStatus" in DAM_SUBMIT_SOURCE


def test_the_day_ahead_reply_no_longer_quotes_the_order_status():
    """"DAM Bid Rejected in exchange!" came from get_bid_details, not from the POST."""
    assert "final_status" not in DAM_SUBMIT_SOURCE
    assert "in exchange!" not in DAM_SUBMIT_SOURCE


def test_the_day_ahead_reply_counts_what_was_posted():
    """A failed get_bid_details used to make a successful submission report zero bids."""
    assert '"bids_submitted": submitted_count' in DAM_SUBMIT_SOURCE


def test_rtm_is_deliberately_left_with_the_defect():
    """
    Scope decision, 2026-08-20: this change is iDAM/GDAM/HPDAM only. RTM keeps
    mirroring an exchange rejection into `status`. Delete this test — do not
    weaken it — if RTM is ever brought into scope.
    """
    assert "exchange_rejected" in RTM_SUBMIT_SOURCE
