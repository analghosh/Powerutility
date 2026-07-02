"""
Scheduler DC SG Repository
Handles database operations for the dc_sg collection
"""

import logging
from datetime import datetime, time, timedelta
from typing import Dict, Any, List, Optional

from libs.database.base_repository import BaseRepository
from libs.models.dc_sg_entity import DCSgEntity
from ..core.config import settings

logger = logging.getLogger(__name__)


class DcSgRepository(BaseRepository):
    """Repository for the dc_sg collection"""

    def __init__(self):
        """Initialize the repository with MongoDB connection"""
        super().__init__(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE,
            collection_name="dc_sg"  # Collection name
        )

    async def create_indexes(self):
        """Create necessary indexes for the collection"""
        try:
             # Unique compound index for rapid lookup and data integrity
             await self.collection.create_index(
                [
                    ("scheduled_at", 1),
                    ("plant_id", 1),
                    ("dc_sg", 1),
                    ("time_block", 1)
                ],
                unique=True,
                name="unique_dc_sg_entry"
             )
             # Index for just date-based queries (often used in reports/processing)
             await self.collection.create_index([("scheduled_at", 1)])
             logger.info("Indexes created for dc_sg collection")
        except Exception as e:
             logger.error(f"Error creating indexes for dc_sg: {e}")

    async def find_by_unique_key(self, scheduled_at: datetime, time_block: Optional[int],
                           dc_sg: str, plant_id: str) -> Optional[Dict[str, Any]]:
        """
        Find a record by its unique key (scheduled_at, time_block, dc_sg, plant_id)
        """
        try:
            filter_criteria = {
                "scheduled_at": scheduled_at,
                "dc_sg": dc_sg,
                "plant_id": plant_id
            }

            if time_block is not None:
                filter_criteria["time_block"] = time_block

            record = await self.collection.find_one(filter_criteria)

            if record:
                logger.debug(f"Found existing dc_sg record for key: {filter_criteria}")
            else:
                logger.debug(f"No existing dc_sg record found for key: {filter_criteria}")

            return record

        except Exception as e:
            logger.error("Error finding dc_sg record by unique key: %s", str(e))
            return None

    async def update_or_insert_record(self, entity: DCSgEntity):
        """
        Update a record if it exists, otherwise insert a new one based on the unique key.
        """
        if entity.process_type:
            filter_criteria = {
                "process_type": entity.process_type,
                "time_block": entity.time_block,
                "dc_sg": entity.dc_sg,
                "plant_id": entity.plant_id
            }
        else:
            filter_criteria = {
                "scheduled_at": entity.scheduled_at,
                "time_block": entity.time_block,
                "dc_sg": entity.dc_sg,
                "plant_id": entity.plant_id
            }

        update_data = {"$set": entity.model_dump(by_alias=True, exclude_unset=True)}

        return await self.collection.update_one(filter_criteria, update_data, upsert=True)

    async def get_all_records(self, target_dates: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
        """
        Retrieve records from the dc_sg collection.
        If target_dates is provided, retrieves records within the range of [min(target_dates), max(target_dates)].
        """
        try:
            today = datetime.now().date()
            tomorrow = today + timedelta(days=1)
            day_after_tomorrow = tomorrow + timedelta(days=1)
            current_time = datetime.now().time()

            today_start = datetime.combine(today, datetime.min.time())
            tomorrow_start = datetime.combine(tomorrow, datetime.min.time())
            day_after_tomorrow_start = datetime.combine(day_after_tomorrow, datetime.min.time())

            query = {
                "scheduled_at": {"$gte": today_start, "$lt": day_after_tomorrow_start}
            }

            cursor = self.collection.find(query)
            return await cursor.to_list(length=10000)
        except Exception as e:
            logger.error("Error retrieving records from dc_sg: %s", str(e))
            return []

    async def delete_all_records(self) -> int:
        """
        Deletes all records from the dc_sg collection.
        """
        try:
            result = await self.collection.delete_many({})
            logger.info(f"Deleted {result.deleted_count} records from dc_sg collection.")
            return result.deleted_count
        except Exception as e:
            logger.error("Error deleting all records from dc_sg: %s", str(e))
            return 0

    async def get_records_by_date(self, date_val: datetime.date, plant_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Get all records for a specific date, optionally filtered by plant_ids.
        """
        try:
            start = datetime.combine(date_val, datetime.min.time())
            end = datetime.combine(date_val, datetime.max.time())

            query = {"scheduled_at": {"$gte": start, "$lte": end}}
            if plant_ids:
                query["plant_id"] = {"$in": plant_ids}

            count = await self.collection.count_documents(query)
            logger.info(f"Found {count} records for date {date_val}")
            cursor = self.collection.find(query)
            return await cursor.to_list(length=10000)
        except Exception as e:
            logger.error(f"Error getting records by date {date_val}: {str(e)}")
            return []

    async def get_last_available_date(self, plant_id: str) -> Optional[datetime.date]:
        """
        Get the last available date for a specific plant.
        """
        try:
            record = await self.collection.find_one(
                {"plant_id": plant_id},
                sort=[("scheduled_at", -1)]
            )
            if record and "scheduled_at" in record:
                return record["scheduled_at"].date()
            return None
        except Exception as e:
            logger.error(f"Error getting last available date for plant {plant_id}: {str(e)}")
            return None

    async def get_latest_records_per_timeblock(
        self, plant_id: str, before_date: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """
        Get the latest record for each time_block and dc_sg combination for a specific plant.

        When before_date is given, only records scheduled strictly before that date are
        considered, so future-dated schedules (e.g. a new generator sharing data from
        tomorrow onwards) are never rolled back into an earlier day.
        """
        try:
            match_stage: Dict[str, Any] = {"plant_id": plant_id}
            if before_date is not None:
                match_stage["scheduled_at"] = {"$lt": before_date}
            pipeline = [
                {"$match": match_stage},
                {"$sort": {"scheduled_at": -1}},
                {
                    "$group": {
                        "_id": {
                            "time_block": "$time_block",
                            "dc_sg": "$dc_sg"
                        },
                        "latest_record": {"$first": "$$ROOT"}
                    }
                },
                {"$replaceRoot": {"newRoot": "$latest_record"}}
            ]

            cursor = self.collection.aggregate(pipeline)
            return await cursor.to_list(length=10000)
        except Exception as e:
            logger.error(f"Error getting latest records per timeblock for plant {plant_id}: {str(e)}")
            return []

    async def bulk_upsert_dc_sg(self, entities: List[DCSgEntity]) -> Dict[str, Any]:
        """
        Bulk upsert DC/SG entities using the optimized base method.
        """
        records = [e.model_dump(by_alias=False) for e in entities]

        unique_key_fields = ["scheduled_at", "time_block", "dc_sg", "plant_id"]

        BATCH_SIZE = 2000
        total_results = {
            "success": True,
            "inserted_count": 0,
            "modified_count": 0,
            "upserted_count": 0,
            "matched_count": 0,
            "total_processed": 0
        }

        for i in range(0, len(records), BATCH_SIZE):
            batch = records[i:i + BATCH_SIZE]
            try:
                batch_result = await self.bulk_upsert_optimized(batch, unique_key_fields)

                if not batch_result.get("success"):
                     logger.warning(f"Batch {i//BATCH_SIZE + 1} failed: {batch_result.get('error')}")
                     total_results["success"] = False

                total_results["inserted_count"] += batch_result.get("inserted_count", 0)
                total_results["modified_count"] += batch_result.get("modified_count", 0)
                total_results["upserted_count"] += batch_result.get("upserted_count", 0)
                total_results["matched_count"] += batch_result.get("matched_count", 0)
                total_results["total_processed"] += batch_result.get("total_processed", 0)

            except Exception as e:
                logger.error(f"Error processing batch {i} in bulk_upsert_dc_sg: {e}")
                total_results["success"] = False

        return total_results

    async def find_by_unique_keys(self, keys: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Find multiple records by their unique keys in a single query.
        """
        if not keys:
            return []

        chunk_size = 1000
        all_records = []

        for i in range(0, len(keys), chunk_size):
            chunk = keys[i:i + chunk_size]
            query = {"$or": chunk}
            try:
                cursor = self.collection.find(query)
                found = await cursor.to_list(length=10000)
                all_records.extend(found)
            except Exception as e:
                logger.error(f"Error in find_by_unique_keys chunk {i}: {str(e)}")

        return all_records


# Create repository instance
dc_sg_repository = DcSgRepository()
