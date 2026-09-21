"""
Storage for the "Other Reports" category — the file, kept as uploaded.

Separate from ``exchange_uploaded_report`` on purpose. That collection holds one
document per (exchange, market, category, delivery date, portfolio), enforced by
a unique index, because the exchange publishes exactly one obligation report per
that tuple and re-publishes corrections under the same identity. Other Reports is
the opposite case: several unrelated files can land for the same day, and there
is no portfolio, exchange or market in them to tell them apart. Widening the
shared index to allow that would have meant dropping and rebuilding a unique
index on live UAT and PROD data, to make it looser for every category at once.

Two collections here:

* ``exchange_other_report`` — one metadata document per uploaded file.
* ``exchange_other_report_file`` — the bytes, one document per file, keyed by
  the same id.

The bytes are kept apart from the metadata so a listing never drags them along:
a 20-row page would otherwise be the whole archive. They are stored inline
rather than in GridFS because the uploader caps a file at 10 MB, comfortably
inside MongoDB's 16 MB document limit, and inline storage needs no second
driver API in a service where an unawaited call on the wrong event loop has
caused a silent outage before.

Indexes are created on first write rather than in the constructor, for that
same reason: repository constructors run at import time here.
"""

import logging
from typing import Any, Dict, List, Optional

from bson import ObjectId
from bson.binary import Binary

from .base_repository import GenericRepository

logger = logging.getLogger(__name__)

COLLECTION = "exchange_other_report"
FILE_COLLECTION = "exchange_other_report_file"

#: What makes two uploads the same file: the day it is for, and its name.
#: Two different files for one day sit side by side; re-uploading the same name
#: replaces rather than duplicating.
#:
#: Exchange and market are deliberately absent. An Other Report can be about
#: anything - a desk note, a regulatory circular - so the uploader does not ask
#: for them, and including a field nobody fills would make every upload unique
#: on a null and defeat the replace.
IDENTITY_FIELDS = (
    "delivery_date_iso",
    "source_filename",
)


class OtherReportRepository(GenericRepository):
    """Reads and writes files uploaded under the Other Reports category."""

    def __init__(self) -> None:
        super().__init__()
        self._indexes_ready = False

    async def _ensure_indexes(self) -> None:
        if self._indexes_ready:
            return
        await self.create_index(
            COLLECTION,
            [(field, 1) for field in IDENTITY_FIELDS],
            name="uq_other_report_day_file",
            unique=True,
        )
        # Both list screens are keyed on the upload day.
        await self.create_index(
            COLLECTION,
            [("uploaded_on_ist_date", -1), ("uploaded_at", -1)],
            name="ix_other_report_upload_date",
        )
        self._indexes_ready = True

    @staticmethod
    def identity_of(document: Dict[str, Any]) -> Dict[str, Any]:
        return {field: document.get(field) for field in IDENTITY_FIELDS}

    async def save(
        self,
        document: Dict[str, Any],
        content: bytes,
        content_type: str,
        upload_event: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Store one file and its metadata, replacing any upload with the same
        identity. Returns the stored document and whether it replaced one.
        """
        await self._ensure_indexes()
        identity = self.identity_of(document)

        existing = await self.db[COLLECTION].find_one(identity, {"_id": 1})
        await self.db[COLLECTION].update_one(
            identity,
            {
                "$set": document,
                "$inc": {"upload_count": 1},
                # Capped: the history answers "did this change after I looked
                # at it", which the last few uploads already do. An unbounded
                # array would grow the document towards the 16 MB limit.
                "$push": {"upload_history": {"$each": [upload_event], "$slice": -20}},
            },
            upsert=True,
        )
        stored = await self.db[COLLECTION].find_one(identity)

        # Written second and keyed by the metadata's own id, so a failure here
        # leaves a row whose file is missing rather than a file nothing points
        # at. The download route reports the former; the latter would be
        # invisible.
        await self.db[FILE_COLLECTION].update_one(
            {"_id": stored["_id"]},
            {
                "$set": {
                    "content": Binary(content),
                    "content_type": content_type,
                    "source_filename": document.get("source_filename"),
                    "file_size_bytes": len(content),
                }
            },
            upsert=True,
        )
        return {"document": stored, "replaced": existing is not None}

    async def find_many(
        self,
        filters: Dict[str, Any],
        limit: int = 50,
        skip: int = 0,
    ) -> List[Dict[str, Any]]:
        cursor = (
            self.db[COLLECTION]
            .find(filters, {"upload_history": 0})
            .sort([("uploaded_on_ist_date", -1), ("uploaded_at", -1)])
            .skip(skip)
            .limit(limit)
        )
        return await cursor.to_list(length=limit)

    async def count(self, filters: Dict[str, Any]) -> int:
        return await self.db[COLLECTION].count_documents(filters)

    async def upload_dates(
        self,
        filters: Dict[str, Any],
        limit: int = 60,
    ) -> List[Dict[str, Any]]:
        """Which upload days have files, newest first — the picker's hints."""
        pipeline = [
            {"$match": filters},
            {
                "$group": {
                    "_id": "$uploaded_on_ist_date",
                    "report_count": {"$sum": 1},
                    "last_uploaded_at": {"$max": "$uploaded_at"},
                }
            },
            {"$sort": {"_id": -1}},
            {"$limit": limit},
        ]
        grouped = await self.db[COLLECTION].aggregate(pipeline).to_list(length=limit)
        return [
            {
                "date": row["_id"],
                "report_count": row.get("report_count", 0),
                "last_uploaded_at": row.get("last_uploaded_at"),
            }
            for row in grouped
        ]

    async def find_by_identity(self, identity: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return await self.db[COLLECTION].find_one(identity)

    async def find_one_by_id(self, report_id: str) -> Optional[Dict[str, Any]]:
        if not ObjectId.is_valid(report_id):
            return None
        return await self.db[COLLECTION].find_one({"_id": ObjectId(report_id)})

    async def find_file(self, report_id: str) -> Optional[Dict[str, Any]]:
        """The stored bytes for one report, or None when the row has no file."""
        if not ObjectId.is_valid(report_id):
            return None
        return await self.db[FILE_COLLECTION].find_one({"_id": ObjectId(report_id)})


other_report_repository = OtherReportRepository()
