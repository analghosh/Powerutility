"""Resolving the delivery date a day-ahead block bid is submitted against.

The DAM/GDAM block bid submit path read `delivery_dates_resp["DeliveryDate"]`
straight off the root of IEX's /deliverydates reply. IEX also answers that
endpoint in the RTM shape, `{"DeliveryDates": [ ... ]}`, and when it did the
read produced None and the whole submission failed with

    DeliveryDate not found from DAM exchange

while the bid date on screen was perfectly valid. The vendor's own scheduler
client documents the ambiguity in `get_valid_delivery_date_dam`, which tries
the root key first and then falls back to the list — the submit path never did.

Falling back to `DeliveryDates[0]` is not enough on its own: when the exchange
offers more than one date, the first is not necessarily the bid's date, and
picking it would submit a day's bids against the wrong delivery day.
"""
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.iex.masterApi import (  # noqa: E402
    delivery_date_candidates,
    describe_delivery_dates,
    resolve_delivery_date,
)


def ts(day: str) -> int:
    """Midnight UTC of a YYYY-MM-DD day, the form IEX uses for DeliveryDate."""
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


TOMORROW = date(2026, 8, 22)


# --------------------------------------------------------------- shapes ---

def test_root_object_shape_is_accepted():
    resp = {"DeliveryDateId": "T+1", "DeliveryDate": ts("2026-08-22")}

    resolved = resolve_delivery_date(resp, TOMORROW)

    assert resolved.timestamp == ts("2026-08-22")
    assert resolved.matched is True


def test_list_shape_is_accepted():
    """The shape that used to produce "DeliveryDate not found"."""
    resp = {"DeliveryDates": [{"DeliveryDateId": "T+1", "DeliveryDate": ts("2026-08-22")}]}

    resolved = resolve_delivery_date(resp, TOMORROW)

    assert resolved.timestamp == ts("2026-08-22")
    assert resolved.matched is True


def test_bare_list_response_is_accepted():
    resp = [{"DeliveryDateId": "T+1", "DeliveryDate": ts("2026-08-22")}]

    assert resolve_delivery_date(resp, TOMORROW).timestamp == ts("2026-08-22")


def test_single_nested_object_is_accepted():
    resp = {"DeliveryDates": {"DeliveryDateId": "T+1", "DeliveryDate": ts("2026-08-22")}}

    assert resolve_delivery_date(resp, TOMORROW).timestamp == ts("2026-08-22")


# ------------------------------------------------------------ selection ---

def test_the_record_matching_the_bid_date_is_chosen_not_the_first():
    resp = {
        "DeliveryDates": [
            {"DeliveryDateId": "T", "DeliveryDate": ts("2026-08-21")},
            {"DeliveryDateId": "T+1", "DeliveryDate": ts("2026-08-22")},
        ]
    }

    resolved = resolve_delivery_date(resp, TOMORROW)

    assert resolved.timestamp == ts("2026-08-22")
    assert resolved.record["DeliveryDateId"] == "T+1"
    assert resolved.matched is True


def test_several_dates_with_none_matching_resolves_to_nothing():
    """Guessing here would put a day's bids on the wrong delivery day."""
    resp = {
        "DeliveryDates": [
            {"DeliveryDateId": "T", "DeliveryDate": ts("2026-08-21")},
            {"DeliveryDateId": "T+1", "DeliveryDate": ts("2026-08-22")},
        ]
    }

    resolved = resolve_delivery_date(resp, date(2026, 8, 25))

    assert resolved.timestamp is None
    assert resolved.matched is False
    assert len(resolved.candidates) == 2


def test_a_lone_non_matching_date_is_used_but_flagged_unmatched():
    """One offered date is the day-ahead norm; the caller warns rather than refuses."""
    resp = {"DeliveryDateId": "T+1", "DeliveryDate": ts("2026-08-23")}

    resolved = resolve_delivery_date(resp, TOMORROW)

    assert resolved.timestamp == ts("2026-08-23")
    assert resolved.matched is False


