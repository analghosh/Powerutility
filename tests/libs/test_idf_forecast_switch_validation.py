"""Tests for Manual switch CSV validation."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from libs.helpers.idf_forecast_session import session_ids_from_block_through_96, session_to_time_blocks
from libs.constants.idf_forecast import MANUAL_AUTO_FLIP_ACTIVE_SESSION_CSV_MESSAGE
from libs.helpers.idf_forecast_session import (
    session_has_csv_forecast_present,
    session_ids_from_active_session_through_96,
)
from libs.helpers.idf_forecast_switch_validation import (
    MANUAL_SWITCH_ACTIVE_SESSION_INVALID_MESSAGE,
    MANUAL_SWITCH_BLOCKED_MESSAGE,
    active_session_csv_requires_auto_flip,
    validate_csv_coverage_for_manual_switch,
)


def test_session_ids_from_active_session_ist_plus_offset():
    """Block 80 (~19:45 IST): current session 40, RTM active session 43."""
    now = datetime(2026, 5, 25, 19, 52, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert session_ids_from_active_session_through_96(now) == list(range(43, 49))


def test_session_has_csv_forecast_present():
    csv_by_block = {9: {"forecast": 0}, 10: {"forecast": 100}}
    assert session_has_csv_forecast_present(5, csv_by_block) is True
    assert session_has_csv_forecast_present(6, csv_by_block) is False


def test_active_session_csv_requires_auto_flip_when_incomplete():
    csv_by_block = {9: {"forecast": 100.0}, 10: {"forecast": 0}}
    should_flip, msg = active_session_csv_requires_auto_flip(5, csv_by_block)
    assert should_flip is True
    assert msg == MANUAL_AUTO_FLIP_ACTIVE_SESSION_CSV_MESSAGE


def test_active_session_csv_requires_auto_flip_false_when_complete():
    csv_by_block = {9: {"forecast": 100.0}, 10: {"forecast": 101.0}}
    should_flip, msg = active_session_csv_requires_auto_flip(5, csv_by_block)
    assert should_flip is False
    assert msg == ""


def test_validate_csv_coverage_all_sessions_ok():
    today = date(2026, 5, 15)
    csv_docs = []
    for session_id in session_ids_from_block_through_96(9):
        b1, b2 = session_to_time_blocks(session_id)
        csv_docs.append({"time_block": b1, "forecast": 100.0})
        csv_docs.append({"time_block": b2, "forecast": 101.0})
    ok, msg = validate_csv_coverage_for_manual_switch(
        {today: csv_docs},
        today,
        active_rtm_session_id=5,
        after_evening_cutover=False,
        include_tomorrow_forecast=False,
    )
    assert ok is True
    assert msg == ""


def test_validate_csv_coverage_active_only_future_all_zero_allows_manual():
    """Active session valid; all later sessions missing or zero is allowed."""
    today = date(2026, 5, 15)
    csv_docs = [
        {"time_block": 9, "forecast": 100.0},
        {"time_block": 10, "forecast": 101.0},
    ]
    ok, msg = validate_csv_coverage_for_manual_switch(
        {today: csv_docs},
        today,
        active_rtm_session_id=5,
        after_evening_cutover=False,
        include_tomorrow_forecast=False,
    )
    assert ok is True
    assert msg == ""


def test_validate_csv_coverage_partial_csv_allows_manual():
    """Active session valid; other sessions may be missing or zero."""
    today = date(2026, 5, 15)
    csv_docs = [
        {"time_block": 9, "forecast": 100.0},
        {"time_block": 10, "forecast": 101.0},
        {"time_block": 20, "forecast": 0},
    ]
    ok, msg = validate_csv_coverage_for_manual_switch(
        {today: csv_docs},
        today,
        active_rtm_session_id=5,
        after_evening_cutover=False,
        include_tomorrow_forecast=False,
    )
    assert ok is True
    assert msg == ""


def test_validate_csv_coverage_all_sessions_missing_blocks_manual():
    today = date(2026, 5, 15)
    ok, msg = validate_csv_coverage_for_manual_switch(
        {today: []},
        today,
        active_rtm_session_id=5,
        after_evening_cutover=False,
        include_tomorrow_forecast=False,
    )
    assert ok is False
    assert msg == MANUAL_SWITCH_BLOCKED_MESSAGE


def test_validate_csv_coverage_active_session_zero_blocks_manual():
    today = date(2026, 5, 15)
    csv_docs = [
        {"time_block": 9, "forecast": 100.0},
        {"time_block": 10, "forecast": 0},
    ]
    ok, msg = validate_csv_coverage_for_manual_switch(
        {today: csv_docs},
        today,
        active_rtm_session_id=5,
        after_evening_cutover=False,
        include_tomorrow_forecast=False,
    )
    assert ok is False
    assert msg == MANUAL_SWITCH_ACTIVE_SESSION_INVALID_MESSAGE


def test_validate_csv_coverage_active_invalid_future_valid_still_blocks():
    """Future session CSV does not bypass active-session zero/invalid rule."""
    today = date(2026, 5, 15)
    csv_docs = [
        {"time_block": 9, "forecast": 0},
        {"time_block": 10, "forecast": 101.0},
        {"time_block": 11, "forecast": 200.0},
        {"time_block": 12, "forecast": 201.0},
    ]
    ok, msg = validate_csv_coverage_for_manual_switch(
        {today: csv_docs},
        today,
        active_rtm_session_id=5,
        after_evening_cutover=False,
        include_tomorrow_forecast=False,
    )
    assert ok is False
    assert msg == MANUAL_SWITCH_ACTIVE_SESSION_INVALID_MESSAGE


def test_validate_csv_coverage_only_zero_csv_blocks_manual():
    today = date(2026, 5, 15)
    csv_docs = [
        {"time_block": 9, "forecast": 0},
        {"time_block": 10, "forecast": 0},
    ]
    ok, msg = validate_csv_coverage_for_manual_switch(
        {today: csv_docs},
        today,
        active_rtm_session_id=5,
        after_evening_cutover=False,
        include_tomorrow_forecast=False,
    )
    assert ok is False
    assert msg == MANUAL_SWITCH_BLOCKED_MESSAGE
