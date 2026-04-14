"""
RTM Single Bid Response Repository
Handles database operations for RTM single bid response collection
"""
from typing import List, Dict, Any, Optional
import logging

from .base_repository import GenericRepository

logger = logging.getLogger(__name__)


class RTMSingleBidResponseRepository(GenericRepository):
    """
    Repository for 'iex_rtm_single_bid_response' collection.
    PHP Model: RtmSingleBidResponse
    Collection: 'iex_rtm_single_bid_response'
    """
    
    def __init__(self, collection_name: Optional[str] = None):
        super().__init__()
        self.collection_name = collection_name or "iex_rtm_single_bid_response"
        logger.info(f"RTMSingleBidResponseRepository initialized with collection: {self.collection_name}")
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
            
            # Index on biding_date for date-based queries
            collection.create_index([("biding_date", 1)])
            
            # Index on from_period_code for period-based queries
            collection.create_index([("from_period_code", 1)])
            
            # Index on start_time and end_time for time range queries
            collection.create_index([("start_time", 1)])
            collection.create_index([("end_time", 1)])
            
            # Index on cancel_status for status filtering
            collection.create_index([("cancel_status", 1)])
            
            # Compound index for date and period queries
            collection.create_index([("biding_date", 1), ("from_period_code", 1)])
            
            logger.info(f"Created indexes for collection '{self.collection_name}'")
        except Exception as e:
            logger.warning(f"Error creating indexes for collection '{self.collection_name}': {str(e)}")
            # Continue anyway - indexes are optional for functionality
    
    @property
    def collection(self):
        """Get the MongoDB collection for this repository"""
        return self.db[self.collection_name]
    
    async def get_rtm_single_bid_response(self, datebid: str) -> Optional[List[Dict[str, Any]]]:
        """
        Get RTM single bid response for a specific date.
        
        PHP: $this->rtmsinglebidresponse->getRtmSingleBidResponse($datebid)
        Query: query()->groupBy("from_period_code")->orderBy('_id', 'desc')
               ->where('biding_date', $date)->get([...])
        
        Args:
            datebid: Biding date in YYYY-MM-DD format (note: PHP uses 'biding_date' field)
        
        Returns:
            List of single bid response documents with fields:
            - from_period_code
            - to_period_code
            - pq_data
            - revision_no
            - buy_sell
            - delivery_date
            - cancel_status
            - start_time
            - end_time
            - order_id
            Returns None if no data found
        """
        try:
            # PHP groups by from_period_code and gets latest by _id desc
            # We'll use aggregation to achieve this
            pipeline = [
                {"$match": {"biding_date": datebid}},
                {"$sort": {"_id": -1}},  # Latest first
                {"$group": {
                    "_id": "$from_period_code",
                    "from_period_code": {"$first": "$from_period_code"},
                    "to_period_code": {"$first": "$to_period_code"},
                    "pq_data": {"$first": "$pq_data"},
                    "revision_no": {"$first": "$revision_no"},
                    "buy_sell": {"$first": "$buy_sell"},
                    "delivery_date": {"$first": "$delivery_date"},
                    "cancel_status": {"$first": "$cancel_status"},
                    "start_time": {"$first": "$start_time"},
                    "end_time": {"$first": "$end_time"},
                    "order_id": {"$first": "$order_id"}
                }},
                {"$project": {
                    "_id": 0,
                    "from_period_code": 1,
                    "to_period_code": 1,
                    "pq_data": 1,
                    "revision_no": 1,
                    "buy_sell": 1,
                    "delivery_date": 1,
                    "cancel_status": 1,
                    "start_time": 1,
                    "end_time": 1,
                    "order_id": 1
                }}
            ]
            
            cursor = self.collection.aggregate(pipeline)
            results = await cursor.to_list(10000)
            
            if not results:
                logger.info(f"No RTM single bid response found for date {datebid}")
                return None
            
            logger.info(f"Retrieved {len(results)} RTM single bid responses for date {datebid}")
            return results
            
        except Exception as e:
            logger.error(f"Error getting RTM single bid response for date {datebid}: {str(e)}")
            return None

