# repositories/serve_dam_scheduling_repository.py
from typing import List, Dict, Any, Optional
import logging

from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


class RTMSingleBidDataForApprovalRepository(BaseRepository):
    """
    Thin repository for the 'serve_dam_scheduling' collection.
    Keeps the same interface style as your other repos (e.g., serveExchangeTableRepository).
    """

    def __init__(self, collection_name: str = "rtm_single_bid_data_for_approval"):
        super().__init__(collection_name=collection_name)
        logger.info(f"serveDamSchedulingRepository initialized with collection: {collection_name}")

    # ---- Generic passthrough used by the service layer ----
    async def insert_many(self, documents: List[Any]) -> Dict[str, Any]:
        """
        Insert multiple documents into the collection asynchronously.
        Accepts any list of dicts or simple values (int, str, etc.) and converts them to dicts.
        Returns {"data": True} if successful.
        """
        # return documents[0]
        documents = list(documents.values())
        documents = [doc for doc in documents if isinstance(doc, dict)]
        result = await self.collection.insert_many(documents, ordered=False)
        inserted_ids = [str(_id) for _id in result.inserted_ids]
        return inserted_ids
        try:
            
            if not documents:
                raise ValueError("documents must be a non-empty list")

            # Ensure all documents are dicts, wrap non-dict items
            clean_docs = []
            
            for i, doc in enumerate(documents):

                if isinstance(doc, dict):
                    clean_docs.append(doc)
                else:
                    logger.warning(f"Document at index {i} is not a dict ({type(doc)}), wrapping into dict")
                    clean_docs.append({"value": doc})

            logger.info(f"Inserting {len(clean_docs)} documents into collection...")
            
            result = await self.collection.insert_many(clean_docs, ordered=False)
            inserted_ids = [str(_id) for _id in result.inserted_ids]

            logger.info(f"Bulk insert successful, inserted_ids: {inserted_ids}")
            return {"data": True}

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