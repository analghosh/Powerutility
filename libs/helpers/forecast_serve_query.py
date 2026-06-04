"""
MongoDB query helpers for forecast_serve — latest forecast_revision in the database.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

IDF_TYPE = "IDF"

# Fields needed for merge / read API (avoid loading full documents).
FORECAST_SERVE_READ_PROJECTION: Dict[str, int] = {
    "_id": 1,
    "scheduled_at": 1,
    "time_block": 1,
    "type": 1,
    "source": 1,
    "forecast": 1,
    "forecasted_frequency": 1,
    "forecast_revision": 1,
    "created_at": 1,
    "updated_at": 1,
}


def build_day_source_match(
    scheduled_at: datetime,
    source: str,
    market_type: str = IDF_TYPE,
    scheduled_at_end: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Match filter for one delivery day (exact or half-open range) and source."""
    if scheduled_at_end is not None:
        return {
            "scheduled_at": {"$gte": scheduled_at, "$lt": scheduled_at_end},
            "type": market_type,
            "source": source,
        }
    return {
        "scheduled_at": scheduled_at,
        "type": market_type,
        "source": source,
    }


async def fetch_latest_forecast_revision(
    collection,
    scheduled_at: datetime,
    source: str,
    market_type: str = IDF_TYPE,
    scheduled_at_end: Optional[datetime] = None,
) -> Optional[int]:
    """Return max forecast_revision for (day, source), or None if unset on all rows."""
    pipeline = [
        {"$match": build_day_source_match(scheduled_at, source, market_type, scheduled_at_end)},
        {"$group": {"_id": None, "max_rev": {"$max": "$forecast_revision"}}},
    ]
    rows = await collection.aggregate(pipeline).to_list(length=1)
    if not rows or rows[0].get("max_rev") is None:
        return None
    return int(rows[0]["max_rev"])


def apply_latest_revision_to_filter(
    base_filter: Dict[str, Any],
    latest_revision: Optional[int],
) -> Dict[str, Any]:
    """Add forecast_revision to a find filter when a latest revision exists."""
    if latest_revision is None:
        return base_filter
    return {**base_filter, "forecast_revision": latest_revision}


def build_latest_revision_or_filter(
    scheduled_at: datetime,
    scheduled_at_end: datetime,
    sources_latest: Dict[str, Optional[int]],
    market_type: str = IDF_TYPE,
    time_block_range: Optional[tuple] = None,
) -> Dict[str, Any]:
    """
    Build a find filter for multiple sources, each constrained to its latest revision.

    sources_latest: map of source -> latest revision (None = legacy, no revision filter).
    """
    base: Dict[str, Any] = {
        "scheduled_at": {"$gte": scheduled_at, "$lt": scheduled_at_end},
        "type": market_type,
    }
    if time_block_range is not None:
        lo, hi = time_block_range
        base["time_block"] = {"$gte": int(lo), "$lte": int(hi)}

    or_clauses: List[Dict[str, Any]] = []
    for source, latest_rev in sources_latest.items():
        clause: Dict[str, Any] = {"source": source}
        if latest_rev is not None:
            clause["forecast_revision"] = latest_rev
        or_clauses.append(clause)

    if len(or_clauses) == 1:
        return {**base, **or_clauses[0]}
    return {**base, "$or": or_clauses}
