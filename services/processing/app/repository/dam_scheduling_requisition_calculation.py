# repositories/dam_scheduling_requisition_calculation.py
from typing import List, Dict, Any, Optional
import logging

from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


class DAMSchedulingRequisitionCalculationRepository(BaseRepository):
    """
    Repository for the 'dam_scheduling_requisition_calculation' collection.
    Handles dayahead market scheduling requisition calculation operations.
    """

    def __init__(self, collection_name: str = "dam_scheduling_requisition_calculation"):
        super().__init__(collection_name=collection_name)
        logger.info(f"DAMSchedulingRequisitionCalculationRepository initialized with collection: {collection_name}")

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

    async def get_calculation_summary(self, schedule_date: str) -> Dict[str, Any]:
        """
        Get calculation summary for a specific schedule date.
        """
        try:
            pipeline = [
                {"$match": {"schedule_date": schedule_date}},
                {"$group": {
                    "_id": None,
                    "total_volume": {"$sum": "$volume"},
                    "total_price": {"$sum": "$price"},
                    "count": {"$sum": 1}
                }}
            ]
            cursor = self.collection.aggregate(pipeline)
            try:
                result = list(cursor)
            finally:
                cursor.close()
            return result[0] if result else {}
        except Exception as e:
            logger.error(f"Failed to get calculation summary in {self.collection_name}: {str(e)}")
            raise

    async def get_by_plant_and_date(self, plant_id: str, schedule_date: str) -> List[Dict[str, Any]]:
        """
        Get calculations by plant ID and schedule date.
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
            logger.error(f"Failed to get calculations by plant and date in {self.collection_name}: {str(e)}")
            raise

    async def insert_calculation(self, calculation_data: Dict[str, Any]) -> str:
        """
        Insert a new calculation record.
        """
        try:
            result = self.collection.insert_one(calculation_data)
            return str(result.inserted_id)
        except Exception as e:
            logger.error(f"Failed to insert calculation in {self.collection_name}: {str(e)}")
            raise

    async def bulk_insert_calculations(self, calculations: List[Dict[str, Any]]) -> List[str]:
        """
        Bulk insert calculation records.
        """
        try:
            result = self.collection.insert_many(calculations)
            return [str(_id) for _id in result.inserted_ids]
        except Exception as e:
            logger.error(f"Failed to bulk insert calculations in {self.collection_name}: {str(e)}")
            raise
