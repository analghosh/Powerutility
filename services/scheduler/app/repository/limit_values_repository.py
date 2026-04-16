"""
Limit Values Repository for Scheduler Service
Handles limit_values collection access for TM, SEZ, MLTP, and other limit values
"""
from typing import List, Dict, Any, Optional
import logging
from datetime import datetime, date, timedelta

from services.processing.app.v2.repository.base_repository import GenericRepository
from ..core.config import settings

logger = logging.getLogger(__name__)


class LimitValuesRepository(GenericRepository):
    """
    Repository for limit_values collection.
    Handles TM (Technical Minimum), SEZ, MLTP (Multiplier), and other limit values.
    """

    def __init__(self, collection_name: str = "limit_values"):
        super().__init__()
        self.collection_name = collection_name
        logger.info(f"LimitValuesRepository initialized with collection: {self.collection_name}")

    async def get_data(
        self,
        start_date,  # Can be str or date
        end_date,    # Can be str or date
        market: str,
        id: str,
        area: str
    ) -> List[Dict[str, Any]]:
        """
        Get limit values data asynchronously.
        Accepts dates as either strings (YYYY-MM-DD) or date objects.
        """
        # Convert strings to date objects if needed
        if isinstance(start_date, str):
            start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        if isinstance(end_date, str):
            end_date = datetime.strptime(end_date, "%Y-%m-%d").date()

        # Convert date objects to datetime if necessary
        if isinstance(start_date, date) and not isinstance(start_date, datetime):
            start_date = datetime.combine(start_date, datetime.min.time())
        if isinstance(end_date, date) and not isinstance(end_date, datetime):
            end_date = datetime.combine(end_date, datetime.min.time())

        try:
            filter_query = {
                "scheduled_at": {"$gte": start_date, "$lt": end_date}
            }

            # Add optional filters
            if market and market != "":
                filter_query["market"] = market
            if id and id != "":
                filter_query["category"] = id
            if area and area != "" and area != "NA":
                filter_query["area"] = area

            cursor = self.find(self.collection_name, filter_query).sort({"scheduled_at": 1})
            result = await cursor.to_list(length=None)
            logger.info(f"Retrieved {len(result)} limit values for market={market}, ID={id}, area={area}")
            return result
        except Exception as e:
            logger.error(f"Failed to retrieve limit values: {str(e)}")
            return []

    async def get_data_byID_with_latest_date(
        self,
        ID: str,
        market: str,
        category: str,
        area: str,
        scheduled_at: datetime = None
    ) -> List[Dict[str, Any]]:
        """
        Get limit values by ID with latest date asynchronously.
        """
        tomorrow_date = datetime.now() + timedelta(days=1)
        try:
            match_filter = {
                "scheduled_at": {"$gte": scheduled_at, "$lt": tomorrow_date},
                "id": ID,
                "market": market
            }

            if category and category != "":
                match_filter["category"] = category
            if area and area != "" and area != "NA":
                match_filter["area"] = area

            cursor = self.find(self.collection_name, match_filter).sort(
                [("scheduled_at", 1), ("time_block", 1)]
            )
            result = await cursor.to_list(length=None)

            logger.info(f"Retrieved {len(result)} limit values for ID={ID}, latest_date={scheduled_at}")
            return result
        except Exception as e:
            logger.error(f"Failed to retrieve limit values by ID: {str(e)}")
            return []

    async def get_by_plant_and_block(
        self,
        schedule_date: str,
        plant_id: str,
        time_block: int,
        ID: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get limit value for a specific plant and time block.
        """
        try:
            filter_query = {
                "scheduled_at": datetime.strptime(schedule_date, "%Y-%m-%d"),
                "id": plant_id,
                "block": time_block,
                "category": ID
            }

            result = await self.find_one(self.collection_name, filter_query)
            return result
        except Exception as e:
            logger.error(f"Failed to get limit value for plant={plant_id}, block={time_block}: {str(e)}")
            return None

    async def ensure_historical_collection(self):
        """Create limit_values_historical as a time-series collection if it doesn't exist"""
        hist_collection_name = f"{self.collection_name}_historical"
        try:
            existing_names = await self.db.list_collection_names()
            if hist_collection_name not in existing_names:
                await self.db.create_collection(
                    hist_collection_name,
                    timeseries={
                        "timeField": "scheduled_at",
                        "metaField": "metadata",
                        "granularity": "hours",
                    },
                )
                logger.info(f"Created time-series collection: {hist_collection_name}")
            return hist_collection_name
        except Exception as e:
            logger.error(f"Failed to ensure historical collection: {e}")
            return None

    async def archive_to_historical(self, today: datetime) -> Dict[str, Any]:
        """
        Move records older than today to the historical time-series collection.
        """
        try:
            hist_collection_name = await self.ensure_historical_collection()
            if not hist_collection_name:
                return {"archived": 0, "deleted": 0, "error": "No historical collection"}

            # 1. Fetch records older than today
            query = {"scheduled_at": {"$lt": today}}
            cursor = self.db[self.collection_name].find(query)
            candidates = await cursor.to_list(length=None)

            if not candidates:
                logger.info("No records found for archival")
                return {"archived": 0, "deleted": 0}

            # 2. Map to time-series format
            to_insert = []
            for doc in candidates:
                metadata = {
                    "block": doc.get("block"),
                    "id": doc.get("id"),
                    "market": doc.get("market"),
                    "category": doc.get("category"),
                    "area": doc.get("area")
                }

                hist_doc = {
                    "scheduled_at": doc.get("scheduled_at"),
                    "metadata": metadata,
                    "values": doc.get("values"),
                    "unit": doc.get("unit"),
                    "inserted_at": doc.get("inserted_at"),
                    "created_by": doc.get("created_by"),
                    "updated_by": doc.get("updated_by"),
                    "updated_at": doc.get("updated_at"),
                    "archived_at": datetime.now()
                }
                to_insert.append(hist_doc)

            # 3. Bulk insert to historical
            inserted_ids = []
            if to_insert:
                result = await self.db[hist_collection_name].insert_many(to_insert, ordered=False)
                inserted_ids = result.inserted_ids
                logger.info(f"Archived {len(inserted_ids)} records to {hist_collection_name}")

            # 4. Delete from main collection
            delete_result = await self.db[self.collection_name].delete_many(query)
            logger.info(f"Deleted {delete_result.deleted_count} archived records from {self.collection_name}")

            return {
                "archived": len(inserted_ids),
                "deleted": delete_result.deleted_count,
                "status": "success"
            }
        except Exception as e:
            logger.error(f"Archival process failed: {e}")
            return {"archived": 0, "deleted": 0, "error": str(e)}

    def bulk_insert_only(self, collection_name: str, documents: List[Dict[str, Any]], unique_key_fields: List[str]) -> Dict[str, Any]:
        """
        Insert documents only if they don't already exist. Never updates existing records.
        Uses $setOnInsert so matched documents are completely untouched.
        """
        from pymongo import UpdateOne
        if not documents:
            return {"inserted": 0, "skipped": 0, "total": 0}

        collection = self.db[collection_name]
        operations = []
        current_time = datetime.now()

        for doc in documents:
            try:
                filter_query = {field: doc[field] for field in unique_key_fields}
            except KeyError as e:
                logger.warning(f"Document missing unique key field {e}, skipping")
                continue

            insert_data = doc.copy()
            insert_data.pop("_id", None)
            insert_data.setdefault("created_at", current_time)
            insert_data.setdefault("inserted_at", current_time)

            operations.append(UpdateOne(
                filter_query,
                {"$setOnInsert": insert_data},
                upsert=True
            ))

        if not operations:
            return {"inserted": 0, "skipped": 0, "total": 0}

        batch_size = 1000
        total_stats = {"inserted": 0, "skipped": 0, "total": 0}

        try:
            for i in range(0, len(operations), batch_size):
                batch_ops = operations[i:i + batch_size]
                result = collection.bulk_write(batch_ops, ordered=False)
                total_stats["inserted"] += result.upserted_count
                total_stats["skipped"] += result.matched_count
                total_stats["total"] += len(batch_ops)

            logger.info(f"Bulk insert-only result for {collection_name}: {total_stats}")
            return total_stats
        except Exception as e:
            logger.error(f"Error in bulk_insert_only for {collection_name}: {e}")
            raise

    def get_approved_tomorrow(self, tomorrow: datetime) -> List[Dict[str, Any]]:
        """
        Fetch approved entries for tomorrow from the approval collection.
        """
        try:
            approval_collection = "limit_approval"
            query = {
                "scheduled_at": tomorrow,
                "status": "APPROVED"
            }
            cursor = self.db[approval_collection].find(query)
            results = await cursor.to_list(length=None)
            logger.info(f"Found {len(results)} approved records for tomorrow ({tomorrow.strftime('%Y-%m-%d')})")
            return results
        except Exception as e:
            logger.error(f"Failed to fetch approved records for tomorrow: {e}")
            return []
