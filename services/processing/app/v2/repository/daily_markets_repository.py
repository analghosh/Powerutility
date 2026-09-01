"""
Daily Markets Report Repository — per-block trade, price and limit reads.

Every source here is a 4-day rolling "serve" collection with a matching
"_historical" archive, so each read routes by date the same way
ReportsForecastRepository does: past dates come from the archive, today and
future from the live collection with the archive merged in behind it.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from libs.helpers.historical_date_split import is_past_date, today_date_str

from ...core.config import settings
from .base_repository import GenericRepository

logger = logging.getLogger(__name__)

# limit_values ids that back Annexure IV (Other Details).
LIMIT_VALUE_CEILING_PRICE_ID = "Ceiling_Price"
LIMIT_VALUE_BUFFER_ID = "Buffer"
LIMIT_VALUE_N_MINUS_1_ID = "n_1"

ANNEXURE_IV_LIMIT_IDS = (
    LIMIT_VALUE_CEILING_PRICE_ID,
    LIMIT_VALUE_BUFFER_ID,
    LIMIT_VALUE_N_MINUS_1_ID,
)

BLOCKS_PER_DAY = 96

_AGGREGATE_MAX_TIME_MS = 60_000
# One day never exceeds 96 blocks per series; the caps are runaway guards only.
_TRADE_MAX_DOCS = BLOCKS_PER_DAY * 8
_EXCHANGE_MAX_DOCS = BLOCKS_PER_DAY * 8
_LIMIT_MAX_DOCS = BLOCKS_PER_DAY * len(ANNEXURE_IV_LIMIT_IDS) * 4


def _day_bounds(report_date: str) -> tuple:
    """Return [start, end) datetimes spanning one YYYY-MM-DD calendar day."""
    start = datetime.strptime(report_date, "%Y-%m-%d")
    return start, start + timedelta(days=1)


class DailyMarketsRepository:
    def __init__(self):
        self.repository = GenericRepository()
        self.trade_collection = getattr(
            settings, "MONGODB_SERVE_PX_BID_COLLECTION", "bid_trade_serve"
        )
        self.trade_historical_collection = getattr(
            settings,
            "MONGODB_BID_TRADE_SERVE_HISTORICAL_COLLECTION",
            "bid_trade_serve_historical",
        )
        self.exchange_collection = getattr(
            settings, "MONGODB_SERVE_EXCHANGE_COLLECTION", "exchange_serve"
        )
        self.exchange_historical_collection = getattr(
            settings,
            "MONGODB_EXCHANGE_SERVE_HISTORICAL_COLLECTION",
            "exchange_serve_historical",
        )
        self.limit_values_collection = getattr(
            settings, "MONGODB_LIMIT_VALUES_COLLECTION", "limit_values"
        )
        self.limit_values_historical_collection = getattr(
            settings,
            "MONGODB_LIMIT_VALUES_HISTORICAL_COLLECTION",
            "limit_values_historical",
        )
        self.time_block_collection = getattr(
            settings, "MONGODB_TIME_BLOCK_COLLECTION", "time_block"
        )
        self.session_collection = getattr(
            settings,
            "MONGODB_RTM_PRODUCT_HARDCODED_DATA_COLLECTION",
            "rtm_product_hardCoded_data",
        )

    # ---------- generic serve/historical routing ----------

    async def _read_with_historical_fallback(
        self,
        report_date: str,
        live_collection: str,
        historical_collection: str,
        pipeline_builder,
        key_fn,
        max_docs: int,
    ) -> List[Dict[str, Any]]:
        """
        Read one day from the serve collection, the archive, or both.

        Serve collections only keep a 4-day window, so anything older has to come
        from the archive. On today/future both are read and live rows win, which
        keeps the report correct while a day is still being archived.
        """
        if is_past_date(report_date, today_date_str()):
            return await self._aggregate(
                historical_collection,
                pipeline_builder(historical=True),
                max_docs,
            )

        live_rows = await self._aggregate(
            live_collection, pipeline_builder(historical=False), max_docs
        )
        historical_rows = await self._aggregate(
            historical_collection, pipeline_builder(historical=True), max_docs
        )
        if not historical_rows:
            return live_rows

        merged = {key_fn(row): row for row in historical_rows}
        merged.update({key_fn(row): row for row in live_rows})
        return list(merged.values())

    async def _aggregate(
        self,
        collection_name: str,
        pipeline: List[Dict[str, Any]],
        max_docs: int,
    ) -> List[Dict[str, Any]]:
        try:
            return await self.repository.aggregate(
                collection_name,
                pipeline,
                max_time_ms=_AGGREGATE_MAX_TIME_MS,
                max_docs=max_docs,
            ) or []
        except Exception as e:
            logger.error("Aggregation on '%s' failed: %s", collection_name, e)
            return []

    # ---------- bid_trade_serve ----------

    def _build_trade_pipeline(
        self,
        report_date: str,
        market: str,
        *,
        historical: bool,
    ) -> List[Dict[str, Any]]:
        """
        Sum bidded and traded per time block.

        Single and block bids are separate documents for the same block
        (unique key includes bid_type), which is mplive's
        single_bid_quantum + block_bid_quantum. Both fields are already signed
        (sell is negative), so summing preserves direction.
        """
        start, end = _day_bounds(report_date)
        return [
            {
                "$match": {
                    "scheduled_at": {"$gte": start, "$lt": end},
                    "type": market,
                }
            },
            {
                "$group": {
                    "_id": "$time_block",
                    "bidded": {"$sum": {"$ifNull": ["$bidded", 0]}},
                    "traded": {"$sum": {"$ifNull": ["$traded", 0]}},
                    "document_count": {"$sum": 1},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "time_block": "$_id",
                    "bidded": 1,
                    "traded": 1,
                    "document_count": 1,
                }
            },
            {"$sort": {"time_block": 1}},
        ]

    async def find_trade_volumes_by_date(
        self,
        report_date: str,
        market: str,
    ) -> List[Dict[str, Any]]:
        return await self._read_with_historical_fallback(
            report_date,
            self.trade_collection,
            self.trade_historical_collection,
            lambda historical: self._build_trade_pipeline(
                report_date, market, historical=historical
            ),
            lambda row: row.get("time_block"),
            _TRADE_MAX_DOCS,
        )

    # ---------- exchange_serve ----------

    def _build_exchange_pipeline(
        self,
        report_date: str,
        market: str,
        exchange: str,
        *,
        historical: bool,
    ) -> List[Dict[str, Any]]:
        start, end = _day_bounds(report_date)
        return [
            {
                "$match": {
                    "scheduled_at": {"$gte": start, "$lt": end},
                    "market_type": market,
                    "px_name": exchange,
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "time_block": 1,
                    "acp": 1,
                    "mcp": 1,
                }
            },
            {"$sort": {"time_block": 1}},
        ]

    async def find_area_prices_by_date(
        self,
        report_date: str,
        market: str,
        exchange: str,
    ) -> List[Dict[str, Any]]:
        return await self._read_with_historical_fallback(
            report_date,
            self.exchange_collection,
            self.exchange_historical_collection,
            lambda historical: self._build_exchange_pipeline(
                report_date, market, exchange, historical=historical
            ),
            lambda row: row.get("time_block"),
            _EXCHANGE_MAX_DOCS,
        )

    # ---------- limit_values ----------

    def _build_limit_values_pipeline(
        self,
        report_date: str,
        market: str,
        *,
        historical: bool,
    ) -> List[Dict[str, Any]]:
        """
        Ceiling price, buffer price and (N-1) quantum per block.

        The archive nests block/id/market under `metadata`, so those are lifted
        first — same normalisation ReportsForecastRepository applies.
        """
        start, end = _day_bounds(report_date)
        pipeline: List[Dict[str, Any]] = []
        if historical:
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
                        "scheduled_at": {"$gte": start, "$lt": end},
                        "market": market,
                        "id": {"$in": list(ANNEXURE_IV_LIMIT_IDS)},
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "block": 1,
                        "id": 1,
                        "values": 1,
                        "unit": 1,
                    }
                },
                {"$sort": {"block": 1}},
            ]
        )
        return pipeline

    async def find_limit_values_by_date(
        self,
        report_date: str,
        market: str,
    ) -> List[Dict[str, Any]]:
        return await self._read_with_historical_fallback(
            report_date,
            self.limit_values_collection,
            self.limit_values_historical_collection,
            lambda historical: self._build_limit_values_pipeline(
                report_date, market, historical=historical
            ),
            lambda row: (row.get("block"), row.get("id")),
            _LIMIT_MAX_DOCS,
        )

    # ---------- static reference data ----------

    async def find_time_blocks(self) -> List[Dict[str, Any]]:
        """96 rows of block -> start/end clock time."""
        return await self._aggregate(
            self.time_block_collection,
            [
                {
                    "$project": {
                        "_id": 0,
                        "timeblock": 1,
                        "starttime": 1,
                        "endtime": 1,
                    }
                },
                {"$sort": {"timeblock": 1}},
            ],
            BLOCKS_PER_DAY,
        )

    async def find_session_mapping(self) -> List[Dict[str, Any]]:
        """
        Block -> 30-minute trade session (1-48).

        Mirrors mplive's getSessionNum(), which buckets the block's clock time
        into 48 half-hour sessions. The service falls back to ceil(block / 2)
        when this collection has not been seeded.
        """
        return await self._aggregate(
            self.session_collection,
            [
                {
                    "$project": {
                        "_id": 0,
                        "time_block": 1,
                        "session_number": {
                            "$ifNull": ["$session_number", "$Session_Id"]
                        },
                    }
                },
                {"$sort": {"time_block": 1}},
            ],
            BLOCKS_PER_DAY,
        )


daily_markets_repository = DailyMarketsRepository()
