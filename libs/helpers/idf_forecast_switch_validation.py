"""Validation helpers for IDF forecast Manual/Auto switch."""

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from libs.helpers.idf_forecast_session import (
    index_forecast_docs,
    parse_csv_forecast_mw,
    session_has_complete_csv,
    session_to_time_blocks,
)
from libs.helpers.rtm_forecast_block_filter import (
    effective_active_session_for_delivery_date,
    rtm_forecast_run_context,
    target_scheduled_dates_for_rtm,
)
from libs.constants.idf_forecast import MANUAL_AUTO_FLIP_ACTIVE_SESSION_CSV_MESSAGE
from libs.models.forecast_serve_entity import FORECAST_SOURCE_CSV

MANUAL_SWITCH_BLOCKED_MESSAGE = (
    "No data exist in manual mode so can't be able to Shift to Manual."
)

MANUAL_SWITCH_ACTIVE_SESSION_INVALID_MESSAGE = (
    "Can't be able to shift to Manual mode as zero / invalid data exist in active session."
)


def active_session_csv_requires_auto_flip(
    active_session_id: int,
    csv_by_block: Dict[int, Dict[str, Any]],
) -> Tuple[bool, str]:
    """
    True when Manual mode should fall back to Auto because the active session
    lacks a full valid CSV pair (missing or zero in either time block).
    """
    if session_has_complete_csv(active_session_id, csv_by_block):
        return False, ""
    return True, MANUAL_AUTO_FLIP_ACTIVE_SESSION_CSV_MESSAGE


def active_session_has_any_valid_csv(
    active_session_id: int,
    csv_by_block: Dict[int, Dict[str, Any]],
) -> bool:
    """True if at least one block in the active session has non-zero CSV forecast."""
    b1, b2 = session_to_time_blocks(active_session_id)
    for block in (b1, b2):
        if parse_csv_forecast_mw((csv_by_block.get(block) or {}).get("forecast")) is not None:
            return True
    return False


def validate_csv_coverage_for_manual_switch(
    csv_docs_by_date: Dict[date, List[Dict[str, Any]]],
    today_ist: date,
    now_local: datetime,
    after_evening_cutover: bool,
    include_tomorrow_forecast: bool,
) -> Tuple[bool, str]:
    """
    Manual switch rules (per target delivery date):

    - Today (always when in scope): active session must have valid non-zero CSV in
      forecast_serve for both time blocks.

    - Tomorrow (noon–22:29 only): validated only when CSV has been ingested for
      that delivery day. If no tomorrow CSV upload exists yet, tomorrow is skipped
      so switching to Manual is not blocked while today has valid data.

    - After 22:30 IST (tomorrow-only window): tomorrow CSV is required.

    - Future sessions (active+1 through 48) within a day: not validated.
    """
    target_dates = target_scheduled_dates_for_rtm(
        today_ist,
        after_evening_cutover,
        include_tomorrow_forecast,
    )

    for target_date in target_dates:
        csv_docs = csv_docs_by_date.get(target_date, [])
        if not csv_docs:
            if target_date == today_ist or after_evening_cutover:
                return False, MANUAL_SWITCH_BLOCKED_MESSAGE
            # Tomorrow not uploaded yet during noon–22:29 window.
            continue

        active_sid = effective_active_session_for_delivery_date(
            target_date,
            today_ist,
            now_local,
            after_evening_cutover,
        )
        csv_by_block = index_forecast_docs(csv_docs)

        if session_has_complete_csv(active_sid, csv_by_block):
            continue

        if not active_session_has_any_valid_csv(active_sid, csv_by_block):
            return False, MANUAL_SWITCH_BLOCKED_MESSAGE

        return False, MANUAL_SWITCH_ACTIVE_SESSION_INVALID_MESSAGE

    return True, ""


def scheduled_at_midnight(d: date) -> datetime:
    return datetime(d.year, d.month, d.day)


def build_manual_switch_validation_context(
    tz_name: Optional[str] = None,
    min_ahead_blocks: Optional[int] = None,
) -> Dict[str, Any]:
    """IST context used when validating a switch to Manual mode."""
    return rtm_forecast_run_context(tz_name, min_ahead_blocks)