def test_timestamp_comes_back_as_an_int_even_when_sent_as_a_string():
    resp = {"DeliveryDateId": "T+1", "DeliveryDate": str(ts("2026-08-22"))}

    resolved = resolve_delivery_date(resp, TOMORROW)

    assert resolved.timestamp == ts("2026-08-22")
    assert isinstance(resolved.timestamp, int)


# --------------------------------------------------------------- empty ----

def test_an_empty_response_resolves_to_nothing():
    for resp in ({}, {"DeliveryDates": []}, [], None, "Unauthorized"):
        resolved = resolve_delivery_date(resp, TOMORROW)
        assert resolved.timestamp is None, resp
        assert resolved.candidates == [], resp


def test_a_null_delivery_date_is_not_treated_as_a_candidate():
    assert delivery_date_candidates({"DeliveryDateId": "T+1", "DeliveryDate": None}) == []


def test_an_unparseable_lone_timestamp_resolves_to_nothing():
    resolved = resolve_delivery_date({"DeliveryDateId": "T+1", "DeliveryDate": "soon"}, TOMORROW)

    assert resolved.timestamp is None


# ------------------------------------------------------------ reporting ---

def test_the_error_text_names_the_dates_the_exchange_offered():
    resp = {
        "DeliveryDates": [
            {"DeliveryDateId": "T", "DeliveryDate": ts("2026-08-21")},
            {"DeliveryDateId": "T+1", "DeliveryDate": ts("2026-08-22")},
        ]
    }

    described = describe_delivery_dates(resp)

    assert "T=2026-08-21" in described
    assert "T+1=2026-08-22" in described


def test_the_error_text_names_the_response_keys_when_there_are_no_dates():
    described = describe_delivery_dates({"Status": "Failed", "StatusMessage": "Unauthorized"})

    assert "Status" in described
    assert "StatusMessage" in described


def test_the_error_text_survives_a_non_dict_response():
    assert "str" in describe_delivery_dates("<html>403 Forbidden</html>")


# ------------------------------------------------ the 2026-08-21 refusal ---
# On 2026-08-21 IEX served delivery date 1787270400 (2026-08-21) — the identical
# timestamp it had served on 2026-08-20, when it was correctly D+1. Six DAM block
# bid submissions dated 2026-08-22 were posted against it and IEX refused every
# one with "Invalid Delivery Date.". Every submission that has ever succeeded on
# this path sent the bid's own date:
#
#   2026-08-17 16:40  sent 2026-08-18 (D+1)  Success
#   2026-08-19 09:10  sent 2026-08-20 (D+1)  Success
#   2026-08-20 11:22  sent 2026-08-21 (D+1)  Success
#   2026-08-21 10:59  sent 2026-08-21 (D+0)  Failed - Invalid Delivery Date.
#
# resolve_delivery_date must report that mismatch so the caller can refuse; the
# caller must not post the pair.

STUCK_DELIVERY_DATE = 1787270400  # 2026-08-21 00:00 UTC


def test_the_stuck_delivery_date_is_reported_as_a_mismatch():
    resp = {"DeliveryDateId": "T+1", "DeliveryDate": STUCK_DELIVERY_DATE}

    resolved = resolve_delivery_date(resp, date(2026, 8, 22))

    assert resolved.matched is False, "a D+0 date against D+1 bids must not read as a match"
    assert resolved.timestamp == STUCK_DELIVERY_DATE


def test_the_same_response_matched_the_day_before():
    """The value was correct on 2026-08-20; only the calendar moved."""
    resp = {"DeliveryDateId": "T+1", "DeliveryDate": STUCK_DELIVERY_DATE}

    assert resolve_delivery_date(resp, date(2026, 8, 21)).matched is True


def test_every_historical_success_still_matches():
    """None of the submissions that worked would be refused by the strict check."""
    for sent, bid_date in [
        (1787011200, date(2026, 8, 18)),
        (1787097600, date(2026, 8, 19)),
        (1787184000, date(2026, 8, 20)),
        (1787270400, date(2026, 8, 21)),
    ]:
        resolved = resolve_delivery_date({"DeliveryDateId": "T+1", "DeliveryDate": sent}, bid_date)
        assert resolved.matched is True, f"{sent} vs {bid_date} should match"
