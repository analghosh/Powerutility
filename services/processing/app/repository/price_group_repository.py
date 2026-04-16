from typing import Any, Dict, List, Optional, Tuple
from pymongo import ReturnDocument
from bson import ObjectId
from libs.models.price_group_entity import PriceGroupEntity, COLL_PRICE_GROUP
from .base_repository import BaseRepository


class PriceGroupRepository(BaseRepository):
    """Data access for price_group collection built on your BaseRepository.
    Change the collection once in libs/models/price_group_entity.py
    """
    def __init__(self):
        super().__init__(COLL_PRICE_GROUP)
        self._indexes_ensured = False


    async def ensure_indexes(self):
        if self._indexes_ensured:
            return
        try:
            await self.create_index([("price_group", 1)], unique=True)
            await self.create_index([("price_from", 1), ("price_to", 1)], unique=False)
            await self.create_index([("price_group", 1), ("created_at", -1)], unique=False)
            await self.create_index([("created_at", -1)], unique=False)
            self._indexes_ensured = True
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to ensure indexes: {e}")


    # Strongly-typed helpers wrapping BaseRepository
    def create(self, data: Dict[str, Any]) -> str: # type: ignore[override]
        entity = PriceGroupEntity(**data)
        return super().create(entity.model_dump(by_alias=True, exclude_none=True))


    def get(self, _id: str) -> Optional[Dict[str, Any]]:
        doc = super().find_by_id(_id)
        return self._dump(doc)


    def get_by_group(self, price_group: str) -> Optional[Dict[str, Any]]:
        doc = super().find_one({"price_group": price_group})
        return self._dump(doc)


    async def list(
        self,
        filters: Optional[Dict[str, Any]] = None,
        page: int = 1,
        page_size: int = 50,
        sort: Optional[List[Tuple[str, int]]] = None,
        ) -> Dict[str, Any]:
        filters = filters or {}
        sort = sort or [("price_group", 1)]
        skip = (page - 1) * page_size
        
        # Lazily ensure indexes on first use
        await self.ensure_indexes()
        
        import time
        import logging
        logger = logging.getLogger(__name__)
        
        # Use aggregation to get both data and count in a single query
        # Add index hint for better performance
        pipeline = [
            {"$match": filters},
            {"$facet": {
                "data": [
                    {"$sort": dict(sort)},
                    {"$skip": skip},
                    {"$limit": page_size},
                    {"$project": {  # Only select needed fields
                        "price_group": 1,
                        "price_from": 1,
                        "price_to": 1,
                        "inserted_date": 1,
                        "created_at": 1,
                        "updated_at": 1
                    }}
                ],
                "count": [
                    {"$count": "total"}
                ]
            }}
        ]
        
        logger.info(f"🔍 Pipeline: {pipeline}")
        
        total_start = time.time()
        logger.info(f"🔍 Starting price_group list query with filters: {filters}, page: {page}, page_size: {page_size}")
        logger.info(f"🔍 Collection: {self.collection.name}, Database: {self.collection.database.name}")
        
        # Check collection stats
        try:
            stats = await self.collection.estimated_document_count()
            logger.info(f"🔍 Collection has approximately {stats} documents")
        except Exception as e:
            logger.warning(f"🔍 Could not get collection stats: {e}")
        
        try:
            pipeline_start = time.time()
            cursor = self.collection.aggregate(pipeline)
            try:
                result = await cursor.to_list(10000)
            finally:
                await cursor.close()
            db_time = time.time() - pipeline_start
            
            logger.info(f"🔍 Aggregation completed in {db_time:.3f}s, result length: {len(result)}")
            
            if not result:
                logger.warning("🔍 Aggregation returned empty result")
                return {"items": [], "total": 0, "page": page, "page_size": page_size}
            
            data = result[0]["data"]
            count_result = result[0]["count"]
            total = count_result[0]["total"] if count_result else 0
            
            logger.info(f"🔍 Extracted data: {len(data)} items, total count: {total}")
            
            # DEBUG: Log the actual data being returned
            for i, item in enumerate(data):
                logger.info(f"🔍 DEBUG Item {i}: {item}")
            
        except Exception as e:
            logger.error(f"🔍 Aggregation failed: {e}, falling back to old method")
            # Fallback to old method
            cursor = self.collection.find(filters).sort(sort).skip(skip).limit(page_size)
            data = await cursor.to_list(10000)
            total = self.count(filters)
            db_time = time.time() - pipeline_start
        
        processing_start = time.time()
        items = [self._dump(d) for d in data]
        processing_time = time.time() - processing_start
        
        total_time = time.time() - total_start
        
        # DEBUG: Log the final processed items
        for i, item in enumerate(items):
            logger.info(f"🔍 DEBUG Final Item {i}: {item}")
        
        # Log comprehensive performance metrics
        logger.info(f"⚡ PERFORMANCE: DB query: {db_time:.3f}s, Processing: {processing_time:.3f}s, Total: {total_time:.3f}s, Items: {len(items)}, Total count: {total}")
        
        return {"items": items, "total": total, "page": page, "page_size": page_size}


    async def update(self, _id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not ObjectId.is_valid(_id):
            return None
        if not updates:
            return await self.get(_id)
        current = await super().find_by_id(_id) or {}
        # Validate merged state
        PriceGroupEntity(**{**current, **updates})
        res = await self.collection.find_one_and_update(
            {"_id": ObjectId(_id)},
            {"$set": updates},
            return_document=ReturnDocument.AFTER,
            )
        return self._dump(res)


    def delete(self, _id: str) -> bool:
       return super().delete(_id)


    def _dump(self, doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not doc:
            return None

        # Fast path: avoid Pydantic validation for read operations
        # Only do basic field mapping and type conversion
        result = {}
        
        # Copy known fields directly
        for field in ["price_group", "price_from", "price_to", "inserted_date", "created_at", "updated_at"]:
            if field in doc:
                result[field] = doc[field]
        
        # Handle ID conversion
        if "_id" in doc and doc["_id"] is not None:
            result["price_group_id"] = str(doc["_id"])
        
        return result