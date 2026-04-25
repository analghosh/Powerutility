"""
DAM Book Repository
Handles database operations for DAM Single Bid Book collection
"""
from typing import List, Dict, Any, Optional
import logging
from datetime import datetime

from .base_repository import GenericRepository

logger = logging.getLogger(__name__)

class DamBookRepository(GenericRepository):
    """
    Repository for 'dam_book_collection'.
    """
    
    def __init__(self):
        super().__init__()
        # Explicitly using the collection name from Scheduler config
        self.collection_name = "dam_book_collection" 
        logger.info(f"DamBookRepository initialized with collection: {self.collection_name}")
        self._create_collection_if_not_exists()
        
    def _create_collection_if_not_exists(self):
        try:
            db = self.db
            if self.collection_name not in db.list_collection_names():
                db.create_collection(self.collection_name)
                logger.info(f"Created collection '{self.collection_name}'")
        except Exception as e:
            logger.warning(f"Error checking/creating collection '{self.collection_name}': {str(e)}")

    @property
    def collection(self):
        return self.db[self.collection_name]

    async def get_book_by_delivery_date(self, delivery_date: int) -> List[Dict[str, Any]]:
        """
        Get all bids for a specific delivery date (integer format YYYYMMDD).
        """
        try:
            # The scheduler stores DeliveryDate as int (e.g., 20250208)
            query = {"DeliveryDate": delivery_date}
            cursor = self.collection.find(query, {"_id": 0}) # Exclude _id
            results = await cursor.to_list(length=10000)
            logger.info(f"Retrieved {len(results)} DAM book entries for date {delivery_date}")
            return results
        except Exception as e:
            logger.error(f"Error getting DAM book for date {delivery_date}: {str(e)}")
            return []

# Singleton
dam_book_repository = DamBookRepository()
