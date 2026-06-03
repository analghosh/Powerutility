"""
RTM session helpers for IDF forecast merge (2 time blocks per 30-minute session).
Aligned with rtm_transaction_service / rtm_bidding block indexing.

IST merge session ranges (see merge_session_ids_for_delivery_date):
- 00:00-11:59: today only, active session -> 48
- 12:00-22:29: today active -> 48; tomorrow sessions 1 -> 48 (blocks 1-96)
- >= 22:30: tomorrow only, active session -> 48
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from libs.helpers.rtm_forecast_block_filter import (
    rtm_active_session_id,
    should_write_forecast_block,
)

SessionMergeOp = Tuple[int, float, float, str]


def time_block_to_session_id(time_block: int) -> int:
    block = max(1, min(96, int(time_block)))
    return (block - 1) // 2 + 1


def session_to_time_blocks(session_id: int) -> Tuple[int, int]:
    sid = max(1, min(48, int(session_id)))
    return 2 * sid - 1, 2 * sid


def first_writable_block(now_local: datetime, min_ahead_blocks: int = 0) -> int:
    """First time block of the RTM active session (IST session + offset)."""
    _ = min_ahead_blocks
    return session_to_time_blocks(rtm_active_session_id(now_local))[0]


def active_session_id(now_local: datetime) -> int:
    """RTM active session (IST current session + 3), not the in-block session."""
    return rtm_active_session_id(now_local)


def blocks_from_first_through_96(first_block: int) -> List[int]:
    return list(range(max(1, min(96, int(first_block))), 97))


def session_ids_from_block_through_96(first_block: int) -> List[int]:
    return list(range(time_block_to_session_id(first_block), 49))


def session_ids_from_active_session_through_96(now_local: datetime) -> List[int]:
    """Session IDs from RTM active session (IST session + offset) through session 48."""
    return list(range(active_session_id(now_local), 49))


def merge_session_ids_for_delivery_date(
    scheduled_date: date,
    today_ist: date,
    after_evening_cutover: bool,
    now_local: datetime,
) -> List[int]:
    """
    Session IDs to iterate when merging forecast into demand_serve for one delivery date.

    - Past dates: none
    - Today: active RTM session through 48
    - Tomorrow before 22:30 IST: sessions 1 through 48 (blocks 1-96)
    - Tomorrow from 22:30 IST: active session through 48
    """
    if scheduled_date < today_ist:
        return []
    if scheduled_date == today_ist:
        return session_ids_from_active_session_through_96(now_local)
    if scheduled_date > today_ist:
        if after_evening_cutover:
            return session_ids_from_active_session_through_96(now_local)
        return session_ids_from_block_through_96(1)
    return []


def session_has_csv_forecast_present(
    session_id: int,
    csv_by_block: Dict[int, Dict[str, Any]],
) -> bool:
    """True if forecast_serve has a CSV row for at least one block in the session pair."""
    b1, b2 = session_to_time_blocks(session_id)
    return b1 in csv_by_block or b2 in csv_by_block


def writable_blocks_for_date(
    scheduled_date: date,
    today_ist: date,
    current_block: int,
    after_evening_cutover: bool,
    min_ahead_blocks: int,
    active_rtm_session_id: int,
    now_local: Optional[datetime] = None,
) -> List[int]:
    return [
        b
        for b in range(1, 97)
        if should_write_forecast_block(
            scheduled_date,
            b,
            today_ist,
            current_block,
            after_evening_cutover=after_evening_cutover,
            min_ahead_blocks=min_ahead_blocks,
            active_rtm_session_id=active_rtm_session_id,
            now_local=now_local,
        )
    ]


def index_forecast_docs(docs: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    for doc in docs:
        tb = doc.get("time_block")
        if tb is not None:
            out[int(tb)] = doc
    return out


def demand_forecast_invalid(forecast: Any) -> bool:
    if forecast is None:
        return True
    try:
        return float(forecast) == 0.0
    except (TypeError, ValueError):
        return True


def parse_csv_forecast_mw(forecast: Any) -> Optional[float]:
    if demand_forecast_invalid(forecast):
        return None
    return float(forecast)


def block_has_resolvable_forecast(
    time_block: int,
    csv_by_block: Dict[int, Dict[str, Any]],
    api_by_block: Dict[int, Dict[str, Any]],
) -> bool:
    csv_doc = csv_by_block.get(time_block)
    if csv_doc is not None and parse_csv_forecast_mw(csv_doc.get("forecast")) is not None:
        return True
    return time_block in api_by_block


def resolve_session_merge_ops(
    session_id: int,
    csv_by_block: Dict[int, Dict[str, Any]],
    api_by_block: Dict[int, Dict[str, Any]],
    demand_by_block: Dict[int, Dict[str, Any]],
    csv_source_label: str,
    api_source_label: str,
) -> Optional[List[SessionMergeOp]]:
    b1, b2 = session_to_time_blocks(session_id)
    csv1 = parse_csv_forecast_mw((csv_by_block.get(b1) or {}).get("forecast"))
    csv2 = parse_csv_forecast_mw((csv_by_block.get(b2) or {}).get("forecast"))

    def freq_from_demand(block: int, doc: Optional[Dict[str, Any]]) -> float:
        if doc is not None and doc.get("forecasted_frequency") is not None:
            return float(doc.get("forecasted_frequency") or 0.0)
        return float((demand_by_block.get(block) or {}).get("forecasted_frequency") or 0.0)

    if csv1 is not None and csv2 is not None:
        return [
            (b1, csv1, freq_from_demand(b1, csv_by_block.get(b1)), csv_source_label),
            (b2, csv2, freq_from_demand(b2, csv_by_block.get(b2)), csv_source_label),
        ]

    api1, api2 = api_by_block.get(b1), api_by_block.get(b2)
    if api1 is not None and api2 is not None:
        return [
            (b1, float(api1.get("forecast") or 0.0), float(api1.get("forecasted_frequency") or 0.0), api_source_label),
            (b2, float(api2.get("forecast") or 0.0), float(api2.get("forecasted_frequency") or 0.0), api_source_label),
        ]
    return None


def resolve_session_merge_ops_csv_manual(
    session_id: int,
    csv_by_block: Dict[int, Dict[str, Any]],
    demand_by_block: Dict[int, Dict[str, Any]],
    csv_source_label: str,
) -> List[SessionMergeOp]:
    """
    Manual mode merge for a session: both blocks use CSV source; missing or zero
    CSV forecast is written as 0 MW in demand_serve.
    """
    b1, b2 = session_to_time_blocks(session_id)

    def freq_from_demand(block: int, doc: Optional[Dict[str, Any]]) -> float:
        if doc is not None and doc.get("forecasted_frequency") is not None:
            return float(doc.get("forecasted_frequency") or 0.0)
        return float((demand_by_block.get(block) or {}).get("forecasted_frequency") or 0.0)

    def mw_for_block(block: int) -> float:
        parsed = parse_csv_forecast_mw((csv_by_block.get(block) or {}).get("forecast"))
        return 0.0 if parsed is None else parsed

    return [
        (b1, mw_for_block(b1), freq_from_demand(b1, csv_by_block.get(b1)), csv_source_label),
        (b2, mw_for_block(b2), freq_from_demand(b2, csv_by_block.get(b2)), csv_source_label),
    ]


def resolve_session_merge_ops_csv_only(
    session_id: int,
    csv_by_block: Dict[int, Dict[str, Any]],
    demand_by_block: Dict[int, Dict[str, Any]],
    csv_source_label: str,
) -> Optional[List[SessionMergeOp]]:
    """Manual mode: both blocks in the session must have valid CSV forecast."""
    b1, b2 = session_to_time_blocks(session_id)
    csv1 = parse_csv_forecast_mw((csv_by_block.get(b1) or {}).get("forecast"))
    csv2 = parse_csv_forecast_mw((csv_by_block.get(b2) or {}).get("forecast"))
    if csv1 is None or csv2 is None:
        return None

    def freq_from_demand(block: int, doc: Optional[Dict[str, Any]]) -> float:
        if doc is not None and doc.get("forecasted_frequency") is not None:
            return float(doc.get("forecasted_frequency") or 0.0)
        return float((demand_by_block.get(block) or {}).get("forecasted_frequency") or 0.0)

    return [
        (b1, csv1, freq_from_demand(b1, csv_by_block.get(b1)), csv_source_label),
        (b2, csv2, freq_from_demand(b2, csv_by_block.get(b2)), csv_source_label),
    ]


def resolve_session_merge_ops_api_only(
    session_id: int,
    api_by_block: Dict[int, Dict[str, Any]],
    api_source_label: str,
) -> Optional[List[SessionMergeOp]]:
    """Auto mode: both blocks in the session must have RTM API forecast."""
    b1, b2 = session_to_time_blocks(session_id)
    api1, api2 = api_by_block.get(b1), api_by_block.get(b2)
    if api1 is None or api2 is None:
        return None
    return [
        (b1, float(api1.get("forecast") or 0.0), float(api1.get("forecasted_frequency") or 0.0), api_source_label),
        (b2, float(api2.get("forecast") or 0.0), float(api2.get("forecasted_frequency") or 0.0), api_source_label),
    ]


def session_has_complete_csv(
    session_id: int,
    csv_by_block: Dict[int, Dict[str, Any]],
) -> bool:
    b1, b2 = session_to_time_blocks(session_id)
    return (
        parse_csv_forecast_mw((csv_by_block.get(b1) or {}).get("forecast")) is not None
        and parse_csv_forecast_mw((csv_by_block.get(b2) or {}).get("forecast")) is not None
    )
