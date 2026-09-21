"""
Scheduler URS Serve Repository

Writes un-requisitioned surplus into ``urs_serve``: one document per plant per
time block per day.

The Motor client and the collection are resolved lazily, on first use, rather
than in ``__init__``. Repository singletons in this service are constructed at
import time, and a Motor client bound to whichever event loop happened to be
current at import will answer every later query with an empty result instead of
an error. That failure mode has already cost this platform a silent outage, so
nothing here touches the driver until it is inside the loop that will use it.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorCollection
from pymongo import ASCENDING, UpdateOne

from libs.database.mongo_client_factory import get_motor_client
from ..core.config import settings

logger = logging.getLogger(__name__)

COLLECTION = "urs_serve"

#: What makes two URS rows the same row. One plant can appear under several
#: schedule entries in a single payload; the parser has already summed those,
#: so a re-run of the same revision must replace rather than accumulate.
IDENTITY_FIELDS = ("scheduled_at", "time_block", "plant_id")


class UrsServeRepository:
    """Reads and writes the ``urs_serve`` collection."""

    def __init__(self) -> None:
        self._collection: Optional[AsyncIOMotorCollection] = None
        self._indexes_ready = False

    @property
    def collection(self) -> AsyncIOMotorCollection:
        if self._collection is None:
            client = get_motor_client(settings.MONGODB_URI, "shared_repository_client")
            self._collection = client[settings.MONGODB_DATABASE][COLLECTION]
        return self._collection

    async def ensure_indexes(self) -> None:
        """Create indexes on first write. Never called from a constructor."""
        if self._indexes_ready:
            return
        try:
            await self.collection.create_index(
                [(field, ASCENDING) for field in IDENTITY_FIELDS],
                name="uq_urs_day_block_plant",
                unique=True,
                background=True,
            )
            # The report reads a day and a block range for every plant at once.
            await self.collection.create_index(
                [("scheduled_at", ASCENDING), ("time_block", ASCENDING)],
                name="ix_urs_day_block",
                background=True,
            )
            self._indexes_ready = True
        except Exception as e:
            # A missing index makes the report slow, not wrong. Losing the
            # day's URS because an index could not be built would be worse.
            logger.warning("urs_serve index ensure skipped: %s", e)

    @staticmethod
    def identity_of(document: Dict[str, Any]) -> Dict[str, Any]:
        return {field: document.get(field) for field in IDENTITY_FIELDS}

    async def bulk_upsert(self, documents: List[Dict[str, Any]]) -> Dict[str, int]:
        """Replace each block's row in place. Returns what changed."""
        if not documents:
            return {"upserted": 0, "modified": 0, "matched": 0}

        await self.ensure_indexes()
        now = datetime.now()
        operations = [
            UpdateOne(
                self.identity_of(doc),
                {
                    # created_at only on the first write, so a row remembers
                    # when the surplus was first reported and not merely when
                    # the latest revision restated it.
                    "$set": {**doc, "updated_at": now},
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )
            for doc in documents
        ]

        try:
            # Unordered: one bad row must not discard the rest of the day.
            result = await self.collection.bulk_write(operations, ordered=False)
            return {
                "upserted": len(result.upserted_ids or {}),
                "modified": result.modified_count,
                "matched": result.matched_count,
            }
        except Exception as e:
            logger.error("urs_serve bulk upsert failed: %s", e, exc_info=True)
            return {"upserted": 0, "modified": 0, "matched": 0, "error": str(e)}

    async def find_by_day(
        self,
        day: datetime,
        from_block: int = 1,
        to_block: int = 96,
    ) -> List[Dict[str, Any]]:
        """Every plant's URS for one day, within a block range."""
        start = datetime(day.year, day.month, day.day)
        cursor = self.collection.find(
            {
                "scheduled_at": {"$gte": start, "$lt": start + timedelta(days=1)},
                "time_block": {"$gte": from_block, "$lte": to_block},
            },
            {"_id": 0},
        ).sort([("time_block", ASCENDING), ("plant_id", ASCENDING)])
        return await cursor.to_list(length=None)


urs_serve_repository = UrsServeRepository()
