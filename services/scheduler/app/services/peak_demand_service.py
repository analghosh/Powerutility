"""
Peak demand computation service.
Computes and backfills peak demand from demand_serve and demand_serve_historical
into peak_demand_serve, filtering only type=IDF records.
Includes today's data (up to current block) and archives records older than 31 days.
"""

import logging
from datetime import datetime, timedelta, date
from typing import Dict, Any, Optional, Tuple
from pymongo import DESCENDING
from motor.motor_asyncio import AsyncIOMotorCollection

from services.scheduler.app.repository.demand_serve_repository import DemandServeRepository
from services.scheduler.app.repository.peak_demand_repository import PeakDemandRepository
from libs.models.peak_demand_serve_entity import PeakDemandServeEntity

logger = logging.getLogger(__name__)

RETENTION_DAYS = 31


class PeakDemandService:
    def __init__(self):
        self.demand_repo = DemandServeRepository()
        self.peak_repo = PeakDemandRepository()

    @staticmethod
    def _get_current_block() -> int:
        """Get current time block (1-96) based on current time."""
        now = datetime.now()
        return (now.hour * 4) + (now.minute // 15) + 1

    async def compute_and_store_peak(self) -> Dict[str, Any]:
        """
        Computes and stores peak demand, backfilling from the last known date
        up to and including today (using current block for today).
        Also archives records older than 31 days.
        """
        today = datetime.now().date()

        # Step 1: Archive old records (older than 31 days)
        archival_result = await self.peak_repo.archive_old_records(RETENTION_DAYS)
        logger.info("Archival result: %s", archival_result)

        # Step 2: Determine start date
        start_date = await self._get_start_date()
        if not start_date:
            logger.info("No start date found for peak demand computation. Nothing to do.")
            return {"status": "skipped", "message": "No data to process."}

        # Validate date range
        if start_date > today:
            logger.info("Start date %s is after today %s. Nothing to process.", start_date, today)
            return {"status": "skipped", "message": "No dates to process."}

        logger.info("Starting peak demand computation from %s to %s", start_date, today)

        total_processed = 0
        total_skipped = 0
        current_date = start_date

        while current_date <= today:
            logger.info("Processing peak demand for %s", current_date.isoformat())

            is_today = current_date == today

            # Use live collection for today/yesterday, historical for older dates
            yesterday = today - timedelta(days=1)
            collection_to_use = (
                self.demand_repo.serve_collection
                if current_date >= yesterday
                else self.demand_repo.historical_collection
            )

            processed_for_date, skipped_for_date = await self._compute_and_store_for_date(
                current_date, collection_to_use, is_today
            )

            total_processed += processed_for_date
            total_skipped += skipped_for_date
            current_date += timedelta(days=1)

        logger.info(
            "Peak demand computation finished. Processed: %d, Skipped: %d",
            total_processed, total_skipped
        )
        return {
            "status": "success",
            "processed_records": total_processed,
            "skipped_records": total_skipped,
            "start_date": start_date.isoformat(),
            "end_date": today.isoformat(),
            "archival": archival_result,
        }

    async def _get_start_date(self) -> Optional[date]:
        """Determines the start date for backfilling peak demand data."""
        # 1. Find the last entry in peak_demand_serve
        last_peak_record = await self.peak_repo.serve_collection.find_one(
            sort=[("scheduled_at", DESCENDING)]
        )

        if last_peak_record and "scheduled_at" in last_peak_record:
            last_date = last_peak_record["scheduled_at"].date()
            logger.info("Last peak demand record found for %s. Starting from next day.", last_date)
            return last_date

        # 2. If peak_demand_serve is empty, check demand_serve_historical
        logger.info("No peak demand records found. Checking historical demand data.")
        first_historical_record = await self.demand_repo.historical_collection.find_one(
            {"type": "IDF"}, sort=[("scheduled_at", 1)]
        )

        if first_historical_record and "scheduled_at" in first_historical_record:
            first_date = first_historical_record["scheduled_at"].date()
            logger.info("First historical IDF record found for %s.", first_date)
            return first_date

        # 3. If historical is also empty, check live demand_serve
        logger.info("No historical demand records. Checking live demand data.")
        first_live_record = await self.demand_repo.serve_collection.find_one(
            {"type": "IDF"}, sort=[("scheduled_at", 1)]
        )
        if first_live_record and "scheduled_at" in first_live_record:
            first_date = first_live_record["scheduled_at"].date()
            logger.info("First live IDF record found for %s.", first_date)
            return first_date

        return None

    async def _compute_and_store_for_date(
        self, target_date: date, source_collection: AsyncIOMotorCollection, is_today: bool = False
    ) -> Tuple[int, int]:
        """
        Computes and stores peak and average demand for a single date,
        filtering only type=IDF records from demand_serve.
        
        For today, limits to blocks up to the current block.
        """
        start_dt = datetime.combine(target_date, datetime.min.time())
        end_dt = datetime.combine(target_date, datetime.max.time())

        # Build match filter — always filter by type IDF
        match_filter: Dict[str, Any] = {
            "scheduled_at": {"$gte": start_dt, "$lte": end_dt},
            "type": "IDF",
        }

        # For today, only include blocks up to current block
        if is_today:
            current_block = self._get_current_block()
            match_filter["time_block"] = {"$lte": current_block}
            logger.info("Today mode: filtering up to block %d", current_block)

        # Aggregation pipeline — no grouping by type, single result per date
        pipeline = [
            {"$match": match_filter},
            {"$sort": {"comp_act_demand": -1}},
            {
                "$group": {
                    "_id": None,
                    "average_demand": {"$avg": "$comp_act_demand"},
                    "peak_demand": {"$max": "$comp_act_demand"},
                    "min_demand": {"$min": "$comp_act_demand"},
                    "sum_demand": {"$sum": "$comp_act_demand"},
                    "count": {"$sum": 1},
                    "peak_record_doc": {"$first": "$$ROOT"},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "average_demand": 1,
                    "peak_demand": 1,
                    "min_demand": 1,
                    "sum_demand": 1,
                    "count": 1,
                    "scheduled_at": "$peak_record_doc.scheduled_at",
                    "time_block": "$peak_record_doc.time_block",
                }
            },
        ]

        try:
            cursor = source_collection.aggregate(pipeline)
            try:
                results = await cursor.to_list(length=10000)
            finally:
                cursor.close()
        except Exception as e:
            logger.error("Error during aggregation for %s: %s", target_date.isoformat(), e)
            return 0, 0

        if not results:
            logger.info(
                "No IDF demand records found in %s for %s.",
                source_collection.name, target_date.isoformat()
            )
            return 0, 0

        result = results[0]
        scheduled_at = result.get("scheduled_at")
        time_block = result.get("time_block")
        peak_value = result.get("peak_demand")
        average_value = result.get("average_demand")
        min_value = result.get("min_demand")
        sum_demand = result.get("sum_demand")

        # Total status sum formula: sum of all 96 blocks / 1000 / 4 = sum / 4000
        total_energy_mu = (sum_demand / 4000.0) if sum_demand is not None else None

        if scheduled_at is None or time_block is None or peak_value is None or average_value is None:
            logger.warning(
                "Peak candidate for %s is missing required fields. Skipping.",
                target_date.isoformat()
            )
            return 0, 1

        # Upsert: insert if not exists, update if exists (handles 15-min cron reruns)
        try:
            entity = PeakDemandServeEntity(
                scheduled_at=scheduled_at,
                time_block=int(time_block),
                peak_demand=float(peak_value),
                average_demand=float(average_value),
                min_demand=float(min_value) if min_value is not None else None,
                total_energy_mu=float(total_energy_mu) if total_energy_mu is not None else None,
            )
            await self.peak_repo.upsert_by_date(target_date, entity)
            logger.info(
                "Upserted peak for %s: peak=%.2f MW at block %d, avg=%.2f MW",
                target_date.isoformat(), peak_value, time_block, average_value
            )
            return 1, 0
        except (ValueError, TypeError) as e:
            logger.error(
                "Error creating PeakDemandServeEntity for %s: %s",
                target_date.isoformat(), e
            )
            return 0, 1
        except Exception as e:
            logger.error(
                "Error upserting peak record for %s: %s",
                target_date.isoformat(), e
            )
            return 0, 1


peak_demand_service = PeakDemandService()