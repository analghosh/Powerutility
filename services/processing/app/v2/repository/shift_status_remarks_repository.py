"""Per-block remarks for the Shift Status Report (`epms.shift_status_remarks`).

One document per delivery date and block — the key the shift sheet itself uses — so
a remark written in a shift view shows in the full-day view and in every download
of that block. The document records who wrote it and when; the screen's Shift
Person Name is derived from that, so it is never typed.

A remark is final once submitted (PTC, 2026-09-29): `insert_new` writes a block
only if it has no remark yet, with `$setOnInsert` on the unique (date, block) key,
so two operators saving the same block at once cannot both win and neither can
overwrite the other. There is no update or delete path.
"""

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from pymongo import ASCENDING, UpdateOne

from ...core.config import settings
from .base_repository import GenericRepository

logger = logging.getLogger(__name__)

#: Longest remark kept. The sheet's remarks are a sentence; this stops one cell
#: from becoming a document.
MAX_REMARK_LENGTH = 500

_NO_ID = {"_id": 0}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ShiftStatusRemarksRepository(GenericRepository):

    def __init__(self, collection_name: Optional[str] = None):
        super().__init__()
        self.collection_name = (
            collection_name or settings.MONGODB_SHIFT_STATUS_REMARKS_COLLECTION)
        self._collection_override = None
        self._index_ready = False

    @property
    def collection(self):
        """Lazy, like every v2 repository (see `GenericRepository.db`)."""
        override = getattr(self, "_collection_override", None)
        if override is not None:
            return override
        return self.db[self.collection_name]

    @collection.setter
    def collection(self, value):
        self._collection_override = value

    async def _ensure_index(self) -> None:
        """The unique key that makes a save an upsert, created once per process."""
        if self._index_ready:
            return
        await self.collection.create_index(
            [("date", ASCENDING), ("time_block", ASCENDING)],
            unique=True, name="uniq_date_time_block")
        self._index_ready = True

    async def find_for_dates(self, dates: Iterable[date]) -> Dict[tuple, Dict[str, Any]]:
        """`{(iso_date, time_block): remark document}` for the given delivery dates."""
        keys = sorted({d.isoformat() for d in dates})
        if not keys:
            return {}
        cursor = self.collection.find({"date": {"$in": keys}}, _NO_ID)
        found = {}
        async for doc in cursor:
            found[(doc["date"], int(doc["time_block"]))] = doc
        return found

    async def insert_new(self, entries: List[Dict[str, Any]], *, user_id: str,
                         username: str) -> Dict[str, Any]:
        """Write each `{date, time_block, remark}` whose block has no remark yet.

        Returns the number written and the blocks that already had a remark (left
        untouched) — normally none, since the service checks first; a block appears
        here only when another operator saved it in between.
        """
        await self._ensure_index()
        if not entries:
            return {"saved": 0, "locked": []}
        now = _utcnow()
        ops = [
            UpdateOne(
                {"date": entry["date"], "time_block": int(entry["time_block"])},
                {"$setOnInsert": {
                    "remark": entry["remark"].strip()[:MAX_REMARK_LENGTH],
                    "updated_by": str(user_id),
                    "updated_by_name": username,
                    "updated_at": now,
                    "created_at": now,
                }},
                upsert=True,
            )
            for entry in entries
        ]
        result = await self.collection.bulk_write(ops, ordered=False)
        written = set((result.upserted_ids or {}).keys())
        locked = [
            {"date": entry["date"], "time_block": int(entry["time_block"])}
            for index, entry in enumerate(entries) if index not in written
        ]
        return {"saved": len(written), "locked": locked}


shift_status_remarks_repository = ShiftStatusRemarksRepository()
