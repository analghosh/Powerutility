"""
DC SG Renewable Repository
Handles database operations for the dc_sg_renewable staging collection.

Role: Intermediate staging layer for renewable data.
      raw → dc_sg_renewable (upsert) → renewable_serve (upsert)

Unique key: (scheduled_at, plant_id, time_block, type)
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from libs.database.base_repository import BaseRepository
from libs.models.dc_sg_renewable_entity import DcSgRenewableEntity
from ..core.config import settings

logger = logging.getLogger(__name__)


class DcSgRenewableRepository(BaseRepository):
    """Repository for the dc_sg_renewable staging collection."""

    def __init__(self):
        super().__init__(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE,
            collection_name=getattr(settings, "MONGODB_DC_SG_RENEWABLE_COLLECTION", "dc_sg_renewable")
        )
        self.create_indexes()

    def create_indexes(self):
        """Create indexes for the dc_sg_renewable collection."""
        try:
            # Unique compound index — unique key for upsert operations
            self.collection.create_index(
                [
                    ("scheduled_at", 1),
                    ("plant_id", 1),
                    ("time_block", 1),
                    ("type", 1),
                ],
                unique=True,
                name="unique_dc_sg_renewable_key"
            )
            # Supporting index for date-range scans
            self.collection.create_index([("scheduled_at", 1)], name="idx_dc_sg_renewable_scheduled_at")
            logger.info("Indexes created for dc_sg_renewable collection")
        except Exception as e:
            logger.error("Error creating indexes for dc_sg_renewable: %s", str(e))

    # ------------------------------------------------------------------
    # Write helpers
    # ------------------------------------------------------------------

    def bulk_upsert(self, entities: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Bulk upsert records into dc_sg_renewable.
        Unique key: scheduled_at, plant_id, time_block, type
        """
        if not entities:
            return {"inserted_count": 0, "modified_count": 0, "upserted_count": 0, "total_processed": 0}

        unique_key_fields = ["scheduled_at", "plant_id", "time_block", "type"]

        BATCH_SIZE = 2000
        totals = {"success": True, "inserted_count": 0, "modified_count": 0,
                  "upserted_count": 0, "matched_count": 0, "total_processed": 0}

        for i in range(0, len(entities), BATCH_SIZE):
            batch = entities[i:i + BATCH_SIZE]
            try:
                result = self.bulk_upsert_optimized(batch, unique_key_fields)
                if not result.get("success"):
                    logger.warning("dc_sg_renewable batch %d failed: %s", i // BATCH_SIZE + 1, result.get("error"))
                    totals["success"] = False
                totals["inserted_count"] += result.get("inserted_count", 0)
                totals["modified_count"] += result.get("modified_count", 0)
                totals["upserted_count"] += result.get("upserted_count", 0)
                totals["matched_count"] += result.get("matched_count", 0)
                totals["total_processed"] += result.get("total_processed", 0)
            except Exception as e:
                logger.error("Error in dc_sg_renewable bulk_upsert batch %d: %s", i, str(e))
                totals["success"] = False

        return totals

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    async def find_by_date(self, target_date) -> List[Dict[str, Any]]:
        """Return all records for a given date."""
        try:
            start = datetime.combine(target_date, datetime.min.time())
            end = datetime.combine(target_date, datetime.max.time())
            return await self.collection.find({"scheduled_at": {"$gte": start, "$lte": end}}).to_list(length=10000)
        except Exception as e:
            logger.error("Error in dc_sg_renewable find_by_date: %s", str(e))
            return []

    async def find_by_date_range(self, start_date, end_date) -> List[Dict[str, Any]]:
        """Return all records within an inclusive date range."""
        try:
            start = datetime.combine(start_date, datetime.min.time())
            end = datetime.combine(end_date, datetime.max.time())
            return await self.collection.find({"scheduled_at": {"$gte": start, "$lte": end}}).to_list(length=10000)
        except Exception as e:
            logger.error("Error in dc_sg_renewable find_by_date_range: %s", str(e))
            return []

    async def get_latest_record_per_constraint(self) -> List[Dict[str, Any]]:
        """
        For each unique (time_block, plant_id, type) combination, return the most recent record.
        Used by the carry-forward job to populate missing records on a new day.
        """
        try:
            pipeline = [
                {"$sort": {"scheduled_at": -1}},
                {
                    "$group": {
                        "_id": {
                            "time_block": "$time_block",
                            "plant_id": "$plant_id",
                            "type": "$type",
                        },
                        "latest": {"$first": "$$ROOT"},
                    }
                },
                {"$replaceRoot": {"newRoot": "$latest"}},
            ]
            cursor = self.collection.aggregate(pipeline)
            return await cursor.to_list(length=10000)
        except Exception as e:
            logger.error("Error in dc_sg_renewable get_latest_record_per_constraint: %s", str(e))
            return []

    async def get_existing_keys_for_date(self, target_date) -> set:
        """
        Return a set of (time_block, plant_id, type) tuples that already exist for target_date.
        """
        records = await self.find_by_date(target_date)
        return {(r.get("time_block"), r.get("plant_id"), r.get("type")) for r in records}

    # ------------------------------------------------------------------
    # Day carry-forward logic
    # ------------------------------------------------------------------

    def carry_forward_for_date(self, target_date) -> Dict[str, Any]:
        """
        For each unique (time_block, plant_id, type) that has NO record on target_date,
        carry the most recent existing record forward to target_date.

        This handles day-change gaps: if yesterday had N unique constraints and some
        (or all) haven't been received yet for today, we seed today with yesterday's values.

        Args:
            target_date: datetime.date — the date to fill

        Returns:
            Dict with carried_count and skipped_count.
        """
        try:
            target_start = datetime.combine(target_date, datetime.min.time())

            # Keys that already exist on target_date
            existing_keys = self.get_existing_keys_for_date(target_date)

            # Latest record per constraint from all historical data
            latest_records = self.get_latest_record_per_constraint()

            carry_docs = []
            skipped = 0

            for record in latest_records:
                key = (record.get("time_block"), record.get("plant_id"), record.get("type"))

                # Skip if already present for target_date
                if key in existing_keys:
                    skipped += 1
                    continue

                # Skip records that are already from target_date (shouldn't happen given
                # get_latest_record_per_constraint was called without date filter, but safe check)
                record_date = record.get("scheduled_at")
                if isinstance(record_date, datetime) and record_date.date() == target_date:
                    skipped += 1
                    continue

                new_doc = {k: v for k, v in record.items() if k != "_id"}
                new_doc["scheduled_at"] = target_start
                new_doc["carry_source_date"] = record.get("scheduled_at")
                new_doc["carried_forward"] = True
                new_doc["updated_at"] = datetime.now()
                new_doc["inserted_at"] = datetime.now()
                # Reset system revision for the carried record
                new_doc["system_revision_number"] = 1

                carry_docs.append(new_doc)

            if not carry_docs:
                logger.info(
                    "dc_sg_renewable carry_forward: no gaps found for %s (%d already present, %d skipped)",
                    target_date, len(existing_keys), skipped
                )
                return {"carried_count": 0, "skipped_count": skipped}

            result = self.bulk_upsert(carry_docs)
            carried = result.get("upserted_count", 0) + result.get("inserted_count", 0)
            logger.info(
                "dc_sg_renewable carry_forward: carried %d records to %s (skipped %d)",
                carried, target_date, skipped
            )
            return {"carried_count": carried, "skipped_count": skipped}

        except Exception as e:
            logger.error("Error in dc_sg_renewable carry_forward_for_date: %s", str(e))
            return {"carried_count": 0, "skipped_count": 0, "error": str(e)}


# Singleton instance
dc_sg_renewable_repository = DcSgRenewableRepository()
