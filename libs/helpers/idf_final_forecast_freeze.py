"""
Freeze RTM API forecast into forecast_serve type=IDF_final (one row per block).

Blocks before the RTM active session are frozen (immutable). Active session and
future blocks are refreshed from the latest rtm_forecast_api revision each run.
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pymongo import UpdateOne

from libs.helpers.idf_forecast_session import time_block_to_session_id
from libs.models.forecast_serve_entity import (
    FORECAST_SOURCE_RTM_API,
    IDF_FINAL_TYPE,
)

IDF_FINAL_BLOCKS = list(range(1, 97))


def is_idf_final_frozen_territory(
    scheduled_date: date,
    time_block: int,
    today_ist: date,
    after_evening_cutover: bool,
    active_session_id: int,
) -> bool:
    """
    True when a block's forecast should be frozen (no further updates).

    - Past delivery dates: all blocks frozen.
    - Today (before evening cutover): sessions before active session.
    - Tomorrow before 22:30: nothing frozen.
    - Tomorrow from 22:30: sessions before active session.
    """
    if scheduled_date < today_ist:
        return True
    if scheduled_date > today_ist:
        if after_evening_cutover:
            return time_block_to_session_id(time_block) < active_session_id
        return False
    if after_evening_cutover:
        return True
    return time_block_to_session_id(time_block) < active_session_id


def _existing_is_frozen(doc: Optional[Dict[str, Any]]) -> bool:
    if not doc:
        return False
    info = doc.get("info") or {}
    return bool(info.get("frozen"))


def build_idf_final_document(
    scheduled_at: datetime,
    time_block: int,
    api_doc: Dict[str, Any],
    *,
    frozen: bool,
    active_session_id: int,
    now: datetime,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build an IDF_final upsert document with traceability in info."""
    source_revision = api_doc.get("forecast_revision")
    info: Dict[str, Any] = {
        "frozen": frozen,
        "source": FORECAST_SOURCE_RTM_API,
        "source_forecast_revision": (
            int(source_revision) if source_revision is not None else None
        ),
        "active_session_id_at_write": int(active_session_id),
    }
    if frozen:
        frozen_at = (existing or {}).get("frozen_at")
        if frozen_at is None:
            frozen_at = now
        info["frozen_at"] = frozen_at

    doc: Dict[str, Any] = {
        "scheduled_at": scheduled_at,
        "time_block": int(time_block),
        "type": IDF_FINAL_TYPE,
        "source": FORECAST_SOURCE_RTM_API,
        "forecast": float(api_doc.get("forecast") or 0.0),
        "forecasted_frequency": float(api_doc.get("forecasted_frequency") or 0.0),
        "forecast_revision": (
            int(source_revision) if source_revision is not None else None
        ),
        "info": info,
        "updated_at": now,
    }
    if frozen:
        doc["frozen_at"] = info.get("frozen_at", now)
    return doc


def build_idf_final_bulk_ops(
    scheduled_at: datetime,
    scheduled_date: date,
    today_ist: date,
    after_evening_cutover: bool,
    active_session_id: int,
    api_by_block: Dict[int, Dict[str, Any]],
    existing_by_block: Dict[int, Dict[str, Any]],
    now: datetime,
    blocks: Optional[List[int]] = None,
) -> List[UpdateOne]:
    """
    Build bulk upsert ops for IDF_final rows (blocks 1–96 when API data exists).

    Skips frozen blocks that are already stored. Always refreshes non-frozen blocks.
    """
    ops: List[UpdateOne] = []
    block_list = blocks if blocks is not None else IDF_FINAL_BLOCKS

    for time_block in block_list:
        api_doc = api_by_block.get(int(time_block))
        if not api_doc:
            continue

        existing = existing_by_block.get(int(time_block))
        frozen_territory = is_idf_final_frozen_territory(
            scheduled_date,
            int(time_block),
            today_ist,
            after_evening_cutover,
            active_session_id,
        )

        if frozen_territory and _existing_is_frozen(existing):
            continue

        doc = build_idf_final_document(
            scheduled_at,
            int(time_block),
            api_doc,
            frozen=frozen_territory,
            active_session_id=active_session_id,
            now=now,
            existing=existing,
        )
        filter_doc = {
            "scheduled_at": scheduled_at,
            "time_block": int(time_block),
            "type": IDF_FINAL_TYPE,
        }
        ops.append(
            UpdateOne(
                filter_doc,
                {
                    "$set": doc,
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )
        )
    return ops
