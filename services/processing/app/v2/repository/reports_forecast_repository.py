"""
Reports Forecast Repository — projected reads from limit_values for forecasted prices.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

from libs.helpers.historical_date_split import split_date_range_by_today, today_date_str

from ...core.config import settings
from .base_repository import GenericRepository

logger = logging.getLogger(__name__)

FORECAST_PRICE_IDS = {
    "RTM": "RTM_forecasted_price",
    "DAM": "DAM_forecasted_price",
    "GDAM": "GDAM_forecasted_price",
    "HPDAM": "HPDAM_forecasted_price",
}

LIMIT_VALUES_PRICE_PROJECTION: Dict[str, int] = {
    "_id": 0,
    "scheduled_at": 1,
    "block": 1,
    "market": 1,
    "id": 1,
    "values": 1,
}

# 90 days × 96 blocks × 4 markets
_LIMIT_VALUES_RANGE_MAX_DOCS = 90 * 96 * 4
_AGGREGATE_MAX_TIME_MS = 60_000


def _limit_values_price_row_key(row: Dict[str, Any]) -> tuple:
    scheduled_at = row.get("scheduled_at")
    if hasattr(scheduled_at, "strftime"):
        date_key = scheduled_at.strftime("%Y-%m-%d")
    else:
        date_key = str(scheduled_at)[:10] if scheduled_at else ""
    market = row.get("market") or row.get("id")
    return (date_key, row.get("block"), market)


class ReportsForecastRepository:
    def __init__(self):
        self.repository = GenericRepository()
        self.collection_name = getattr(
            settings, "MONGODB_LIMIT_VALUES_COLLECTION", "limit_values"
        )
        self.historical_collection_name = getattr(
            settings,
            "MONGODB_LIMIT_VALUES_HISTORICAL_COLLECTION",
            "limit_values_historical",
        )

    def _build_limit_values_price_pipeline(
        self,
        range_start: datetime,
        range_end_exclusive: datetime,
        from_block: int,
        to_block: int,
        *,
        normalize_metadata: bool = False,
    ) -> List[Dict[str, Any]]:
        pipeline: List[Dict[str, Any]] = []
        if normalize_metadata:
            pipeline.append(
                {
                    "$addFields": {
                        "block": {"$ifNull": ["$block", "$metadata.block"]},
                        "id": {"$ifNull": ["$id", "$metadata.id"]},
                        "market": {"$ifNull": ["$market", "$metadata.market"]},
                        "category": {"$ifNull": ["$category", "$metadata.category"]},
                    }
                }
            )
        pipeline.extend(
            [
                {
                    "$match": {
                        "scheduled_at": {
                            "$gte": range_start,
                            "$lt": range_end_exclusive,
                        },
                        "block": {"$gte": from_block, "$lte": to_block},
                        "id": {"$in": list(FORECAST_PRICE_IDS.values())},
                        "category": "Limits",
                    }
                },
                {"$project": LIMIT_VALUES_PRICE_PROJECTION},
                {"$sort": {"scheduled_at": 1, "block": 1}},
            ]
        )
        return pipeline

    async def _find_prices_in_collection(
        self,
        collection_name: str,
        from_date: str,
        to_date: str,
        from_block: int,
        to_block: int,
        *,
        normalize_metadata: bool = False,
    ) -> List[Dict[str, Any]]:
        range_start = datetime.strptime(from_date, "%Y-%m-%d")
        range_end_exclusive = datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)
        pipeline = self._build_limit_values_price_pipeline(
            range_start,
            range_end_exclusive,
            from_block,
            to_block,
            normalize_metadata=normalize_metadata,
        )
        return await self.repository.aggregate(
            collection_name,
            pipeline,
            max_time_ms=_AGGREGATE_MAX_TIME_MS,
            max_docs=_LIMIT_VALUES_RANGE_MAX_DOCS,
        ) or []

    @staticmethod
    def _merge_price_rows_prefer_live(
        live_rows: List[Dict[str, Any]],
        historical_rows: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        live_map = {_limit_values_price_row_key(row): row for row in live_rows}
        merged = list(live_map.values())
        for row in historical_rows:
            key = _limit_values_price_row_key(row)
            if key not in live_map:
                merged.append(row)
        merged.sort(
            key=lambda row: (
                str(row.get("scheduled_at") or ""),
                row.get("block") or 0,
            )
        )
        return merged

    async def find_forecasted_prices_by_date_range(
        self,
        from_date: str,
        to_date: str,
        from_block: int = 1,
        to_block: int = 96,
    ) -> List[Dict[str, Any]]:
        """
        Return forecasted price rows for the given date/block range.

        Past dates read from limit_values_historical (metadata-normalized).
        Current/future dates merge limit_values with historical, preferring live rows.
        """
        try:
            today = today_date_str()
            past_from, past_to, current_from, current_to = split_date_range_by_today(
                from_date, to_date, today
            )

            merged: List[Dict[str, Any]] = []

            if past_from and past_to:
                merged.extend(
                    await self._find_prices_in_collection(
                        self.historical_collection_name,
                        past_from,
                        past_to,
                        from_block,
                        to_block,
                        normalize_metadata=True,
                    )
                )

            if current_from and current_to:
                live_results = await self._find_prices_in_collection(
                    self.collection_name,
                    current_from,
                    current_to,
                    from_block,
                    to_block,
                )
                hist_results = await self._find_prices_in_collection(
                    self.historical_collection_name,
                    current_from,
                    current_to,
                    from_block,
                    to_block,
                    normalize_metadata=True,
                )
                merged.extend(
                    self._merge_price_rows_prefer_live(live_results, hist_results)
                )

            return merged
        except Exception as e:
            logger.error(
                "Failed to get forecasted prices for %s to %s blocks %s-%s: %s",
                from_date,
                to_date,
                from_block,
                to_block,
                e,
            )
            return []


reports_forecast_repository = ReportsForecastRepository()
