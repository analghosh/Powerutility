"""
Reports PLF/PAF Repository — per-plant DC and SG averages from supply_serve.

Mirrors the legacy mplive source for the same report
(``HistoricalServeDataModel::getAllDataGroupbyDate``), which grouped
``survey_table_data_historical`` by plant and averaged ``volume`` once for
``dc_sg_type = DC`` and once for ``SG``.

EPMS keeps the same rows in two collections: ``supply_serve`` (a short rolling
window) and ``supply_serve_historical`` (the archive). The two overlap at the
roll boundary, so a naive union would count those days twice and skew the
average. Every date is therefore resolved to exactly one collection — live when
the date is present there, archive otherwise — before any grouping runs.

The same rows are also read block by block (``find_plant_blocks``) for the
block-wise view and download; those blocks average back to the plant figures.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from libs.helpers.historical_date_split import iter_dates_inclusive

from ...core.config import settings
from .base_repository import GenericRepository

logger = logging.getLogger(__name__)

DC_TYPE = "DC"
SG_TYPE = "SG"

_AGGREGATE_MAX_TIME_MS = 60_000
# 34 plants x 2 dc_sg types, with generous headroom for master-data growth.
_PLANT_TOTALS_MAX_DOCS = 5_000
# A 90-day download reads every plant's blocks: ~250k grouped rows.
_BLOCKS_MAX_TIME_MS = 120_000


def _to_datetime(date_str: str) -> datetime:
    return datetime.strptime(date_str, "%Y-%m-%d")


def _date_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    text = str(value)
    return text[:10] if len(text) >= 10 else text


class ReportsPlfPafRepository:
    def __init__(self):
        self.repository = GenericRepository()
        self.collection_name = settings.MONGODB_SUPPLY_SERVE_COLLECTION
        self.historical_collection_name = (
            settings.MONGODB_SUPPLY_SERVE_HISTORICAL_COLLECTION
        )
        self.plant_master_collection_name = settings.MONGODB_PLANT_MASTER_COLLECTION

    async def _dates_present(
        self,
        collection_name: str,
        from_date: str,
        to_date: str,
    ) -> Set[str]:
        """Which dates in the range this collection actually holds."""
        pipeline = [
            {
                "$match": {
                    "scheduled_at": {
                        "$gte": _to_datetime(from_date),
                        "$lt": _to_datetime(to_date) + timedelta(days=1),
                    }
                }
            },
            # Grouped on the calendar day rather than the raw value: a row stamped
            # with a time of day would otherwise present as a date of its own.
            {
                "$group": {
                    "_id": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$scheduled_at",
                        }
                    }
                }
            },
        ]
        rows = await self.repository.aggregate(
            collection_name,
            pipeline,
            max_time_ms=_AGGREGATE_MAX_TIME_MS,
        ) or []
        return {d for d in (_date_str(row.get("_id")) for row in rows) if d}

    @staticmethod
    def _build_plant_totals_pipeline(
        dates: Sequence[str],
        from_block: int,
        to_block: int,
    ) -> List[Dict[str, Any]]:
        # Day ranges, not an equality list: matching exact midnight datetimes
        # would silently drop a day whose rows carry a time of day.
        day_windows = [
            {
                "scheduled_at": {
                    "$gte": _to_datetime(d),
                    "$lt": _to_datetime(d) + timedelta(days=1),
                }
            }
            for d in dates
        ]
        return [
            {
                "$match": {
                    "$or": day_windows,
                    "time_block": {"$gte": from_block, "$lte": to_block},
                    "dc_sg": {"$in": [DC_TYPE, SG_TYPE]},
                    # $avg ignores non-numeric volumes; match the same rows so the
                    # sum/count pair recombines into the identical average.
                    "volume": {"$type": "number"},
                }
            },
            {
                "$group": {
                    "_id": {"plant_id": "$plant_id", "dc_sg": "$dc_sg"},
                    "volume_sum": {"$sum": "$volume"},
                    "block_count": {"$sum": 1},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "plant_id": "$_id.plant_id",
                    "dc_sg": "$_id.dc_sg",
                    "volume_sum": 1,
                    "block_count": 1,
                }
            },
        ]

    async def _plant_totals_for_dates(
        self,
        collection_name: str,
        dates: Sequence[str],
        from_block: int,
        to_block: int,
    ) -> List[Dict[str, Any]]:
        if not dates:
            return []
        return await self.repository.aggregate(
            collection_name,
            self._build_plant_totals_pipeline(dates, from_block, to_block),
            max_time_ms=_AGGREGATE_MAX_TIME_MS,
            max_docs=_PLANT_TOTALS_MAX_DOCS,
        ) or []

    @staticmethod
    def _accumulate(
        totals: Dict[Tuple[str, str], Dict[str, float]],
        rows: List[Dict[str, Any]],
    ) -> None:
        for row in rows:
            plant_id = row.get("plant_id")
            dc_sg = row.get("dc_sg")
            if not plant_id or dc_sg not in (DC_TYPE, SG_TYPE):
                continue
            bucket = totals.setdefault(
                (plant_id, dc_sg),
                {"volume_sum": 0.0, "block_count": 0},
            )
            bucket["volume_sum"] += float(row.get("volume_sum") or 0.0)
            bucket["block_count"] += int(row.get("block_count") or 0)

    async def find_plant_dc_sg_averages(
        self,
        from_date: str,
        to_date: str,
        from_block: int = 1,
        to_block: int = 96,
    ) -> Dict[Tuple[str, str], Dict[str, float]]:
        """
        Average DC and SG volume per plant across the date and block range.

        Returns ``{(plant_id, "DC"|"SG"): {"average", "block_count"}}``.
        """
        try:
            all_dates = iter_dates_inclusive(from_date, to_date)
            live_dates = await self._dates_present(
                self.collection_name, from_date, to_date
            )
            # Live wins wherever it has the date; the archive covers the rest.
            live = [d for d in all_dates if d in live_dates]
            historical = [d for d in all_dates if d not in live_dates]

            totals: Dict[Tuple[str, str], Dict[str, float]] = {}
            self._accumulate(
                totals,
                await self._plant_totals_for_dates(
                    self.collection_name, live, from_block, to_block
                ),
            )
            self._accumulate(
                totals,
                await self._plant_totals_for_dates(
                    self.historical_collection_name, historical, from_block, to_block
                ),
            )

            return {
                key: {
                    "average": (
                        bucket["volume_sum"] / bucket["block_count"]
                        if bucket["block_count"]
                        else 0.0
                    ),
                    "block_count": bucket["block_count"],
                }
                for key, bucket in totals.items()
            }
        except Exception as e:
            logger.error(
                "Failed to get PLF/PAF plant averages for %s to %s blocks %s-%s: %s",
                from_date,
                to_date,
                from_block,
                to_block,
                e,
            )
            return {}

    @staticmethod
    def _build_plant_blocks_pipeline(
        dates: Sequence[str],
        from_block: int,
        to_block: int,
        plant_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        match: Dict[str, Any] = {
            "$or": [
                {
                    "scheduled_at": {
                        "$gte": _to_datetime(d),
                        "$lt": _to_datetime(d) + timedelta(days=1),
                    }
                }
                for d in dates
            ],
            "time_block": {"$gte": from_block, "$lte": to_block},
            "dc_sg": {"$in": [DC_TYPE, SG_TYPE]},
            # Same rows the averages read, so the blocks average back to them.
            "volume": {"$type": "number"},
        }
        if plant_id:
            match["plant_id"] = plant_id

        def volume_of(dc_sg: str) -> Dict[str, Any]:
            # $avg skips the null, so a block with no DC row reports DC as null.
            return {"$avg": {"$cond": [{"$eq": ["$dc_sg", dc_sg]}, "$volume", None]}}

        return [
            {"$match": match},
            {
                "$group": {
                    "_id": {
                        "plant_id": "$plant_id",
                        "date": {
                            "$dateToString": {"format": "%Y-%m-%d", "date": "$scheduled_at"}
                        },
                        "time_block": "$time_block",
                    },
                    "dc": volume_of(DC_TYPE),
                    "sg": volume_of(SG_TYPE),
                }
            },
            {"$sort": {"_id.plant_id": 1, "_id.date": 1, "_id.time_block": 1}},
            {
                "$project": {
                    "_id": 0,
                    "plant_id": "$_id.plant_id",
                    "date": "$_id.date",
                    "time_block": "$_id.time_block",
                    "dc": 1,
                    "sg": 1,
                }
            },
        ]

    async def find_plant_blocks(
        self,
        from_date: str,
        to_date: str,
        from_block: int = 1,
        to_block: int = 96,
        plant_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        DC and SG side by side for every plant, date and block in the range.

        Returns ``[{"plant_id", "date", "time_block", "dc", "sg"}]`` ordered by
        plant, date and block. Each date comes from one collection, as for the
        averages, so the overlap day is not read twice.
        """
        all_dates = iter_dates_inclusive(from_date, to_date)
        live_dates = await self._dates_present(self.collection_name, from_date, to_date)
        sources = (
            (self.collection_name, [d for d in all_dates if d in live_dates]),
            (self.historical_collection_name, [d for d in all_dates if d not in live_dates]),
        )

        rows: List[Dict[str, Any]] = []
        for collection_name, dates in sources:
            if not dates:
                continue
            rows.extend(
                await self.repository.aggregate(
                    collection_name,
                    self._build_plant_blocks_pipeline(dates, from_block, to_block, plant_id),
                    max_time_ms=_BLOCKS_MAX_TIME_MS,
                ) or []
            )
        # The two collections cover different dates, so one sort puts them in order.
        rows.sort(key=lambda r: (r["plant_id"], r["date"], r["time_block"]))
        return rows

    async def find_plant_master(self) -> Dict[str, Dict[str, Any]]:
        """Plant display name, technology and contracted capacity, keyed by plant_id."""
        try:
            rows = await self.repository.get_all(
                self.plant_master_collection_name,
                projection={
                    "_id": 0,
                    "plant_id": 1,
                    "plant_name": 1,
                    "technology": 1,
                    "category": 1,
                    "c_s": 1,
                    "ppa": 1,
                },
            ) or []
            return {
                row["plant_id"]: row
                for row in rows
                if row.get("plant_id")
            }
        except Exception as e:
            logger.error("Failed to load plant master for PLF/PAF report: %s", e)
            return {}


reports_plf_paf_repository = ReportsPlfPafRepository()
