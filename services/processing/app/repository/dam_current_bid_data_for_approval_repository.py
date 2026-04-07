# repositories/dam_current_bid_data_for_approval_repository.py
from typing import List, Dict, Any, Optional
import logging
from datetime import datetime

from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


class DAMCurrentBidDataForApprovalRepository(BaseRepository):
    """
    Repository for the 'dam_current_bid_data_for_approval' collection.
    Handles dayahead market current bid data for approval operations.
    """

    def __init__(self, collection_name: str = "dam_current_bid_data_for_approval"):
        super().__init__(collection_name=collection_name)
        logger.info(f"DAMCurrentBidDataForApprovalRepository initialized with collection: {collection_name}")

    async def find_by_schedule_date(self, schedule_date: str) -> List[Dict[str, Any]]:
        """
        Find documents by schedule date.
        """
        try:
            query = {"schedule_date": schedule_date}
            cursor = self.collection.find(query)
            try:
                result = list(cursor)
            finally:
                cursor.close()
            return result
        except Exception as e:
            logger.error(f"Failed to find documents by schedule_date in {self.collection_name}: {str(e)}")
            raise

    async def find_by_time_block_range(self, schedule_date: str, from_block: int, to_block: int) -> List[Dict[str, Any]]:
        """
        Find documents by schedule date and time block range.
        """
        try:
            query = {
                "schedule_date": schedule_date,
                "time_block": {"$gte": from_block, "$lte": to_block}
            }
            cursor = self.collection.find(query)
            try:
                result = list(cursor)
            finally:
                cursor.close()
            return result
        except Exception as e:
            logger.error(f"Failed to find documents by time block range in {self.collection_name}: {str(e)}")
            raise

    async def find_by_plant_and_date(self, plant_id: str, schedule_date: str) -> List[Dict[str, Any]]:
        """
        Find documents by plant ID and schedule date.
        """
        try:
            query = {
                "plant_id": plant_id,
                "schedule_date": schedule_date
            }
            cursor = self.collection.find(query)
            try:
                result = list(cursor)
            finally:
                cursor.close()
            return result
        except Exception as e:
            logger.error(f"Failed to find documents by plant and date in {self.collection_name}: {str(e)}")
            raise

    async def update_bid_status(self, bid_id: str, status: str, approved_by: str = None) -> bool:
        """
        Update bid status for a specific bid.
        """
        try:
            query = {"_id": bid_id}
            update_data = {"bid_status": status}
            if approved_by:
                update_data["approved_by"] = approved_by
                update_data["approved_at"] = datetime.now()
            
            result = self.collection.update_one(query, {"$set": update_data})
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Failed to update bid status in {self.collection_name}: {str(e)}")
            raise

    async def get_pending_approvals(self, schedule_date: str) -> List[Dict[str, Any]]:
        """
        Get all pending approvals for a specific schedule date.
        """
        try:
            query = {
                "schedule_date": schedule_date,
                "bid_status": {"$in": ["pending", "submitted"]}
            }
            cursor = self.collection.find(query)
            try:
                result = list(cursor)
            finally:
                cursor.close()
            return result
        except Exception as e:
            logger.error(f"Failed to get pending approvals in {self.collection_name}: {str(e)}")
            raise

    async def bulk_update_status(self, bid_ids: List[str], status: str, approved_by: str = None) -> int:
        """
        Bulk update bid status for multiple bids.
        """
        try:
            query = {"_id": {"$in": bid_ids}}
            update_data = {"bid_status": status}
            if approved_by:
                update_data["approved_by"] = approved_by
                update_data["approved_at"] = datetime.now()
            
            result = self.collection.update_many(query, {"$set": update_data})
            return result.modified_count
        except Exception as e:
            logger.error(f"Failed to bulk update bid status in {self.collection_name}: {str(e)}")
            raise

    async def getData(self, time_block: int, schedule_date: str) -> int:
        """
        Check if data exists for a given time_block and schedule_date.
        Returns 1 if exists, 0 if not.
        """
        try:
            result = self.collection.find_one({"time_block": time_block, "schedule_date": schedule_date})
            if result:
                return 1
            else:
                return 0
        except Exception as e:
            logger.error(f"Failed to get data in {self.collection_name}: {str(e)}")
            return 0

    async def deleteData(self, time_block: int, schedule_date: str):
        """
        Delete data for a given time_block and schedule_date.
        """
        try:
            result = self.collection.delete_one({"time_block": time_block, "schedule_date": schedule_date})
            return result
        except Exception as e:
            logger.error(f"Failed to delete data in {self.collection_name}: {str(e)}")
            raise

    async def insert_many(self, documents: List[Dict[str, Any]]) -> List[str]:
        """
        Insert multiple documents into the collection.
        Returns list of inserted IDs.
        """
        try:
            if not documents:
                return []
            
            # Add timestamps to each document
            for doc in documents:
                doc["created_at"] = datetime.now()
                doc["updated_at"] = datetime.now()
            
            result = self.collection.insert_many(documents, ordered=False)
            inserted_ids = [str(_id) for _id in result.inserted_ids]
            logger.info(f"Inserted {len(inserted_ids)} documents in {self.collection_name}")
            return inserted_ids
        except Exception as e:
            logger.error(f"Failed to insert documents in {self.collection_name}: {str(e)}")
            raise
