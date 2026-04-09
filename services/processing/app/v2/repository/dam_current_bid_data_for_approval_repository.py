# repositories/serve_dam_scheduling_repository.py
from typing import List, Dict, Any, Optional
import logging
from bson import ObjectId
from fastapi.encoders import jsonable_encoder

from .base_repository import GenericRepository

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
    
class DAMCurrentBidDataForApprovalRepository(GenericRepository):
    """
    Thin repository for the 'serve_dam_scheduling' collection.
    Keeps the same interface style as your other repos (e.g., serveExchangeTableRepository).
    """

    def __init__(self, collection_name: str = "dam_currect_selected_forecast_log"):
        super().__init__()
        self.collection_name = collection_name
        self.collection = self.db[self.collection_name]
        logger.info(f"DAMCurrentBidDataForApprovalRepository initialized with collection: {collection_name}")

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


    async def getData(self, time_block: str, schedule_date: str) -> int:
        
        result =  await self.collection.find_one({"time_block": time_block, "schedule_date":schedule_date})
    
        if result:
            return 1
        else:
            return 0
        
    async def deleteData(self,time_block: str, schedule_date: str):
        result = await self.collection.delete_one({"time_block": time_block, "schedule_date":schedule_date})
        return result

    async def createData(self, entity_dict):
        
        result =  await self.collection.insert_many([entity_dict])
        inserted_ids = [str(_id) for _id in result.inserted_ids]
        logger.info("Bulk insert successful")
        return {"data": True}
        response = {
            "inserted_id": result.inserted_id,
            "data": entity_dict
        }

        # Use jsonable_encoder with custom ObjectId handler
        return jsonable_encoder(response, custom_encoder={ObjectId: str})
    
    