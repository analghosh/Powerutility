"""
RTM Product Hardcoded Data Repository for Scheduler Service
Handles RTM product hardcoded data collection access
"""
from typing import List, Dict, Any, Optional
import logging

from services.processing.app.v2.repository.base_repository import GenericRepository
from ..core.config import settings

logger = logging.getLogger(__name__)


class RTMProductHardCodedDataRepository(GenericRepository):
    """
    Repository for RTM product hardcoded data collection.
    Supports both new standard field names and legacy field names.
    """

    def __init__(self, collection_name: str = "rtm_product_hardCoded_data"):
        super().__init__()
        self.collection_name = collection_name
        logger.info(f"RTMProductHardCodedDataRepository initialized with collection: {self.collection_name}")

    async def get_Data(self) -> List[Dict[str, Any]]:
        """
        Get all RTM product hardcoded data asynchronously.
        Returns standardized field names.
        Maps legacy fields to standard field names in response.
        """
        try:
            pipeline = [
                {
                    "$addFields": {
                        "sort_key": {"$ifNull": ["$product_code", "$Product_Code"]}
                    }
                },
                {"$sort": {"sort_key": 1}},
                {
                    "$project": {
                        "_id": 0,
                        "product_code": {"$ifNull": ["$product_code", "$Product_Code"]},
                        "from_period_code": {"$ifNull": ["$from_period_code", "$From_Period_Code"]},
                        "to_period_code": {"$ifNull": ["$to_period_code", "$To_Period_Code"]},
                        "from_period_time": {"$ifNull": ["$from_period_time", "$From_Period_Id"]},
                        "to_period_time": {"$ifNull": ["$to_period_time", "$To_Period_Id"]},
                        "delivery_type": {"$ifNull": ["$delivery_type", "$Delivery_Date"]},
                        "session_number": {"$ifNull": ["$session_number", "$Session_Id"]}
                    }
                }
            ]
            result = await self.aggregate(self.collection_name, pipeline)
            logger.info(f"Retrieved {len(result)} RTM product records")
            return result
        except Exception as e:
            logger.error(f"Failed to retrieve RTM product data: {str(e)}")
            return []

    async def get_session_mapping(self) -> Dict[int, Dict[str, Any]]:
        """
        Get session data mapped by time_block (from_period_code).
        Returns a dictionary keyed by time_block with session information.
        """
        try:
            session_data_raw = await self.get_Data()
            session_mapping = {}

            for session_item in session_data_raw:
                time_block = session_item.get("from_period_code") or session_item.get("From_Period_Code")
                session_number = session_item.get("session_number") or session_item.get("Session_Id")

                if time_block is not None:
                    session_mapping[time_block] = {
                        "session_number": session_number,
                        "Session_Id": session_number,  # Keep both for backward compatibility
                        "product_code": session_item.get("product_code") or session_item.get("Product_Code"),
                        "from_period_time": session_item.get("from_period_time") or session_item.get("From_Period_Id"),
                        "to_period_time": session_item.get("to_period_time") or session_item.get("To_Period_Id"),
                        "delivery_type": session_item.get("delivery_type") or session_item.get("Delivery_Date")
                    }

            logger.info(f"Created session mapping for {len(session_mapping)} time blocks")
            return session_mapping
        except Exception as e:
            logger.error(f"Failed to get session mapping: {str(e)}")
            return {}
