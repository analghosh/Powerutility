"""
Storage for the month-keyed report categories — LTOA and STOA.

A third collection rather than a filter on an existing one, and the reason is
the identity. ``exchange_uploaded_report`` is unique on (exchange, market,
category, delivery date, portfolio); ``exchange_other_report`` is unique on
(delivery date, filename). Neither shape fits a report that covers a calendar
month and has no exchange, market or delivery day at all — filing one under a
delivery date would mean inventing a day the power flowed on, and every month's
report would then sit on whichever arbitrary day was chosen.

Sharing ``exchange_other_report`` would also have meant widening its unique
index on live UAT and PROD data, loosening it for every store-only category at
once so that one of them could be keyed differently. A new collection needs no
migration and keeps LTOA and STOA out of the Other Reports listing, which is
the catch-all for everything that has no category of its own.

Two collections, the same split the Other Reports storage uses and for the same
reason — a listing must never drag whole files along with it:

* ``exchange_period_report`` — one metadata document per uploaded file.
* ``exchange_period_report_file`` — the bytes, keyed by the same id.

Indexes are created on first write rather than in the constructor, because
repository constructors run at import time in this service and an unawaited
call on the wrong event loop has caused a silent outage before.
"""

import logging
from typing import Any, Dict, List, Optional

from bson import ObjectId
from bson.binary import Binary

from .base_repository import GenericRepository

logger = logging.getLogger(__name__)

COLLECTION = "exchange_period_report"
FILE_COLLECTION = "exchange_period_report_file"

#: What makes two uploads the same file: the category, the month it covers and
#: its name.
#:
#: The category is part of the identity here, unlike in the Other Reports
#: storage, because two categories share this collection — an LTOA and a STOA
#: report for September 2021 can easily arrive under the same filename, and
#: without the category one would silently replace the other.
IDENTITY_FIELDS = (
    "report_category",
    "period_year",
    "period_month",
    "source_filename",
)


class PeriodReportRepository(GenericRepository):
    """Reads and writes files uploaded under a month-keyed category."""

    def __init__(self) -> None:
        super().__init__()
        self._indexes_ready = False

    async def _ensure_indexes(self) -> None:
        if self._indexes_ready:
            return
        await self.create_index(
            COLLECTION,
            [(field, 1) for field in IDENTITY_FIELDS],
            name="uq_period_report_month_file",
            unique=True,
        )
        # The screen opens on one category and one month, which is exactly
        # this prefix. Sorted by upload time so the newest file for a month
        # is the first row.
        await self.create_index(
            COLLECTION,
            [
                ("report_category", 1),
                ("period_year", -1),
                ("period_month", -1),
                ("uploaded_at", -1),
            ],
            name="ix_period_report_category_month",
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
                # Capped for the same reason as everywhere else here: the
                # history answers "did this change after I looked at it",
                # which the last few uploads already do, and an unbounded
                # array grows the document towards the 16 MB limit.
                "$push": {"upload_history": {"$each": [upload_event], "$slice": -20}},
            },
            upsert=True,
        )
        stored = await self.db[COLLECTION].find_one(identity)

        # Written second and keyed by the metadata's own id, so a failure here
        # leaves a row whose file is missing rather than a file nothing points
        # at. The download route reports the former; the latter is invisible.
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
            .sort(
                [
                    ("period_year", -1),
                    ("period_month", -1),
                    ("uploaded_at", -1),
                ]
            )
            .skip(skip)
            .limit(limit)
        )
        return await cursor.to_list(length=limit)

    async def count(self, filters: Dict[str, Any]) -> int:
        return await self.db[COLLECTION].count_documents(filters)

    async def periods(
        self,
        filters: Dict[str, Any],
        limit: int = 60,
    ) -> List[Dict[str, Any]]:
        """
        Which months have files, newest first — the picker's hints.

        A month picker cannot tell "nothing was uploaded for September" from
        "I picked the wrong month" on its own, which is the whole reason this
        exists.
        """
        pipeline = [
            {"$match": filters},
            {
                "$group": {
                    "_id": {"year": "$period_year", "month": "$period_month"},
                    "report_count": {"$sum": 1},
                    "last_uploaded_at": {"$max": "$uploaded_at"},
                }
            },
            {"$sort": {"_id.year": -1, "_id.month": -1}},
            {"$limit": limit},
        ]
        grouped = await self.db[COLLECTION].aggregate(pipeline).to_list(length=limit)
        return [
            {
                "year": row["_id"].get("year"),
                "month": row["_id"].get("month"),
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


period_report_repository = PeriodReportRepository()
