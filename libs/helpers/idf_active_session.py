"""
Active RTM session helpers for IDF forecast merge.
Session pairs come from rtm_product_hardCoded_data (RTM bidding), keyed by time_block.
"""

from typing import Any, Dict, List, Optional, Tuple

LAST_RTM_SESSION_BLOCK = 96


def index_forecast_by_block(docs: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    for doc in docs:
        tb = doc.get("time_block")
        if tb is not None:
            out[int(tb)] = doc
    return out


def active_session_blocks_from_mapping(
    session_mapping: Dict[int, Dict[str, Any]],
    current_block: int,
) -> List[int]:
    """
    Return sorted time_block list for the RTM session containing current_block.
    Falls back to (2s-1, 2s) math if mapping is missing.
    """
    block = max(1, min(LAST_RTM_SESSION_BLOCK, int(current_block)))
    row = session_mapping.get(block)
    if not row:
        session_id = (block - 1) // 2 + 1
        b1, b2 = 2 * session_id - 1, 2 * session_id
        return sorted([b1, b2])

    session_number = row.get("session_number") or row.get("Session_Id")
    if session_number is None:
        session_id = (block - 1) // 2 + 1
        b1, b2 = 2 * session_id - 1, 2 * session_id
        return sorted([b1, b2])

    blocks: List[int] = []
    for tb, info in session_mapping.items():
        sid = info.get("session_number") or info.get("Session_Id")
        if sid == session_number:
            blocks.append(int(tb))
    if not blocks:
        session_id = (block - 1) // 2 + 1
        b1, b2 = 2 * session_id - 1, 2 * session_id
        return sorted([b1, b2])
    return sorted(blocks)


def merge_time_blocks_active_through_session_48(active_session_start: int) -> List[int]:
    """Blocks from active session start through end of day (session 48 = block 96)."""
    start = max(1, min(LAST_RTM_SESSION_BLOCK, int(active_session_start)))
    return list(range(start, LAST_RTM_SESSION_BLOCK + 1))


def forecast_value_is_zero_or_blank(forecast: Any) -> bool:
    if forecast is None:
        return True
    if isinstance(forecast, str) and not str(forecast).strip():
        return True
    try:
        return float(forecast) == 0.0
    except (TypeError, ValueError):
        return True


def csv_forecast_mw(forecast: Any, treat_blank_as_zero: bool) -> Optional[float]:
    """Parse CSV MW; when treat_blank_as_zero, null/0/blank -> 0.0 instead of skip."""
    if forecast is None:
        return 0.0 if treat_blank_as_zero else None
    if isinstance(forecast, str) and not str(forecast).strip():
        return 0.0 if treat_blank_as_zero else None
    try:
        value = float(forecast)
    except (TypeError, ValueError):
        return 0.0 if treat_blank_as_zero else None
    if value == 0.0:
        return 0.0 if treat_blank_as_zero else None
    return value


def active_session_should_flip_manual_to_auto(
    active_blocks: List[int],
    csv_by_block: Dict[int, Dict[str, Any]],
) -> Tuple[bool, str]:
    """
    Manual -> Auto only when active session has exactly two blocks and any block
    has missing CSV row or zero/blank CSV forecast in forecast_serve.
    """
    if len(active_blocks) != 2:
        return False, ""

    for block in active_blocks:
        doc = csv_by_block.get(block)
        if doc is None or forecast_value_is_zero_or_blank(doc.get("forecast")):
            return True, "active_session_zero_or_blank_forecast"

    return False, ""
