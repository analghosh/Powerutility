"""What the shift desk types on the Shift Status Report (`epms.shift_status_entries`).

Two kinds of entry share one collection, keyed on (date, time_block):

* a remark — one per delivery date and block 1-96, the key the shift sheet itself
  uses, so a remark written in a shift view shows in the full-day view and in every
  download of that block;
* the Shift Person Name — one per date, on block 0, shown in every shift view of
  that date.

The report reads both for its dates in one query, and the one unique index keeps
each key to a single document.

A remark is final once submitted (PTC, 2026-09-29): `insert_new` writes a block
only if it has no remark yet, with `$setOnInsert` on the unique key, so two
operators saving the same block at once cannot both win and neither can overwrite
the other. There is no update or delete path for remarks.

The Shift Person Name can be changed as often as the desk likes (PTC, 2026-09-30):
`set_shift_person` overwrites it, and records who saved it last and when.

The collection was `shift_status_remarks` while it held only remarks; the first use
in a process renames that collection if it is still there.
"""

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from pymongo import ASCENDING, UpdateOne
from pymongo.errors import OperationFailure

from ...core.config import settings
from .base_repository import GenericRepository

logger = logging.getLogger(__name__)

#: Longest remark kept. The sheet's remarks are a sentence; this stops one cell
#: from becoming a document.
MAX_REMARK_LENGTH = 500

#: The block a date's Shift Person Name is stored on. Remarks are blocks 1-96.
SHIFT_PERSON_BLOCK = 0

KIND_REMARK = "remark"
KIND_SHIFT_PERSON = "shift_person"

#: The collection's name before it held anything but remarks.
LEGACY_COLLECTION = "shift_status_remarks"

#: Mongo's codes for a rename another process already made.
_ALREADY_RENAMED = {26, 48}  # NamespaceNotFound, NamespaceExists

_NO_ID = {"_id": 0}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ShiftStatusEntriesRepository(GenericRepository):

    def __init__(self, collection_name: Optional[str] = None,
                 legacy_collection_name: Optional[str] = LEGACY_COLLECTION):
        super().__init__()
        self.collection_name = (
            collection_name or settings.MONGODB_SHIFT_STATUS_ENTRIES_COLLECTION)
        self.legacy_collection_name = legacy_collection_name
        self._collection_override = None
        self._ready = False

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

    async def _adopt_legacy_collection(self) -> None:
        """Rename `shift_status_remarks` to this collection if that was never done.

        Every process tries it on first use; the rename is atomic, so when two race,
        one moves the collection and the other is told it is gone and carries on.
        """
        legacy = self.legacy_collection_name
        target = self.collection
        if not legacy or legacy == target.name:
            return
        database = target.database
        names = set(await database.list_collection_names(
            filter={"name": {"$in": [legacy, target.name]}}))
        if legacy not in names:
            return
        if target.name in names:
            logger.warning("Both %s and %s exist; the remarks in %s are not read",
                           legacy, target.name, legacy)
            return
        try:
            await database[legacy].rename(target.name)
            logger.info("Renamed collection %s to %s", legacy, target.name)
        except OperationFailure as exc:
            if exc.code not in _ALREADY_RENAMED:
                raise

    async def _ensure_ready(self) -> None:
        """The rename above and the unique key, done once per process."""
        if self._ready:
            return
        await self._adopt_legacy_collection()
        await self.collection.create_index(
            [("date", ASCENDING), ("time_block", ASCENDING)],
            unique=True, name="uniq_date_time_block")
        self._ready = True

    async def find_for_dates(self, dates: Iterable[date]) -> Dict[tuple, Dict[str, Any]]:
        """`{(iso_date, time_block): entry}` for the given dates, both kinds.

        A date's Shift Person Name is at `(iso_date, SHIFT_PERSON_BLOCK)`.
        """
        keys = sorted({d.isoformat() for d in dates})
        if not keys:
            return {}
        await self._ensure_ready()
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
        await self._ensure_ready()
        if not entries:
            return {"saved": 0, "locked": []}
        now = _utcnow()
        ops = [
            UpdateOne(
                {"date": entry["date"], "time_block": int(entry["time_block"])},
                {"$setOnInsert": {
                    "kind": KIND_REMARK,
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

    async def set_shift_person(self, iso_date: str, names: str, *, user_id: str,
                               username: str) -> Dict[str, Any]:
        """Save a date's Shift Person Name, replacing any earlier one."""
        await self._ensure_ready()
        now = _utcnow()
        await self.collection.update_one(
            {"date": iso_date, "time_block": SHIFT_PERSON_BLOCK},
            {"$set": {
                "kind": KIND_SHIFT_PERSON,
                "names": names,
                "updated_by": str(user_id),
                "updated_by_name": username,
                "updated_at": now,
            }, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        return {"date": iso_date, "names": names, "updated_by_name": username,
                "updated_at": now}


shift_status_entries_repository = ShiftStatusEntriesRepository()
