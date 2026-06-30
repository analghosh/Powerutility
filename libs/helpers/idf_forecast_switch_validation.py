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
    primary_scheduled_date_for_rtm,
    rtm_forecast_run_context,
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
    Manual switch rules for the **primary** delivery date only (aligned with UI
    manual-status for today before 22:30 IST, or tomorrow from 22:30 IST).

    Tomorrow is not validated here during the noon–22:29 window; merge may still
    apply tomorrow CSV later when present, or skip missing sessions.

    - Active session on the primary date: both time blocks must have valid
      non-zero CSV in forecast_serve. If either block is zero/invalid but the
      other has valid CSV → reject (active-session message). If neither block
      has any valid CSV → reject (no manual data message).
    """
    _ = include_tomorrow_forecast
    target_date = primary_scheduled_date_for_rtm(today_ist, after_evening_cutover)
    active_sid = effective_active_session_for_delivery_date(
        target_date,
        today_ist,
        now_local,
        after_evening_cutover,
    )
    csv_by_block = index_forecast_docs(csv_docs_by_date.get(target_date, []))

    if session_has_complete_csv(active_sid, csv_by_block):
        return True, ""

    if not active_session_has_any_valid_csv(active_sid, csv_by_block):
        return False, MANUAL_SWITCH_BLOCKED_MESSAGE

    return False, MANUAL_SWITCH_ACTIVE_SESSION_INVALID_MESSAGE


def scheduled_at_midnight(d: date) -> datetime:
    return datetime(d.year, d.month, d.day)


def build_manual_switch_validation_context(
    tz_name: Optional[str] = None,
    min_ahead_blocks: Optional[int] = None,
) -> Dict[str, Any]:
    """IST context used when validating a switch to Manual mode."""
    return rtm_forecast_run_context(tz_name, min_ahead_blocks)
