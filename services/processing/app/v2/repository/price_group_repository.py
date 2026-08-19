from typing import Any, Dict, List, Optional, Tuple
from pymongo import ReturnDocument
from bson import ObjectId
from libs.models.price_group_entity import PriceGroupEntity, COLL_PRICE_GROUP
from libs.database.base_repository import BaseRepository


class PriceGroupRepository(BaseRepository):
    """Data access for price_group collection built on BaseRepository."""

    def __init__(self):
        from services.processing.app.core.config import settings
        super().__init__(COLL_PRICE_GROUP, settings.MONGODB_URI, settings.MONGODB_DATABASE)
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


    async def create(self, data: Dict[str, Any]) -> str:  # type: ignore[override]
        entity = PriceGroupEntity(**data)
        return await super().create(entity.model_dump(by_alias=True, exclude_none=True))


    async def get(self, _id: str) -> Optional[Dict[str, Any]]:
        doc = await super().find_by_id(_id)
        return self._dump(doc)


    async def get_by_group(self, price_group: str) -> Optional[Dict[str, Any]]:
        doc = await super().find_one({"price_group": price_group})
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

        await self.ensure_indexes()

        import time
        import logging
        logger = logging.getLogger(__name__)

        pipeline = [
            {"$match": filters},
            {"$facet": {
                "data": [
                    {"$sort": dict(sort)},
                    {"$skip": skip},
                    {"$limit": page_size},
                    {"$project": {
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

        total_start = time.time()
        logger.info(f"Starting price_group list query with filters: {filters}, page: {page}, page_size: {page_size}")

        try:
            stats = await self.collection.estimated_document_count()
            logger.info(f"Collection has approximately {stats} documents")
        except Exception as e:
            logger.warning(f"Could not get collection stats: {e}")

        try:
            pipeline_start = time.time()
            cursor = self.collection.aggregate(pipeline)
            try:
                result = await cursor.to_list(length=10000)
            finally:
                await cursor.close()
            db_time = time.time() - pipeline_start

            if not result:
                return {"items": [], "total": 0, "page": page, "page_size": page_size}

            data = result[0]["data"]
            count_result = result[0]["count"]
            total = count_result[0]["total"] if count_result else 0

        except Exception as e:
            logger.error(f"Aggregation failed: {e}, falling back to old method")
            pipeline_start = time.time()
            cursor = self.collection.find(filters).sort(sort).skip(skip).limit(page_size)
            data = await cursor.to_list(length=page_size)
            total = await self.count(filters)
            db_time = time.time() - pipeline_start

        processing_start = time.time()
        items = [self._dump(d) for d in data]
        processing_time = time.time() - processing_start
        total_time = time.time() - total_start

        logger.info(f"PERFORMANCE: DB query: {db_time:.3f}s, Processing: {processing_time:.3f}s, Total: {total_time:.3f}s, Items: {len(items)}, Total count: {total}")

        return {"items": items, "total": total, "page": page, "page_size": page_size}


    async def update(self, _id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not ObjectId.is_valid(_id):
            return None
        if not updates:
            return await self.get(_id)
        current = await super().find_by_id(_id) or {}
        PriceGroupEntity(**{**current, **updates})
        res = await self.collection.find_one_and_update(
            {"_id": ObjectId(_id)},
            {"$set": updates},
            return_document=ReturnDocument.AFTER,
            )
        return self._dump(res)


    # Collection holding the live records that point at a price group by name. Deliberately
    # only plant_master: seven other collections carry a `price_group` field
    # (dam/gdam/hpdam/rtm bid captures, supply_serve, supply_serve_historical), but those
    # are records of things that already happened. Blocking on them would make any price
    # group that has ever been traded permanently undeletable, which is a worse outcome
    # than the orphan this guards against — and it is not what VAPT F3 describes.
    PLANT_COLLECTION = "plant_master"

    async def count_referencing_plants(self, price_group: str) -> int:
        """
        How many plants still point at this price group.

        The reference is by NAME, not by id (`plant_master.price_group` holds "A"/"B"/"C"),
        so the caller must pass the group's `price_group` value, not its ObjectId.
        """
        if not price_group:
            return 0
        return await self.db[self.PLANT_COLLECTION].count_documents(
            {"price_group": price_group}
        )

    async def delete(self, _id: str) -> bool:
        return await super().delete(_id)


    def _dump(self, doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not doc:
            return None
        result = {}
        for field in ["price_group", "price_from", "price_to", "inserted_date", "created_at", "updated_at"]:
            if field in doc:
                result[field] = doc[field]
        if "_id" in doc and doc["_id"] is not None:
            result["price_group_id"] = str(doc["_id"])
        return result
