"""
Power Exchange Repository
Repository for managing power exchange records (key, market, exchange, value)
"""

from typing import Dict, Any, List, Optional
from bson import ObjectId
from datetime import datetime
import logging
from .base_repository import GenericRepository

logger = logging.getLogger(__name__)


class PowerExchangeRepository(GenericRepository):
    """Repository for the power_exchange collection"""

    def __init__(self):
        super().__init__()
        self.collection_name = "power_exchange"

    async def create(self, data: Dict[str, Any]) -> Any:
        """Insert a new power exchange record"""
        now = datetime.now()
        data["created_at"] = now
        data["updated_at"] = now
        return await self.insert_one_async(self.collection_name, data)

    async def find_by_id(self, id: str) -> Optional[Dict[str, Any]]:
        """Find a power exchange record by its ObjectId"""
        if not ObjectId.is_valid(id):
            return None
        return await self.find_one_async(self.collection_name, {"_id": ObjectId(id)})

    async def find_by_key(
        self, key: str, market: str, exchange: str, exclude_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Find a record by its unique combination of key, market and exchange"""
        query: Dict[str, Any] = {"key": key, "market": market, "exchange": exchange}
        if exclude_id and ObjectId.is_valid(exclude_id):
            query["_id"] = {"$ne": ObjectId(exclude_id)}
        return await self.find_one_async(self.collection_name, query)

    async def get_paginated(
        self,
        filters: Optional[Dict[str, Any]] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        """List power exchange records with optional filters and pagination"""
        result = await self.get_paginated_response(
            self.collection_name,
            filter=filters or {},
            sort=[("_id", -1)],
            page=page,
            page_size=page_size,
        )
        result["items"] = [self._serialize(item) for item in result.get("items", [])]
        return result

    async def update(self, id: str, data: Dict[str, Any]) -> Any:
        """Update a power exchange record by ObjectId"""
        data["updated_at"] = datetime.now()
        return await self.update_one_async(
            self.collection_name,
            {"_id": ObjectId(id)},
            {"$set": data}
        )

    async def delete(self, id: str) -> Any:
        """Delete a power exchange record by ObjectId"""
        return await self.delete_one(self.collection_name, {"_id": ObjectId(id)})

    @staticmethod
    def _serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
        if doc and doc.get("_id") is not None:
            doc["_id"] = str(doc["_id"])
        for field in ("created_at", "updated_at"):
            if isinstance(doc.get(field), datetime):
                doc[field] = doc[field].isoformat()
        return doc


# Singleton instance
power_exchange_repository = PowerExchangeRepository()
