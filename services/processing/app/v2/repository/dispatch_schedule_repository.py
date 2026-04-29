"""
Dispatch Schedule Repository
Handles database operations for dispatch schedule data collection
"""
from typing import List, Dict, Any, Optional
import logging

from .base_repository import GenericRepository

logger = logging.getLogger(__name__)


class DispatchScheduleRepository(GenericRepository):
    """
    Repository for 'rtm_market_integrate_data_backup_list' collection.
    PHP Model: DispatchScheduleModel
    Collection: 'rtm_market_integrate_data_backup_list'
    """
    
    def __init__(self, collection_name: Optional[str] = None):
        super().__init__()
        self.collection_name = collection_name or "rtm_market_integrate_data_backup_list"
        logger.info(f"DispatchScheduleRepository initialized with collection: {self.collection_name}")
        self._create_collection_if_not_exists()
        self._create_indexes()
    
    def _create_collection_if_not_exists(self):
        """
        Create collection if it does not exist.
        Matches pattern used in ingestion services (libs/database/base_repository.py)
        """
        try:
            # Access db property to ensure database connection is initialized
            db = self.db
            db_name = self.db_name or "unknown"
            
            if self.collection_name not in db.list_collection_names():
                db.create_collection(self.collection_name)
                logger.info(f"Created collection '{self.collection_name}' in database '{db_name}'")
            else:
                logger.debug(f"Collection '{self.collection_name}' already exists in database '{db_name}'")
        except Exception as e:
            logger.warning(f"Error checking/creating collection '{self.collection_name}': {str(e)}")
            # Continue anyway - MongoDB will create collection on first insert
    
    def _create_indexes(self):
        """Create necessary indexes for efficient queries (matching ingestion service pattern)"""
        try:
            collection = self.collection
            
            # Index on schedule_date for date-based queries
            collection.create_index([("schedule_date", 1)])
            
            # Index on _id for sorting (already exists by default, but explicit for clarity)
            collection.create_index([("_id", 1)])
            
            logger.info(f"Created indexes for collection '{self.collection_name}'")
        except Exception as e:
            logger.warning(f"Error creating indexes for collection '{self.collection_name}': {str(e)}")
            # Continue anyway - indexes are optional for functionality
    
    @property
    def collection(self):
        """Get the MongoDB collection for this repository"""
        return self.db[self.collection_name]
    
    async def get_history_data(self, block_bid_date: str) -> List[Dict[str, Any]]:
        """
        Get history data for a specific date.
        
        PHP: $this->DispatchScheduleModel->gethistorydata($block_bid_date)
        Query: orderBy('_id', 'asc')->where('schedule_date', $date)->get()
        
        Args:
            block_bid_date: Schedule date in d-m-Y format (e.g., "01-01-2025")
                           Will be converted to YYYY-MM-DD for querying
        
        Returns:
            List of history data documents ordered by _id ascending
        """
        try:
            # Convert d-m-Y format to YYYY-MM-DD format
            # PHP uses d-m-Y format but stores in YYYY-MM-DD format typically
            # Try to parse the date format
            from datetime import datetime
            try:
                # Try parsing as d-m-Y first
                date_obj = datetime.strptime(block_bid_date, "%d-%m-%Y")
                schedule_date = date_obj.strftime("%Y-%m-%d")
            except ValueError:
                # If parsing fails, assume it's already in YYYY-MM-DD format
                schedule_date = block_bid_date
            
            query = {"schedule_date": schedule_date}
            cursor = self.collection.find(query).sort("_id", 1)  # ascending order
            results = await cursor.to_list(length=10000)
            
            # Convert ObjectId to string for JSON serialization
            for result in results:
                if "_id" in result:
                    result["_id"] = str(result["_id"])
            
            logger.info(f"Retrieved {len(results)} history data records for date {block_bid_date}")
            return results
            
        except Exception as e:
            logger.error(f"Error getting history data for date {block_bid_date}: {str(e)}")
            return []

