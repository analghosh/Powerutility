# repositories/rtm_single_bid_data_for_approval_repository.py
from typing import List, Dict, Any, Optional
import logging

from libs.database.base_repository import BaseRepository as GenericRepository
from ..core.config import settings

logger = logging.getLogger(__name__)


class RTMSingleBidDataForApprovalRepository(GenericRepository):
    """
    Repository for the 'rtm_single_bid_data_for_approval' collection.
    Handles RTM (Real-Time Market) single bid approval data.
    """

    def __init__(self, collection_name: Optional[str] = None):
        if collection_name is None:
            collection_name = settings.MONGODB_RTM_SINGLE_BID_DATA_FOR_APPROVAL_COLLECTION

        super().__init__(
            collection_name=collection_name,
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE
        )

        logger.info(f"RTMSingleBidDataForApprovalRepository initialized with collection: {collection_name}")

    async def _create_collection_if_not_exists(self):
        """
        Create collection if it does not exist.
        """
        try:
            db = self.db
            db_name = self.db_name or "unknown"

            existing_names = await db.list_collection_names()
            if self.collection_name not in existing_names:
                await db.create_collection(self.collection_name)
                logger.info(f"Created collection '{self.collection_name}' in database '{db_name}'")
            else:
                logger.debug(f"Collection '{self.collection_name}' already exists in database '{db_name}'")
        except Exception as e:
            logger.warning(f"Error checking/creating collection '{self.collection_name}': {str(e)}")
            # Continue anyway - MongoDB will create collection on first insert

    async def _create_indexes(self):
        """Create necessary indexes for efficient queries"""
        try:
            collection = self.collection

            await collection.create_index([("bid_date", 1)])
            await collection.create_index([("inserted_date", 1)])
            await collection.create_index([("time_block", 1)])
            await collection.create_index([("start_time", 1)])
            await collection.create_index([("block_no", 1)])
            await collection.create_index([("bid_status", 1)])
            await collection.create_index([("bid_date", 1), ("block_no", 1)])
            await collection.create_index([("bid_date", 1), ("bid_status", 1)])
            await collection.create_index([("inserted_date", 1), ("start_time", 1)])

            logger.info(f"Created indexes for collection '{self.collection_name}'")
        except Exception as e:
            logger.warning(f"Error creating indexes for collection '{self.collection_name}': {str(e)}")
            # Continue anyway - indexes are optional for functionality


    # ---- Generic passthrough used by the service layer ----
    async def insert_many(self, documents: List[Any]) -> Dict[str, Any]:
        """
        Insert multiple documents into the collection asynchronously.
        Accepts any list of dicts or simple values and converts them to dicts.
        Returns {"data": True} if successful.
        """
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
            cursor = self.collection.find(filters, projection or {})
            return await cursor.to_list(length=10000)
        except Exception as e:
            logger.error("Failed to find RTM single bid records: %s", str(e))
            return []

    async def update_one(self, filter_query: Dict[str, Any], update_query: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update a single document matching the filter.
        """
        try:
            result = await self.collection.update_one(filter_query, update_query)
            return {"matched_count": result.matched_count, "modified_count": result.modified_count}
        except Exception as e:
            logger.error("Failed to update RTM single bid record: %s", str(e))
            return {"error": str(e)}

    async def update_many(self, filter_query: Dict[str, Any], update_query: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update multiple documents matching the filter.
        """
        try:
            result = await self.collection.update_many(filter_query, update_query)
            return {"matched_count": result.matched_count, "modified_count": result.modified_count}
        except Exception as e:
            logger.error("Failed to update RTM single bid records: %s", str(e))
            return {"error": str(e)}

    async def aggregate(self, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Run aggregation pipeline on the collection.
        """
        try:
            cursor = self.collection.aggregate(pipeline)
            return await cursor.to_list(length=10000)
        except Exception as e:
            logger.error("Failed to aggregate RTM single bid records: %s", str(e))
            return []

    async def get_bid_status_data(self, datebid: str) -> Dict[str, Dict[str, Any]]:
        """
        Get bid status data for a specific date.
        """
        try:
            logger.debug(f"Querying bid_status_data for date: {datebid}, collection: {self.collection_name}")

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

    async def get_approval_data_by_date(self, datebid: str) -> Dict[str, Dict[str, Any]]:
        """
        Get approval data for a specific date, keyed by time_block string.
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
                    if "_id" in result:
                        result["_id"] = str(result["_id"])
                    result_dict[time_block] = result

            logger.info(f"Retrieved {len(result_dict)} approval data records for date {datebid}")
            return result_dict

        except Exception as e:
            logger.error(f"Error getting approval data for date {datebid}: {str(e)}")
            return {}
