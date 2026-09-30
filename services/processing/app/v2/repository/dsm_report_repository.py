"""
Storage for the DSM Report category.

Three collections, and the split is the point:

* ``exchange_dsm_report`` — one document per upload: who sent it, when, which
  file, and the window of settlement days it turned out to cover.
* ``exchange_dsm_report_file`` — the bytes, keyed by the upload's id, so a
  listing never drags the workbook along with it.
* ``exchange_dsm_report_day`` — one document per settlement day, holding that
  day's blocks. This is what every screen reads.

The day collection is keyed on the settlement date alone, and that is the whole
reason it exists as a third collection rather than as an array inside the
upload. A DSM statement is republished with corrections, and the corrected file
rarely covers exactly the same window as the original: filing the figures under
the upload would leave two documents both claiming 3 April with nothing to say
which one is current. Keyed on the day, a re-upload replaces the days it
actually carries and leaves the rest alone, which is what a correction means.

Each day keeps a pointer back to the upload it came from, so "where did this
figure come from" is still answerable and the original file is still reachable.

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

COLLECTION = "exchange_dsm_report"
FILE_COLLECTION = "exchange_dsm_report_file"
DAY_COLLECTION = "exchange_dsm_report_day"

#: What makes two uploads the same upload: the file's name and the window it
#: covers. Re-sending the same statement replaces its row rather than adding a
#: second one; a corrected statement under a new name sits beside it, and the
#: day documents decide which figures are current.
IDENTITY_FIELDS = (
    "source_filename",
    "report_date_from",
    "report_date_to",
)


class DsmReportRepository(GenericRepository):
    """Reads and writes DSM statements and the settlement days inside them."""

    def __init__(self) -> None:
        super().__init__()
        self._indexes_ready = False

    async def _ensure_indexes(self) -> None:
        if self._indexes_ready:
            return
        await self.create_index(
            COLLECTION,
            [(field, 1) for field in IDENTITY_FIELDS],
            name="uq_dsm_report_file_window",
            unique=True,
        )
        await self.create_index(
            COLLECTION,
            [("uploaded_on_ist_date", -1), ("uploaded_at", -1)],
            name="ix_dsm_report_upload_date",
        )
        # One document per settlement day, whichever upload delivered it.
        await self.create_index(
            DAY_COLLECTION,
            [("report_date", 1)],
            name="uq_dsm_report_day",
            unique=True,
        )
        # The screen's two questions: a window of settlement days, and a
        # window of upload days. Both are plain YYYY-MM-DD strings, so a range
        # compares lexically.
        await self.create_index(
            DAY_COLLECTION,
            [("uploaded_on_ist_date", -1), ("report_date", 1)],
            name="ix_dsm_report_day_upload_date",
        )
        self._indexes_ready = True

    @staticmethod
    def identity_of(document: Dict[str, Any]) -> Dict[str, Any]:
        return {field: document.get(field) for field in IDENTITY_FIELDS}

    # -- writes ------------------------------------------------------------

    async def existing_days(self, report_dates: List[str]) -> List[Dict[str, Any]]:
        """
        Which of these settlement days are already stored, and from what.

        Asked before a write so the operator can be told they are about to
        overwrite figures, rather than finding out afterwards.
        """
        await self._ensure_indexes()
        cursor = self.db[DAY_COLLECTION].find(
            {"report_date": {"$in": report_dates}},
            {
                "report_date": 1,
                "source_filename": 1,
                "uploaded_at": 1,
                "block_count": 1,
                # Carried so the caller can name the statement a day came from
                # — without it the "already uploaded" question shows a row of
                # blanks where the file, the uploader and the time should be.
                "source_report_id": 1,
            },
        ).sort([("report_date", 1)])
        return await cursor.to_list(length=len(report_dates) or 1)

    async def save(
        self,
        document: Dict[str, Any],
        *,
        days: List[Dict[str, Any]],
        content: bytes,
        content_type: str,
        upload_event: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Store one statement: its metadata, its bytes and each of its days.

        Returns the stored upload document, whether it replaced an upload of
        its own, and which settlement days it overwrote.
        """
        await self._ensure_indexes()
        identity = self.identity_of(document)

        existing = await self.db[COLLECTION].find_one(identity, {"_id": 1})
        await self.db[COLLECTION].update_one(
            identity,
            {
                "$set": document,
                "$inc": {"upload_count": 1},
                # Capped for the same reason as everywhere else here: an
                # unbounded history grows the document towards the 16 MB limit,
                # and the last few uploads already answer "did this change
                # after I looked at it".
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

        replaced_days: List[str] = []
        for day in days:
            result = await self.db[DAY_COLLECTION].update_one(
                {"report_date": day["report_date"]},
                {"$set": {**day, "source_report_id": stored["_id"]}},
                upsert=True,
            )
            if result.matched_count:
                replaced_days.append(day["report_date"])

        return {
            "document": stored,
            "replaced": existing is not None,
            "replaced_days": replaced_days,
        }

    # -- reads -------------------------------------------------------------

    async def find_days(
        self,
        filters: Dict[str, Any],
        limit: int = 400,
        skip: int = 0,
        *,
        with_blocks: bool = True,
    ) -> List[Dict[str, Any]]:
        projection = None if with_blocks else {"blocks": 0}
        cursor = (
            self.db[DAY_COLLECTION]
            .find(filters, projection)
            .sort([("report_date", 1)])
            .skip(skip)
            .limit(limit)
        )
        return await cursor.to_list(length=limit)

    async def count_days(self, filters: Dict[str, Any]) -> int:
        return await self.db[DAY_COLLECTION].count_documents(filters)

    async def day_dates(self, limit: int = 400) -> List[Dict[str, Any]]:
        """Settlement days that have figures, newest first — the picker's hints."""
        cursor = (
            self.db[DAY_COLLECTION]
            .find({}, {"report_date": 1, "block_count": 1, "uploaded_on_ist_date": 1})
            .sort([("report_date", -1)])
            .limit(limit)
        )
        rows = await cursor.to_list(length=limit)
        return [
            {
                "date": row.get("report_date"),
                "block_count": row.get("block_count", 0),
                "uploaded_on": row.get("uploaded_on_ist_date"),
            }
            for row in rows
        ]

    async def upload_dates(self, limit: int = 60) -> List[Dict[str, Any]]:
        """Upload days that delivered settlement figures, newest first."""
        pipeline = [
            {
                "$group": {
                    "_id": "$uploaded_on_ist_date",
                    "day_count": {"$sum": 1},
                    "last_uploaded_at": {"$max": "$uploaded_at"},
                }
            },
            {"$sort": {"_id": -1}},
            {"$limit": limit},
        ]
        grouped = await self.db[DAY_COLLECTION].aggregate(pipeline).to_list(length=limit)
        return [
            {
                "date": row["_id"],
                "day_count": row.get("day_count", 0),
                "last_uploaded_at": row.get("last_uploaded_at"),
            }
            for row in grouped
        ]

    async def find_many(
        self,
        filters: Dict[str, Any],
        limit: int = 50,
        skip: int = 0,
    ) -> List[Dict[str, Any]]:
        """Uploaded statements, newest upload first."""
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

    async def find_one_by_id(self, report_id: str) -> Optional[Dict[str, Any]]:
        if not ObjectId.is_valid(report_id):
            return None
        return await self.db[COLLECTION].find_one({"_id": ObjectId(report_id)})

    async def find_file(self, report_id: str) -> Optional[Dict[str, Any]]:
        """The stored bytes for one statement, or None when the row has no file."""
        if not ObjectId.is_valid(report_id):
            return None
        return await self.db[FILE_COLLECTION].find_one({"_id": ObjectId(report_id)})


dsm_report_repository = DsmReportRepository()
