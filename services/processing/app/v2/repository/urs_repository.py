"""
Read side of ``urs_serve`` — un-requisitioned surplus per plant per block.

The collection is written by the scheduler's supply processor, which diverts
URS rows out of the Grid India feed before they reach dc_sg. Nothing here
writes.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

from .base_repository import GenericRepository

logger = logging.getLogger(__name__)

COLLECTION = "urs_serve"

#: An aggregation that walks a whole day for every plant should finish in well
#: under a second on an indexed collection. A minute means something is wrong -
#: better to fail the request than to hold a worker open indefinitely.
_AGGREGATE_MAX_TIME_MS = 60_000


def _day_bounds(schedule_date: str) -> tuple:
    start = datetime.strptime(schedule_date, "%Y-%m-%d")
    return start, start + timedelta(days=1)


class UrsRepository(GenericRepository):
    """Reads URS rows for the URS Availed report."""

    async def get_day(
        self,
        schedule_date: str,
        from_block: int,
        to_block: int,
    ) -> List[Dict[str, Any]]:
        """
        Every stored URS row for one day, within a block range.

        Sorted by block then plant so the caller can build the grid in one
        pass rather than holding the whole day in a dictionary first.
        """
        start, end = _day_bounds(schedule_date)
        pipeline = [
            {
                "$match": {
                    "scheduled_at": {"$gte": start, "$lt": end},
                    "time_block": {"$gte": from_block, "$lte": to_block},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "time_block": 1,
                    "plant_id": 1,
                    "plant_name": 1,
                    "technology": 1,
                    "volume": 1,
                    "fc_price": 1,
                    "vc_price": 1,
                    "total_fc_vc_price": 1,
                    "price_complete": 1,
                    "source_revision_number": 1,
                    "schedule_published_time": 1,
                }
            },
            {"$sort": {"time_block": 1, "plant_id": 1}},
        ]
        return await self.aggregate(
            COLLECTION, pipeline, max_time_ms=_AGGREGATE_MAX_TIME_MS
        )

    async def dates_with_data(self, limit: int = 60) -> List[Dict[str, Any]]:
        """
        Which days hold URS, newest first.

        The report defaults to today, and on a fresh install or a day the feed
        was quiet that is an empty screen with nothing to click. These are the
        picker's suggestions.
        """
        pipeline = [
            {
                "$group": {
                    "_id": {
                        "$dateToString": {"format": "%Y-%m-%d", "date": "$scheduled_at"}
                    },
                    "plant_count": {"$addToSet": "$plant_id"},
                }
            },
            {"$project": {"_id": 0, "date": "$_id", "plant_count": {"$size": "$plant_count"}}},
            {"$sort": {"date": -1}},
            {"$limit": limit},
        ]
        return await self.aggregate(
            COLLECTION, pipeline, max_time_ms=_AGGREGATE_MAX_TIME_MS
        )


urs_repository = UrsRepository()
