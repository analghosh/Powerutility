"""
DAM Trade Book Response Repository
Handles database operations for DAM trade book response collection
"""
from typing import List, Dict, Any, Optional
import logging

from .base_repository import GenericRepository

logger = logging.getLogger(__name__)


class DAMTradeBookResponseRepository(GenericRepository):
    """
    Repository for 'dam_trade_book_response' collection.
    PHP Model: DamTradeBookResponse
    Collection: 'dam_trade_book_response'
    """
    
    def __init__(self, collection_name: Optional[str] = None):
        super().__init__()
        self.collection_name = collection_name or "dam_trade_book_response"
        logger.info(f"DAMTradeBookResponseRepository initialized with collection: {self.collection_name}")
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
            
            # Index on from_period_code for period-based queries
            collection.create_index([("from_period_code", 1)])
            
            # Compound index for date and period queries
            collection.create_index([("schedule_date", 1), ("from_period_code", 1)])
            
            logger.info(f"Created indexes for collection '{self.collection_name}'")
        except Exception as e:
            logger.warning(f"Error creating indexes for collection '{self.collection_name}': {str(e)}")
            # Continue anyway - indexes are optional for functionality
    
    @property
    def collection(self):
        """Get the MongoDB collection for this repository"""
        return self.db[self.collection_name]
    
    async def get_dam_trade_book_response(self, datebid: str) -> List[Dict[str, Any]]:
        """
        Get DAM trade book response for a specific date.
        
        PHP: $this->damtradebookresponse->getDamTradeBookResponse($datebid)
        Query: where('schedule_date', $date)->get()
        
        Args:
            datebid: Schedule date in YYYY-MM-DD format
        
        Returns:
            List of trade book response documents with fields:
            - from_period_code
            - to_period_code
            - trade_price
            - trade_quantity
            - buy_sell (S for Sell, B for Buy)
            - delivery_date
            - revision_no
        """
        try:
            query = {"schedule_date": datebid}
            cursor = self.collection.find(query)
            results = await cursor.to_list(length=10000)
            
            # Convert ObjectId to string for JSON serialization
            for result in results:
                if "_id" in result:
                    result["_id"] = str(result["_id"])
            
            logger.info(f"Retrieved {len(results)} DAM trade book responses for date {datebid}")
            return results
            
        except Exception as e:
            logger.error(f"Error getting DAM trade book response for date {datebid}: {str(e)}")
            return []

