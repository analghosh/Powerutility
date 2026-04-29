# repositories/dam_single_bid_data_for_approval_repository.py
from typing import List, Dict, Any, Optional
import logging
from bson import ObjectId

from .base_repository import GenericRepository
from ...core.config import settings

logger = logging.getLogger(__name__)


class DAMSingleBidDataForApprovalRepository(GenericRepository):
    """
    Repository for the 'dam_single_bid_data_for_approval' collection.
    Handles DAM (Day-Ahead Market) single bid approval data.
    """

    def __init__(self, collection_name: Optional[str] = None):
        super().__init__()
        if collection_name is None:
            collection_name = settings.MONGODB_DAM_SINGLE_BID_DATA_FOR_APPROVAL_COLLECTION
        self.collection_name = collection_name
        logger.info(f"DAMSingleBidDataForApprovalRepository initialized with collection: {collection_name}")

    @property
    def collection(self):
        """Get the MongoDB collection for this repository"""
        return self.db[self.collection_name]

   
     # ---- Generic passthrough used by the service layer ----
    async def insert_many(self, documents: List[Any]):
        """
        Insert multiple documents into the collection asynchronously.
        Accepts any list of dicts or simple values (int, str, etc.) and converts them to dicts.
        Returns {"data": True} if successful.
        """
        # return documents[0]
        try: 
            documents = list(documents.values())
            documents = [doc for doc in documents if isinstance(doc, dict)]
            result = await self.collection.insert_many(documents, ordered=False)
            inserted_ids = [str(_id) for _id in result.inserted_ids]
            return inserted_ids
            
        except Exception as e:
            logger.error("Failed to bulk insert iex exchange serve records: %s", str(e))
            return {"error": str(e)}
        

    async def getPlantDataForApprovalbytime(self, date: str, timeBlock: str) -> int:
        pipeline = [
            {"$match": {"inserted_date": date, "start_time": timeBlock}},
            {"$sort": {"revision_no": -1}},
            {"$limit": 1},
            {"$project": {"_id": 0, "revision_no": 1}}
        ]

        cursor = self.collection.aggregate(pipeline)

        result = await cursor.to_list(length=10000)

        if result:
            return result[0].get("revision_no", 0)
        return 0

    async def find_by_filters(self, filters: Dict[str, Any], projection: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Find documents by filters with optional projection.
        """
        try:
            query = self.collection.find(filters, projection or {})
            return await query.to_list(length=10000)
        except Exception as e:
            logger.error("Failed to find DAM single bid records: %s", str(e))
            return []

    async def update_one(self, filter_query: Dict[str, Any], update_query: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update a single document matching the filter.
        """
        try:
            result = await self.collection.update_one(filter_query, update_query)
            return {"matched_count": result.matched_count, "modified_count": result.modified_count}
        except Exception as e:
            logger.error("Failed to update DAM single bid record: %s", str(e))
            return {"error": str(e)}

    async def aggregate(self, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Run aggregation pipeline on the collection.
        """
        try:
            cursor = self.collection.aggregate(pipeline)
            return await cursor.to_list(length=10000)
        except Exception as e:
            logger.error("Failed to aggregate DAM single bid records: %s", str(e))
            return []
    
    async def get_approval_data_by_date(self, datebid: str) -> Dict[str, Dict[str, Any]]:
        """
        Get approval data for a specific date, keyed by time_block string.
        
        Args:
            datebid: Schedule date in YYYY-MM-DD format
        
        Returns:
            Dictionary keyed by time_block string (e.g., "16:00-16:15")
        """
        try:
            query = {"bid_date": datebid}
            cursor = self.collection.find(query)
            results = await cursor.to_list(length=10000)
            
            # Convert to dictionary keyed by time_block
            result_dict = {}
            for result in results:
                time_block = result.get("time_block")
                if time_block:
                    # Convert ObjectId to string for JSON serialization
                    if "_id" in result and isinstance(result["_id"], ObjectId):
                        result["_id"] = str(result["_id"])
                    result_dict[time_block] = result
            
            logger.info(f"Retrieved {len(result_dict)} approval data records for date {datebid}")
            return result_dict
            
        except Exception as e:
            logger.error(f"Error getting approval data for date {datebid}: {str(e)}")
            return {}

    async def get_bid_status_data(self, datebid: str) -> Dict[str, Dict[str, Any]]:
        """
        Get bid status data for a specific date.
        
        PHP: $this->PlantDataForApprovalModel->getBidStatusData($datebid)
        Query: DB::table('dam_single_bid_data_for_approval')
               ->where('bid_date', $date)
               ->select(...)->groupBy('block_no')->get()->keyBy("time_block")
        
        Args:
            datebid: Bid date in YYYY-MM-DD format
        
        Returns:
            Dictionary keyed by time_block with bid status data
        """
        try:
            # Log the query details for debugging
            logger.debug(f"Querying bid_status_data for date: {datebid}, collection: {self.collection_name}")
            
            # First, check if any records exist for this date (for debugging)
            count = await self.collection.count_documents({"bid_date": datebid})
            logger.debug(f"Found {count} documents with bid_date={datebid} in collection {self.collection_name}")
            
            pipeline = [
                {"$match": {"bid_date": datebid}},
                {"$sort": {"revision_no": -1}},
                {"$group": {
                    "_id": "$block_no",
                    "block_no": {"$first": "$block_no"},
                    "time_block": {"$first": "$time_block"},
                    "revision_no": {"$first": "$revision_no"},
                    "bid_status": {"$first": "$bid_status"},
                    "requested_by": {"$first": "$requested_by"},
                    "approved_by": {"$first": "$approved_by"},
                    "requested_time": {"$first": "$requested_time"},
                    "approved_time": {"$first": "$approved_time"},
                    "Plants": {"$first": "$Plants"},
                    "remark": {"$first": "$remark"}
                }},
                {"$project": {
                    "_id": 0,
                    "block_no": 1,
                    "time_block": 1,
                    "revision_no": 1,
                    "bid_status": 1,
                    "requested_by": 1,
                    "approved_by": 1,
                    "requested_time": 1,
                    "approved_time": 1,
                    "Plants": 1,
                    "remark": 1
                }}
            ]
            
            cursor = self.collection.aggregate(pipeline)
            results = await cursor.to_list(length=10000)
            
            logger.debug(f"Aggregation returned {len(results)} grouped results for date {datebid}")
            
            # Convert to dictionary keyed by time_block
            result_dict = {}
            for result in results:
                time_block = result.get("time_block")
                if time_block:
                    result_dict[time_block] = result
                else:
                    logger.warning(f"Result missing time_block field: {result}")
            
            logger.info(f"Retrieved {len(result_dict)} bid status records for date {datebid}")
            if len(result_dict) == 0 and count > 0:
                logger.warning(f"No results after grouping, but {count} documents exist. Check if time_block field is present.")
            return result_dict
            
        except Exception as e:
            logger.error(f"Error getting bid status data for date {datebid}: {str(e)}")
            return {}
    

    async def get_bid_status_data_submitted(self, datebid: str) -> Dict[str, Dict[str, Any]]:
        """
        Get submitted bid status data for a specific date.
        
        PHP: $this->PlantDataForApprovalModel->getBidStatusDataSubmitte($datebid)
        Query: DB::table('dam_single_bid_data_for_approval')
               ->where('bid_date', $date)->where("bid_status","submitted")
               ->select(...)->groupBy('block_no')->get()->keyBy("time_block")
        
        Args:
            datebid: Bid date in YYYY-MM-DD format
        
        Returns:
            Dictionary keyed by time_block with submitted bid status data
        """
        try:
            pipeline = [
                {"$match": {
                    "bid_date": datebid,
                    "bid_status": "submitted"
                }},
                {"$group": {
                    "_id": "$block_no",
                    "block_no": {"$first": "$block_no"},
                    "time_block": {"$first": "$time_block"},
                    "revision_no": {"$first": "$revision_no"},
                    "bid_status": {"$first": "$bid_status"},
                    "requested_by": {"$first": "$requested_by"},
                    "approved_by": {"$first": "$approved_by"},
                    "requested_time": {"$first": "$requested_time"},
                    "approved_time": {"$first": "$approved_time"},
                    "Plants": {"$first": "$Plants"},
                    "remark": {"$first": "$remark"}
                }},
                {"$project": {
                    "_id": 0,
                    "block_no": 1,
                    "time_block": 1,
                    "revision_no": 1,
                    "bid_status": 1,
                    "requested_by": 1,
                    "approved_by": 1,
                    "requested_time": 1,
                    "approved_time": 1,
                    "Plants": 1,
                    "remark": 1
                }}
            ]
            
            cursor = self.collection.aggregate(pipeline)
            results = await cursor.to_list(length=10000)
            
            # Convert to dictionary keyed by time_block
            result_dict = {}
            for result in results:
                time_block = result.get("time_block")
                if time_block:
                    result_dict[time_block] = result
            
            logger.info(f"Retrieved {len(result_dict)} submitted bid status records for date {datebid}")
            return result_dict
            
        except Exception as e:
            logger.error(f"Error getting submitted bid status data for date {datebid}: {str(e)}")
            return {}
    