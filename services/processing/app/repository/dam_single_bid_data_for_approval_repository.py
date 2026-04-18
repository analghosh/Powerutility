# repositories/dam_single_bid_data_for_approval_repository.py
from typing import List, Dict, Any, Optional
import logging
from datetime import datetime

from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


class DAMSingleBidDataForApprovalRepository(BaseRepository):
    """
    Repository for the 'dam_single_bid_data_for_approval' collection.
    Handles dayahead market single bid data for approval operations.
    """

    def __init__(self, collection_name: str = "dam_single_bid_data_for_approval"):
        super().__init__(collection_name=collection_name)
        logger.info(f"DAMSingleBidDataForApprovalRepository initialized with collection: {collection_name}")

    # ---- Generic passthrough used by the service layer ----
    async def insert_many(self, documents: List[Any]) -> Dict[str, Any]:
        """
        Insert multiple documents into the collection asynchronously.
        Accepts any list of dicts or simple values (int, str, etc.) and converts them to dicts.
        Returns {"data": True} if successful.
        """
        try:
            documents = list(documents.values())
            documents = [doc for doc in documents if isinstance(doc, dict)]
            result = self.collection.insert_many(documents, ordered=False)
            inserted_ids = [str(_id) for _id in result.inserted_ids]
            return inserted_ids
        except Exception as e:
            logger.error(f"Failed to insert documents in {self.collection_name}: {str(e)}")
            raise

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

    async def getPlantDataForApprovalbytime(self, date: str, timeBlock: str) -> int:
        """
        Get the latest revision number for a specific date and time block.
        """
        try:
            pipeline = [
                {"$match": {"inserted_date": date, "start_time": timeBlock}},
                {"$sort": {"revision_no": -1}},
                {"$limit": 1},
                {"$project": {"_id": 0, "revision_no": 1}}
            ]

            cursor = self.collection.aggregate(pipeline)

            try:
                result = cursor.to_list(length=None)
            except AttributeError:
                # Fallback for PyMongo CommandCursor in server environment
                result = list(cursor)

            if result:
                return result[0].get("revision_no", 0)
            return 0
        except Exception as e:
            logger.error(f"Failed to get plant data for approval by time in {self.collection_name}: {str(e)}")
            return 0

    def update_one(self, filter_query: Dict[str, Any], update_query: Dict[str, Any]) -> Any:
        """
        Update one document matching the filter with the update query.
        Returns the result object with modified_count attribute.
        """
        try:
            result = self.collection.update_one(filter_query, update_query)
            return result
        except Exception as e:
            logger.error(f"Failed to update document in {self.collection_name}: {str(e)}")
            raise
