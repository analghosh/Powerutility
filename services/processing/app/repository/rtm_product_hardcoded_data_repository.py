# repositories/rtm_product_hardcoded_data_repository.py
from typing import List, Dict, Any, Optional
import logging

from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


class RTMProductHardCodedDataRepository(BaseRepository):
    """
    Repository for RTM product hardcoded data collection.
    Uses standard field names following industry practice.
    """

    def __init__(self, collection_name: str = "rtm_product_hardCoded_data"):
        super().__init__(collection_name=collection_name)
        logger.info(f"RTMProductHardCodedDataRepository initialized with collection: {collection_name}")

    def get_all_data(self) -> List[Dict[str, Any]]:
        """
        Get all RTM product data, sorted by product_code.
        """
        try:
            pipeline = [
                {"$sort": {"Product_Code": 1}},
                {"$project": {"_id": 0}}
            ]
            result = self.aggregate(pipeline)
            logger.info(f"Retrieved {len(result)} RTM product records")
            return result
        except Exception as e:
            logger.error(f"Failed to retrieve RTM product data: {str(e)}")
            return []

    def get_by_session_id(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Get RTM product data by session ID.
        
        Args:
            session_id: Session ID to search for
            
        Returns:
            Document matching the session ID
        """
        try:
            filter_query = {"Session_Id": session_id}
            projection = {"_id": 0}
            
            result = self.find_one(filter_query, projection)
            return result
        except Exception as e:
            logger.error(f"Failed to get session data for {session_id}: {str(e)}")
            return None