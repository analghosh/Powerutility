from typing import Any, Dict, List, Optional
from datetime import datetime
from bson import ObjectId
from pymongo import UpdateOne
import logging
import re

from ..repository.base_repository import GenericRepository
from ...core.config import settings

logger = logging.getLogger(__name__)


class PpaDetailsRepository(GenericRepository):
    def __init__(self):
        super().__init__()
        self.collection_name = getattr(
            settings, "MONGODB_PPA_DETAILS_COLLECTION", "ppa_details"
        )
        logger.info(f"PpaDetailsRepository initialized with collection: {self.collection_name}")

    async def create_indexes(self):
        """Create indexes for ppa_details collection."""
        try:
            collection = self.db[self.collection_name]

            await collection.create_index(
                [("plant_id", 1)],
                unique=True,
                name="idx_ppa_plant_id",
            )
            await collection.create_index(
                [("plant_name", 1)],
                name="idx_ppa_plant_name",
            )
            await collection.create_index(
                [("status", 1)],
                name="idx_ppa_status",
            )
            logger.info("Indexes created/verified for ppa_details collection")
        except Exception as e:
            logger.error(f"Failed to create indexes for ppa_details: {str(e)}")

    async def create_ppa(self, data: Dict[str, Any]) -> str:
        now = datetime.utcnow()
        data.setdefault("created_at", now)
        data.setdefault("updated_at", now)
        data.setdefault("ppa_files", [])
        data.setdefault("status", "Active")
        data.setdefault("expired_but_extended", False)
        collection = self.db[self.collection_name]
        result = await collection.insert_one(data)
        return str(result.inserted_id)

    async def upsert_from_plant(
        self,
        plant_id: str,
        plant_name: str,
        category: Optional[str] = None,
    ) -> str:
        """
        Ensure a ppa_details row exists for the given plant.
        Syncs plant_id / plant_name / category from plant_master.
        Does not overwrite other PPA field values.
        """
        if not plant_id:
            raise ValueError("plant_id is required to upsert ppa_details")

        collection = self.db[self.collection_name]
        now = datetime.utcnow()
        result = await collection.update_one(
            {"plant_id": plant_id},
            {
                "$set": {
                    "plant_name": plant_name or "",
                    "category": category,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "plant_id": plant_id,
                    "status": "Active",
                    "expired_but_extended": False,
                    "ppa_files": [],
                    "created_at": now,
                },
            },
            upsert=True,
        )
        if result.upserted_id:
            return str(result.upserted_id)

        existing = await collection.find_one({"plant_id": plant_id}, {"_id": 1})
        return str(existing["_id"]) if existing else ""

    async def bulk_upsert_from_plants(self, plants: List[Dict[str, Any]]) -> Dict[str, int]:
        """Upsert ppa_details stubs for plants — syncs plant_id, plant_name, category."""
        if not plants:
            return {"inserted": 0, "updated": 0, "total": 0}

        collection = self.db[self.collection_name]
        now = datetime.utcnow()
        ops = []
        seen = set()
        for plant in plants:
            plant_id = (plant.get("plant_id") or "").strip() if plant.get("plant_id") else ""
            if not plant_id or plant_id in seen:
                continue
            seen.add(plant_id)
            plant_name = plant.get("plant_name") or ""
            category = plant.get("category")
            ops.append(
                UpdateOne(
                    {"plant_id": plant_id},
                    {
                        "$set": {
                            "plant_name": plant_name,
                            "category": category,
                            "updated_at": now,
                        },
                        "$setOnInsert": {
                            "plant_id": plant_id,
                            "status": "Active",
                            "expired_but_extended": False,
                            "ppa_files": [],
                            "created_at": now,
                        },
                    },
                    upsert=True,
                )
            )

        if not ops:
            return {"inserted": 0, "updated": 0, "total": 0}

        result = await collection.bulk_write(ops)
        return {
            "inserted": result.upserted_count,
            "updated": result.modified_count,
            "total": result.upserted_count + result.modified_count,
        }

    async def get_by_id(self, ppa_oid: str) -> Optional[Dict[str, Any]]:
        return await self.find_by_id(self.collection_name, ppa_oid)

    async def get_by_plant_id(self, plant_id: str) -> Optional[Dict[str, Any]]:
        return await self.find_one(self.collection_name, {"plant_id": plant_id})

    async def list_ppa(
        self,
        filters: Optional[Dict[str, Any]] = None,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
        sort: Optional[List[tuple]] = None,
    ) -> Dict[str, Any]:
        query: Dict[str, Any] = dict(filters or {})

        if search and search.strip():
            pattern = re.escape(search.strip())
            query["$or"] = [
                {"plant_name": {"$regex": pattern, "$options": "i"}},
                {"plant_id": {"$regex": pattern, "$options": "i"}},
                {"category": {"$regex": pattern, "$options": "i"}},
            ]

        return await self.get_paginated_response(
            collection_name=self.collection_name,
            filter=query,
            page=page,
            page_size=page_size,
            sort=sort or [("plant_name", 1)],
        )

    async def update_ppa(self, ppa_oid: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not ObjectId.is_valid(ppa_oid):
            return None
        # plant_id / plant_name / category are owned by plant_master
        updates = {
            k: v for k, v in updates.items() if k not in ("_id", "plant_id", "plant_name", "category")
        }
        updates["updated_at"] = datetime.utcnow()
        collection = self.db[self.collection_name]
        await collection.update_one({"_id": ObjectId(ppa_oid)}, {"$set": updates})
        return await self.find_by_id(self.collection_name, ppa_oid)

    async def update_plant_name(self, plant_id: str, plant_name: str) -> bool:
        if not plant_id:
            return False
        collection = self.db[self.collection_name]
        result = await collection.update_one(
            {"plant_id": plant_id},
            {"$set": {"plant_name": plant_name, "updated_at": datetime.utcnow()}},
        )
        return result.matched_count > 0

    async def update_synced_plant_fields(
        self,
        plant_id: str,
        plant_name: Optional[str] = None,
        category: Optional[str] = None,
    ) -> bool:
        if not plant_id:
            return False
        updates: Dict[str, Any] = {"updated_at": datetime.utcnow()}
        if plant_name is not None:
            updates["plant_name"] = plant_name
        if category is not None:
            updates["category"] = category
        collection = self.db[self.collection_name]
        result = await collection.update_one({"plant_id": plant_id}, {"$set": updates})
        return result.matched_count > 0

    async def delete_by_id(self, ppa_oid: str) -> bool:
        if not ObjectId.is_valid(ppa_oid):
            return False
        collection = self.db[self.collection_name]
        result = await collection.delete_one({"_id": ObjectId(ppa_oid)})
        return result.deleted_count > 0

    async def delete_by_plant_id(self, plant_id: str) -> bool:
        if not plant_id:
            return False
        collection = self.db[self.collection_name]
        result = await collection.delete_one({"plant_id": plant_id})
        return result.deleted_count > 0

    async def push_file(self, ppa_oid: str, file_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not ObjectId.is_valid(ppa_oid):
            return None
        collection = self.db[self.collection_name]
        await collection.update_one(
            {"_id": ObjectId(ppa_oid)},
            {
                "$push": {"ppa_files": file_info},
                "$set": {"updated_at": datetime.utcnow()},
            },
        )
        return await self.find_by_id(self.collection_name, ppa_oid)

    async def remove_file(self, ppa_oid: str, file_id: str) -> Optional[Dict[str, Any]]:
        if not ObjectId.is_valid(ppa_oid):
            return None
        collection = self.db[self.collection_name]
        await collection.update_one(
            {"_id": ObjectId(ppa_oid)},
            {
                "$pull": {"ppa_files": {"file_id": file_id}},
                "$set": {"updated_at": datetime.utcnow()},
            },
        )
        return await self.find_by_id(self.collection_name, ppa_oid)


ppa_details_repository = PpaDetailsRepository()
