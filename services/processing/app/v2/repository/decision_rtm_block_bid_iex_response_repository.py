"""
Decision RTM Block Bid IEX Response Repository
Handles database operations for decision RTM block bid IEX response collection
"""
from typing import List, Dict, Any, Optional, Union
import logging

from .base_repository import GenericRepository

logger = logging.getLogger(__name__)


class DecisionRtmBlockBidIexResponseRepository(GenericRepository):
    """
    Repository for 'dicision_rtm_block_bid_iex_response' collection.
    PHP Model: DecisionRtmBlockBidIexResponse
    Collection: 'dicision_rtm_block_bid_iex_response'
    """
    
    def __init__(self, collection_name: Optional[str] = None):
        super().__init__()
        self.collection_name = collection_name or "dicision_rtm_block_bid_iex_response"
        logger.info(f"DecisionRtmBlockBidIexResponseRepository initialized with collection: {self.collection_name}")
        self._create_collection_if_not_exists()
        self._create_related_collections_if_not_exists()
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
    
    def _create_related_collections_if_not_exists(self):
        """Ensure related collections used by this repository exist (matching ingestion service pattern)"""
        try:
            # Access db property to ensure database connection is initialized
            db = self.db
            db_name = self.db_name or "unknown"
            
            related_collections = [
                "rtm_single_bid_status_template",
                "rtm_block_bid_status_template"
            ]
            
            collection_list = db.list_collection_names()
            
            for collection_name in related_collections:
                try:
                    if collection_name not in collection_list:
                        db.create_collection(collection_name)
                        logger.info(f"Created related collection '{collection_name}' in database '{db_name}'")
                    else:
                        logger.debug(f"Related collection '{collection_name}' already exists in database '{db_name}'")
                except Exception as e:
                    logger.warning(f"Error checking/creating related collection '{collection_name}': {str(e)}")
                    # Continue anyway - MongoDB will create collection on first insert
        except Exception as e:
            logger.warning(f"Error in _create_related_collections_if_not_exists: {str(e)}")
            # Continue anyway - MongoDB will create collection on first insert
    
    def _create_indexes(self):
        """Create necessary indexes for efficient queries (matching ingestion service pattern)"""
        try:
            collection = self.collection
            
            # Index on inserted_date for date-based queries
            collection.create_index([("inserted_date", 1)])
            
            # Index on Biding_date for bid date queries
            collection.create_index([("Biding_date", 1)])
            
            # Index on time_block for time block queries
            collection.create_index([("time_block", 1)])
            
            # Index on from and to fields for time range queries
            collection.create_index([("from", 1)])
            collection.create_index([("to", 1)])
            
            # Index on status for status filtering
            collection.create_index([("status", 1)])
            
            # Compound index for date and status queries
            collection.create_index([("inserted_date", 1), ("status", 1)])
            
            # Compound index for bid date and time range queries
            collection.create_index([("Biding_date", 1), ("from", 1), ("to", 1)])
            
            logger.info(f"Created indexes for collection '{self.collection_name}'")
            
            # Also create indexes for related collections
            self._create_related_collection_indexes()
            
        except Exception as e:
            logger.warning(f"Error creating indexes for collection '{self.collection_name}': {str(e)}")
            # Continue anyway - indexes are optional for functionality
    
    def _create_related_collection_indexes(self):
        """Create indexes for related collections"""
        try:
            db = self.db
            
            # Indexes for rtm_single_bid_status_template
            if "rtm_single_bid_status_template" in db.list_collection_names():
                template_collection = db["rtm_single_bid_status_template"]
                template_collection.create_index([("schedule_date", 1)])
                template_collection.create_index([("exn_user_id", 1)])
                template_collection.create_index([("from_period_code", 1)])
                template_collection.create_index([("order_status", 1)])
                template_collection.create_index([("schedule_date", 1), ("exn_user_id", 1)])
                logger.info("Created indexes for rtm_single_bid_status_template")
            
            # Indexes for rtm_block_bid_status_template
            if "rtm_block_bid_status_template" in db.list_collection_names():
                block_template_collection = db["rtm_block_bid_status_template"]
                block_template_collection.create_index([("schedule_date", 1)])
                block_template_collection.create_index([("exn_user_id", 1)])
                block_template_collection.create_index([("schedule_date", 1), ("exn_user_id", 1)])
                logger.info("Created indexes for rtm_block_bid_status_template")
                
        except Exception as e:
            logger.warning(f"Error creating indexes for related collections: {str(e)}")
            # Continue anyway - indexes are optional for functionality
    
    @property
    def collection(self):
        """Get the MongoDB collection for this repository"""
        return self.db[self.collection_name]
    
    async def get_block_data(self, block_bid_date: str) -> Dict[str, Dict[str, Any]]:
        """
        Get block bid data for a specific date, keyed by time_block.
        
        PHP: $this->decisionrtmblockbidiexresponse->getblockdata($block_bid_date)
        Query: where('inserted_date', $date)->get()->keyBy("time_block")
        
        Args:
            block_bid_date: Date in d-m-Y format (PHP uses inserted_date in YYYY-MM-DD)
        
        Returns:
            Dictionary keyed by time_block
        """
        try:
            # Convert d-m-Y format to YYYY-MM-DD format
            from datetime import datetime
            try:
                date_obj = datetime.strptime(block_bid_date, "%d-%m-%Y")
                inserted_date = date_obj.strftime("%Y-%m-%d")
            except ValueError:
                inserted_date = block_bid_date
            
            query = {"inserted_date": inserted_date}
            cursor = self.collection.find(query)
            results = await cursor.to_list(None)
            
            # Convert to dictionary keyed by time_block
            # NOTE: multiple docs can exist per time_block; use list to avoid overwriting
            result_dict = {}
            for result in results:
                time_block = result.get("time_block")
                if time_block:
                    # Convert ObjectId to string for JSON serialization
                    if "_id" in result:
                        result["_id"] = str(result["_id"])
                    result_dict.setdefault(time_block, []).append(result)
            
            logger.info(f"Retrieved {len(result_dict)} block bid data records for date {block_bid_date}")
            return result_dict
            
        except Exception as e:
            logger.error(f"Error getting block data for date {block_bid_date}: {str(e)}")
            return {}
    
    async def get_exchange_bid_status(self, datebid: str, user_id: str) -> Dict[str, Dict[str, Any]]:
        """
        Get exchange bid status for a specific date and user.
        
        PHP: $this->decisionrtmblockbidiexresponse->get_Exchange_bid_status($datebid, $userid)
        Query: DB::table("rtm_single_bid_status_template")
               ->where("schedule_date", $date)->where("exn_user_id", $userid)
               ->where("order_status","!=",4)->where("order_status","!=",8)
               ->where("order_status","!=",256)->get()->keyby("from_period_code")
        
        Args:
            datebid: Schedule date in YYYY-MM-DD format
            user_id: Exchange user ID
        
        Returns:
            Dictionary keyed by from_period_code
        """
        try:
            # Note: This queries a different collection: rtm_single_bid_status_template
            collection_name = "rtm_single_bid_status_template"
            collection = self.db[collection_name]
            
            query = {
                "schedule_date": datebid,
                "exn_user_id": user_id,
                "order_status": {"$nin": [4, 8, 256]}  # Not in [4, 8, 256]
            }
            
            cursor = collection.find(query)
            results = await cursor.to_list(None)
            
            # Convert to dictionary keyed by from_period_code
            result_dict = {}
            for result in results:
                from_period = result.get("from_period_code")
                if from_period is not None:
                    # Convert ObjectId to string for JSON serialization
                    if "_id" in result:
                        result["_id"] = str(result["_id"])
                    result_dict[from_period] = result
            
            logger.info(f"Retrieved {len(result_dict)} exchange bid status records for date {datebid}, user {user_id}")
            return result_dict
            
        except Exception as e:
            logger.error(f"Error getting exchange bid status for date {datebid}, user {user_id}: {str(e)}")
            return {}
    
    async def get_exchange_block_bid_status(self, datebid: str, user_id: str) -> List[Dict[str, Any]]:
        """
        Get exchange block bid status for a specific date and user.
        
        PHP: $this->decisionrtmblockbidiexresponse->get_Exchange_Block_bid_status($datebid, $userid)
        Query: DB::table("rtm_block_bid_status_template")
               ->orderBy('submited_bid_response.order_id', 'asc')
               ->where("schedule_date", $date)->where("exn_user_id", $userid)->get()
        
        Args:
            datebid: Schedule date in YYYY-MM-DD format
            user_id: Exchange user ID
        
        Returns:
            List of exchange block bid status documents ordered by order_id
        """
        try:
            # Note: This queries a different collection: rtm_block_bid_status_template
            collection_name = "rtm_block_bid_status_template"
            collection = self.db[collection_name]
            
            query = {
                "schedule_date": datebid,
                "exn_user_id": user_id
            }
            
            # PHP orders by submited_bid_response.order_id, but that's nested
            # We'll order by _id first, then sort in Python if needed
            cursor = collection.find(query).sort("_id", 1)
            results = await cursor.to_list(None)
            
            # Convert ObjectId to string for JSON serialization
            for result in results:
                if "_id" in result:
                    result["_id"] = str(result["_id"])
            
            # Sort by nested order_id if available
            try:
                results.sort(key=lambda x: x.get("submited_bid_response", {}).get("order_id", 0))
            except (KeyError, TypeError):
                pass  # If order_id not available, keep original order
            
            logger.info(f"Retrieved {len(results)} exchange block bid status records for date {datebid}, user {user_id}")
            return results
            
        except Exception as e:
            logger.error(f"Error getting exchange block bid status for date {datebid}, user {user_id}: {str(e)}")
            return []
    
    async def insert_response_doc(self, doc: Dict[str, Any]) -> bool:
        """
        Insert a single exchange response document.
        Called once per bid entry in the exchange response bid_details[].
        """
        try:
            self.collection.insert_one(doc)
            return True
        except Exception as e:
            logger.error(f"Failed to insert IEX response doc: {str(e)}")
            return False

    async def get_by_order_id(self, order_id: int) -> Optional[Dict[str, Any]]:
        """
        Fetch a single document by exchange order_id.
        Used before cancellation to retrieve last_updated_time.
        """
        try:
            doc = self.collection.find_one({"order_id": order_id})
            if doc and "_id" in doc:
                doc["_id"] = str(doc["_id"])
            return doc
        except Exception as e:
            logger.error(f"Failed to get IEX response doc by order_id={order_id}: {str(e)}")
            return None

    async def update_status_by_order_id(self, order_id: int, status: str, extra_fields: Dict[str, Any] = None) -> bool:
        """
        Update status (and optional extra fields) on the document with given exchange order_id.
        Called after successful exchange cancellation.
        """
        try:
            update = {"status": status}
            if extra_fields:
                update.update(extra_fields)
            result = self.collection.update_one(
                {"order_id": order_id},
                {"$set": update}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Failed to update status for order_id={order_id}: {str(e)}")
            return False

    async def bulk_insert_response_docs(self, docs: List[Dict[str, Any]]) -> int:
        """
        Insert multiple exchange response documents at once.
        Returns count of inserted documents.
        """
        if not docs:
            return 0
        try:
            result = self.collection.insert_many(docs, ordered=False)
            return len(result.inserted_ids)
        except Exception as e:
            logger.error(f"Failed to bulk insert IEX response docs: {str(e)}")
            return 0

    async def get_submitted_data_for_bid_status_by_time_block(
        self, 
        block_bid_date: str, 
        from_time: str, 
        to_time: str
    ) -> List[Dict[str, Any]]:
        """
        Get submitted data for bid status by time block.
        
        PHP: $this->decisionrtmblockbidiexresponse->get_submitted_data_for_bid_status_by_timeBlock($block_bid_date, $from, $to)
        Query: orderBy('_id', 'asc')->where('from', $from)->orWhere('to', $to)
               ->where('Biding_date', $date)->where(function($query) {...})->get()
        
        Args:
            block_bid_date: Biding date in d-m-Y format (PHP uses Biding_date in YYYY-MM-DD)
            from_time: Start time (from field)
            to_time: End time (to field)
        
        Returns:
            List of submitted data documents
            Must support ->toarray() method for PHP compatibility
        """
        try:
            # Convert d-m-Y format to YYYY-MM-DD format
            from datetime import datetime
            try:
                date_obj = datetime.strptime(block_bid_date, "%d-%m-%Y")
                biding_date = date_obj.strftime("%Y-%m-%d")
            except ValueError:
                biding_date = block_bid_date
            
            # PHP query logic:
            # where('from', $from)->orWhere('to', $to)->where('Biding_date', $date)
            # This is a bit ambiguous, but based on the PHP code, it seems to be:
            # (from == $from OR to == $to) AND Biding_date == $date
            # 
            query = {
                "from": from_time,
                "to": to_time,
                "Biding_date": biding_date,
                "status": {"$in": [
                    "Submitted",
                    "pending",
                    "Pending Withdrawal Lapse",
                    "Pending_withdrawal"
                ]}
            }

            cursor = self.collection.find(query).sort("_id", 1)
            results = await cursor.to_list(None)
            
            # Convert ObjectId to string for JSON serialization
            for result in results:
                if "_id" in result:
                    result["_id"] = str(result["_id"])
            
            logger.info(f"Retrieved {len(results)} submitted data records for date {block_bid_date}, time {from_time}-{to_time}")
            return results
            
        except Exception as e:
            logger.error(f"Error getting submitted data for bid status by time block: {str(e)}")
            return []

