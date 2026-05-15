"""
Forecast Serve Repository
Upserts IDF forecasts into forecast_serve — one document per source per block.
"""

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from pymongo import UpdateOne

from libs.database.base_repository import BaseRepository
from libs.models.forecast_serve_entity import (
    FORECAST_SOURCE_CSV,
    FORECAST_SOURCE_RTM_API,
    ensure_forecast_serve_indexes,
)

logger = logging.getLogger(__name__)

DEFAULT_COLLECTION = "forecast_serve"
IDF_TYPE = "IDF"


class ForecastServeRepository(BaseRepository):
    """Repository for forecast_serve collection."""

    def __init__(
        self,
        mongodb_uri: Optional[str] = None,
        database_name: Optional[str] = None,
        collection_name: str = DEFAULT_COLLECTION,
    ):
        mongodb_uri = mongodb_uri or os.getenv("MONGODB_URI", "mongodb://localhost:27017/epms")
        database_name = database_name or os.getenv("MONGODB_DATABASE", "epms")
        super().__init__(collection_name, mongodb_uri, database_name)

    async def create_indexes(self) -> None:
        await self._create_collection_if_not_exists()
        ensure_forecast_serve_indexes(self.collection)

    @staticmethod
    def _build_filter(
        scheduled_at: datetime,
        time_block: int,
        source: str,
        market_type: str = IDF_TYPE,
    ) -> Dict[str, Any]:
        return {
            "scheduled_at": scheduled_at,
            "time_block": int(time_block),
            "type": market_type,
            "source": source,
        }

    @staticmethod
    def _build_update(
        scheduled_at: datetime,
        time_block: int,
        forecast: Optional[float],
        source: str,
        now: datetime,
        market_type: str = IDF_TYPE,
    ) -> Dict[str, Any]:
        return {
            "$set": {
                "forecast": forecast,
                "updated_at": now,
            },
            "$setOnInsert": {
                "scheduled_at": scheduled_at,
                "time_block": int(time_block),
                "type": market_type,
                "source": source,
                "created_at": now,
            },
        }

    async def upsert_forecast(
        self,
        scheduled_at: datetime,
        time_block: int,
        forecast: Optional[float],
        source: str,
        market_type: str = IDF_TYPE,
    ) -> bool:
        """Upsert a single forecast document for the given source."""
        now = datetime.now()
        filter_doc = self._build_filter(scheduled_at, time_block, source, market_type)
        update_doc = self._build_update(
            scheduled_at, time_block, forecast, source, now, market_type
        )
        try:
            result = await self.collection.update_one(filter_doc, update_doc, upsert=True)
            return bool(result.upserted_id or result.modified_count > 0)
        except Exception as e:
            logger.error(
                "Error upserting forecast_serve scheduled_at=%s block=%s source=%s: %s",
                scheduled_at,
                time_block,
                source,
                e,
            )
            raise

    async def bulk_upsert_forecasts(
        self,
        records: List[Dict[str, Any]],
        source: str,
        market_type: str = IDF_TYPE,
    ) -> Dict[str, int]:
        """
        Bulk upsert forecasts. Each record must include scheduled_at, time_block,
        and optionally forecast (defaults to None).
        """
        if not records:
            return {"matched": 0, "modified": 0, "upserted": 0}

        now = datetime.now()
        operations = []
        for rec in records:
            scheduled_at = rec.get("scheduled_at")
            time_block = rec.get("time_block")
            if scheduled_at is None or time_block is None:
                continue
            forecast = rec.get("forecast")
            filter_doc = self._build_filter(scheduled_at, int(time_block), source, market_type)
            update_doc = self._build_update(
                scheduled_at, int(time_block), forecast, source, now, market_type
            )
            operations.append(UpdateOne(filter_doc, update_doc, upsert=True))

        if not operations:
            return {"matched": 0, "modified": 0, "upserted": 0}

        try:
            result = await self.collection.bulk_write(operations, ordered=False)
            return {
                "matched": result.matched_count,
                "modified": result.modified_count,
                "upserted": len(result.upserted_ids),
            }
        except Exception as e:
            logger.error("Error bulk upserting forecast_serve source=%s: %s", source, e)
            raise

    async def find_by_block(
        self,
        scheduled_at: datetime,
        time_block: int,
        market_type: str = IDF_TYPE,
    ) -> List[Dict[str, Any]]:
        """Return 0–2 documents (one per source) for a block."""
        try:
            cursor = self.collection.find(
                {
                    "scheduled_at": scheduled_at,
                    "time_block": int(time_block),
                    "type": market_type,
                }
            )
            return await cursor.to_list(length=10)
        except Exception as e:
            logger.error("Error finding forecast_serve by block: %s", e)
            return []


forecast_serve_repository = ForecastServeRepository()
