# repositories/serve_dam_scheduling_repository.py
import datetime
from typing import List, Dict, Any, Optional
import logging
from bson import ObjectId
from fastapi.encoders import jsonable_encoder

from .base_repository import BaseRepository

logger = logging.getLogger(__name__)

def convert_objectid(obj):
    if isinstance(obj, dict):
        return {k: convert_objectid(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_objectid(i) for i in obj]
    elif isinstance(obj, ObjectId):
        return str(obj)
    else:
        return obj
    
class BlockBidCurrentDataRepository(BaseRepository):
    """
    Thin repository for the 'serve_dam_scheduling' collection.
    Keeps the same interface style as your other repos (e.g., serveExchangeTableRepository).
    """

    def __init__(self, collection_name: str = "block_bid_current_data"):
        super().__init__(collection_name=collection_name)
        logger.info(f"BlockBidCurrentDataRepository initialized with collection: {collection_name}")

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

            logger.info(f"Inserting {len(documents)} documents into collection...")
            
            result= await self.collection.insert_many(documents, ordered=False)
            inserted_ids = [str(_id) for _id in result.inserted_ids]
            
            logger.info("Bulk insert successful")
            return {"data": True}
        except Exception as e:
            logger.error("Failed to bulk insert iex exchange serve records: %s", str(e))
            return str(e) # type: ignore

    async def get_data(self, date: str) -> List[Dict]:
        try:
            query = {
                "biding_date": date,
                "status": {"$ne": "Submitted"}
            }

            docs = await self.collection.find(query).sort("from", -1).to_list(length=10000)
            result = []

            for doc in docs:
                # Convert ObjectId to string for JSON-safe response
                if "_id" in doc and isinstance(doc["_id"], ObjectId):
                    doc["_id"] = str(doc["_id"])
                result.append(doc)

            return result

        except Exception as e:
            logger.error(f"Failed to get pending approvals in {self.collection_name}: {str(e)}")
            raise ValueError(f"Error fetching data: {str(e)}") from e

        
    async def updatedata(self, record_id, data):
        result =  await self.collection.update_one(
            {"_id": ObjectId(record_id)},
            {"$set": data}
        )
        print(result)
        return result.modified_count

    
    