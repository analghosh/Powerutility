"""Mongo access for the epms.notifications collection."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId

from libs.database.base_repository import BaseRepository
from libs.notifications.models import Notification

logger = logging.getLogger(__name__)

COLLECTION_NAME = "notifications"
TTL_SECONDS = 7776000  # 90 days
MAX_LIMIT = 200


class NotificationRepository(BaseRepository):
    def __init__(self, mongodb_uri: str, database_name: str):
        super().__init__(COLLECTION_NAME, mongodb_uri, database_name)

    async def ensure_indexes(self) -> None:
        """Idempotent. Called once at service startup, never at import time.

        Creating indexes in a constructor that runs at import is what caused the
        Motor event-loop misbind elsewhere in this codebase; do not move this.
        """
        await self.collection.create_index([("user_id", 1), ("created_at", -1)])
        await self.collection.create_index([("user_id", 1), ("read_at", 1)])
        # The tabbed /notifications list filters on category AND still sorts
        # by created_at. The {user_id, created_at} index above can only serve
        # the user_id prefix for that query; category would be a post-filter
        # scanning every one of the user's rows instead of using the index.
        await self.collection.create_index(
            [("user_id", 1), ("category", 1), ("created_at", -1)]
        )
        await self.collection.create_index(
            [("created_at", 1)], expireAfterSeconds=TTL_SECONDS
        )

    async def insert_many_for(self, notifications: List[Notification]) -> List[str]:
        if not notifications:
            return []
        result = await self.collection.insert_many(
            [n.to_document() for n in notifications]
        )
        return [str(i) for i in result.inserted_ids]

    async def list_for_user(
        self,
        user_id: str,
        limit: int = 50,
        skip: int = 0,
        category: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        # Clamp limit to valid range and skip to non-negative
        clamped_limit = max(1, min(limit, MAX_LIMIT))
        clamped_skip = max(0, skip)

        query: Dict[str, Any] = {"user_id": user_id}
        if category:
            query["category"] = category

        cursor = (
            self.collection.find(query)
            .sort("created_at", -1)
            .skip(clamped_skip)
            .limit(clamped_limit)
        )
        return [self._serialise(doc) async for doc in cursor]

    async def unread_count(self, user_id: str) -> int:
        return await self.collection.count_documents(
            {"user_id": user_id, "read_at": None}
        )

    async def mark_read(self, user_id: str, notification_id: str) -> bool:
        if not ObjectId.is_valid(notification_id):
            return False
        result = await self.collection.update_one(
            {"_id": ObjectId(notification_id), "user_id": user_id, "read_at": None},
            {"$set": {"read_at": datetime.now()}},
        )
        return result.modified_count > 0

    async def mark_all_read(self, user_id: str) -> int:
        result = await self.collection.update_many(
            {"user_id": user_id, "read_at": None},
            {"$set": {"read_at": datetime.now()}},
        )
        return result.modified_count

    @staticmethod
    def _serialise(doc: Dict[str, Any]) -> Dict[str, Any]:
        doc["_id"] = str(doc["_id"])
        for field in ("created_at", "read_at"):
            if isinstance(doc.get(field), datetime):
                doc[field] = doc[field].isoformat()
        return doc
