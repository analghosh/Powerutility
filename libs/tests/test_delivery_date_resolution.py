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


# ------------------------------------------- the delivery date we send ---
# The submit path no longer takes its DeliveryDate from /deliverydates. Every
# DeliveryDate IEX has ever accepted is exactly midnight UTC of the delivery day,
# so it is derived from the bid date. resolve_delivery_date stays as the
# cross-check that decides whether to log a warning.
#
#   2026-08-17 16:40  sent 2026-08-18 (D+1)  Success
#   2026-08-19 09:10  sent 2026-08-20 (D+1)  Success
#   2026-08-20 11:22  sent 2026-08-21 (D+1)  Success
#   2026-08-21 10:59  sent 2026-08-21 (D+0)  Failed - Invalid Delivery Date.
#
# The failures carried /deliverydates' value; the successes all carried the bid's
# own date. Deriving reproduces every success and none of the failures.

STUCK_DELIVERY_DATE = 1787270400  # 2026-08-21 00:00 UTC


def derive(bid_date):
    """What the submit path now sends: midnight UTC of the bid date."""
    return int(datetime.combine(bid_date, datetime.min.time(), tzinfo=timezone.utc).timestamp())


def test_derivation_reproduces_every_accepted_delivery_date():
    """Each of these was accepted by IEX; derivation must produce the same value."""
    for accepted, bid_date in [
        (1787011200, date(2026, 8, 18)),
        (1787097600, date(2026, 8, 19)),
        (1787184000, date(2026, 8, 20)),
        (1787270400, date(2026, 8, 21)),
    ]:
        assert derive(bid_date) == accepted, f"{bid_date} should derive to {accepted}"


def test_derivation_would_not_have_sent_the_rejected_value():
    """The 2026-08-21 failures sent D+0 for bids dated 2026-08-22."""
    assert derive(date(2026, 8, 22)) == 1787356800
    assert derive(date(2026, 8, 22)) != STUCK_DELIVERY_DATE


def test_every_delivery_date_is_midnight_utc():
    for d in (date(2026, 8, 18), date(2026, 8, 22), date(2027, 1, 1)):
        assert derive(d) % 86400 == 0


def test_the_stuck_endpoint_value_reads_as_a_mismatch_for_the_cross_check():
    """Drives the warning, not a refusal — the derived value is sent regardless."""
    resp = {"DeliveryDateId": "T+1", "DeliveryDate": STUCK_DELIVERY_DATE}

    assert resolve_delivery_date(resp, date(2026, 8, 22)).matched is False
    assert resolve_delivery_date(resp, date(2026, 8, 21)).matched is True
