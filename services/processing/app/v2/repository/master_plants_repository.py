
from typing import Any, Dict, List, Optional
from bson import ObjectId
import asyncio
from concurrent.futures import ThreadPoolExecutor

from ..repository.base_repository import GenericRepository
from ...core.config import settings
import logging

logger = logging.getLogger(__name__)

class MasterPlantsRepository(GenericRepository):
    def __init__(self):
        super().__init__()
        self.collection_name = settings.MONGODB_PLANT_MASTER_COLLECTION if hasattr(settings, 'MONGODB_PLANT_MASTER_COLLECTION') else "plant_master"
        logger.info(f"MasterPlantsRepository initialized with collection: {self.collection_name}")
        self.create_indexes()

    def create_indexes(self):
        """Create indexes for plant_master collection"""
        try:
            # Create indexes using pymongo directly on the collection object
            collection = self.db[self.collection_name]

            # Drop old non-unique idx_plant_id so it can be re-created as unique
            for index_name in ["plant_id_1", "idx_plant_id"]:
                try:
                    collection.drop_index(index_name)
                except Exception:
                    pass

            # Unique index on plant_id
            collection.create_index(
                [("plant_id", 1)],
                unique=True,
                name="idx_plant_id"
            )

            # Compound unique index on (plant_id, approval_no)
            collection.create_index(
                [("plant_id", 1), ("approval_no", 1)],
                unique=True, 
                background=True,
                name="unique_plant_id_approval"
            )
            
            logger.info("Indexes created/verified for plant_master collection")
        except Exception as e:
            logger.error(f"Failed to create indexes for plant_master: {str(e)}")

    def _create_sync(self, plant_data: Dict[str, Any]) -> str:
        result = self.db[self.collection_name].insert_one(plant_data)
        return str(result.inserted_id)

    async def create_plant(self, plant_data: Dict[str, Any]) -> str:
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            return await loop.run_in_executor(executor, lambda: self._create_sync(plant_data))

    async def get_plant_by_id(self, plant_oid: str) -> Optional[Dict[str, Any]]:
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            return await loop.run_in_executor(executor, lambda: self.find_by_id(self.collection_name, plant_oid))

    async def list_plants(
        self,
        filters: Optional[Dict[str, Any]] = None,
        page: int = 1,
        page_size: int = 50,
        sort: Optional[List[tuple]] = None,
    ) -> Dict[str, Any]:
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            return await loop.run_in_executor(
                executor,
                lambda: self.get_paginated_response(
                    collection_name=self.collection_name,
                    filter=filters,
                    page=page,
                    page_size=page_size,
                    sort=sort
                )
            )

    def _update_sync(self, plant_oid: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not ObjectId.is_valid(plant_oid):
            return None
        result = self.db[self.collection_name].update_one({"_id": ObjectId(plant_oid)}, {"$set": updates})
        if result.modified_count > 0:
            return self.find_by_id(self.collection_name, plant_oid)
        return self.find_by_id(self.collection_name, plant_oid) # Return doc even if no change

    async def update_plant(self, plant_oid: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            return await loop.run_in_executor(executor, lambda: self._update_sync(plant_oid, updates))

    def _delete_sync(self, plant_oid: str) -> bool:
        if not ObjectId.is_valid(plant_oid):
            return False
        result = self.db[self.collection_name].delete_one({"_id": ObjectId(plant_oid)})
        return result.deleted_count > 0

    async def delete_plant(self, plant_oid: str) -> bool:
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            return await loop.run_in_executor(executor, lambda: self._delete_sync(plant_oid))

    def _bulk_upsert_sync(self, plants: List[Dict[str, Any]], filter_criteria: callable) -> Dict[str, int]:
        from pymongo import UpdateOne
        
        if not plants:
            return {"inserted": 0, "updated": 0, "total": 0}

        bulk_ops = [UpdateOne(filter_criteria(p), {"$set": p}, upsert=True) for p in plants]
        result = self.db[self.collection_name].bulk_write(bulk_ops)
        
        return {
            "inserted": result.upserted_count,
            "updated": result.modified_count,
            "total": result.upserted_count + result.modified_count
        }

    async def bulk_upsert_plants(self, plants: List[Dict[str, Any]], filter_criteria: callable) -> Dict[str, int]:
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            return await loop.run_in_executor(executor, lambda: self._bulk_upsert_sync(plants, filter_criteria))

    async def get_all_plants(self) -> List[Dict[str, Any]]:
        """
        Get all plants from the plant_master collection.
        
        Returns:
            List[Dict[str, Any]]: List of all plant documents
        """
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            def _find_sync():
                cursor = self.db[self.collection_name].find({}, {"_id": 0})
                try:
                    return list(cursor)
                finally:
                    cursor.close()
            return await loop.run_in_executor(executor, _find_sync)

    async def get_plants_map_by_ids(self, plant_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        if not plant_ids:
            return {}
        unique_ids = list({pid for pid in plant_ids if pid})
        if not unique_ids:
            return {}
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            def _find_sync():
                cursor = self.db[self.collection_name].find(
                    {"plant_id": {"$in": unique_ids}},
                    {
                        "_id": 0,
                        "plant_id": 1,
                        "plant_name": 1,
                        "category": 1,
                        "technology": 1,
                        "dispatch_type": 1,
                        "display_category": 1,
                        "c_s": 1,
                        "px_price": 1,
                        "fc_price": 1,
                        "vc_price": 1,
                        "ppa": 1,
                        "price_group": 1,
                    },
                )
                try:
                    docs = list(cursor)
                finally:
                    cursor.close()
                return {doc.get("plant_id"): doc for doc in docs if doc.get("plant_id")}
            return await loop.run_in_executor(executor, _find_sync)

master_plants_repository = MasterPlantsRepository()
