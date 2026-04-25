# repositories/dam_single_bid_data_for_approval_repository.py
from typing import List, Dict, Any, Optional
import logging

from libs.database.base_repository import BaseRepository as GenericRepository
from ..core.config import settings

logger = logging.getLogger(__name__)


class DAMSingleBidDataForApprovalRepository(GenericRepository):
    """
    Repository for the 'dam_single_bid_data_for_approval' collection.
    Handles DAM (Day Ahead Market) single bid approval data.
    """

    def __init__(self, collection_name: Optional[str] = None):
        if collection_name is None:
            collection_name = settings.MONGODB_DAM_SINGLE_BID_DATA_FOR_APPROVAL_COLLECTION

        super().__init__(
            collection_name=collection_name,
            mongodb_uri=settings.AUDIT_DB_URI,
            database_name=settings.AUDIT_DB_NAME
        )

        logger.info(f"DAMSingleBidDataForApprovalRepository initialized with collection: {collection_name}")



    # ---- Generic passthrough used by the service layer ----
    async def insert_many(self, documents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Insert multiple documents into the collection asynchronously.
        Returns {"data": True} if successful.
        """
        try:
            if not documents:
                raise ValueError("documents must be a non-empty list")

            # Ensure all documents are dicts
            for i, doc in enumerate(documents):
                if not isinstance(doc, dict):
                    raise TypeError(f"Document at index {i} is not a dict: {type(doc)}")

            logger.info(f"Inserting {len(documents)} documents into DAM single bid collection...")

            result = await self.collection.insert_many(documents, ordered=False)
            inserted_ids = [str(_id) for _id in result.inserted_ids]

            logger.info("DAM single bid bulk insert successful")
            return {"data": True, "inserted_ids": inserted_ids}
        except Exception as e:
            logger.error("Failed to bulk insert DAM single bid records: %s", str(e))
            return {"data": False, "error": str(e)}

    async def find_by_filters(self, filters: Dict[str, Any], projection: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Find documents by filters with optional projection.
        """
        try:
            cursor = self.collection.find(filters, projection or {})
            return await cursor.to_list(length=10000)
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
