"""
Storage for the reports uploaded through the Report Uploader screen.

One document per (exchange, market, category, delivery date, portfolio). That
tuple is what identifies a report: the exchange publishes one per portfolio per
market per delivery day, and re-publishes a corrected one under the same
identity when it has to. So an upload replaces rather than duplicates, and the
document keeps a short history of who uploaded it and when, because "this
report changed after I looked at it" is otherwise invisible.

The unique index is created on first write rather than in the constructor.
Repository constructors in this service run at import time, and an unawaited
index call in one is what silently bound Motor to the wrong event loop and made
every processing query return an empty list.
"""

import logging
from typing import Any, Dict, List, Optional

from .base_repository import GenericRepository

logger = logging.getLogger(__name__)

COLLECTION = "exchange_uploaded_report"

IDENTITY_FIELDS = (
    "report_category",
    "exchange",
    "market_type",
    "delivery_date_iso",
    "portfolio_code",
)

#: Fields excluded from list responses. The block arrays are 96 rows each and a
#: listing never shows them; sending them would make a 20-row page a megabyte.
LIST_PROJECTION = {
    "trade_blocks": 0,
    "sell_blocks": 0,
    "upload_history": 0,
}


class ExchangeReportRepository(GenericRepository):
    """Reads and writes uploaded exchange reports."""

    def __init__(self) -> None:
        super().__init__()
        self._indexes_ready = False

    async def _ensure_indexes(self) -> None:
        if self._indexes_ready:
            return
        await self.create_index(
            COLLECTION,
            [(field, 1) for field in IDENTITY_FIELDS],
            name="uq_uploaded_report_identity",
            unique=True,
        )
        await self.create_index(
            COLLECTION,
            [("report_category", 1), ("delivery_date_iso", -1)],
            name="ix_uploaded_report_category_date",
        )
        # The lists are keyed on the upload day now, so it needs the same
        # covering index the delivery day has.
        await self.create_index(
            COLLECTION,
            [("report_category", 1), ("uploaded_on_ist_date", -1)],
            name="ix_uploaded_report_category_upload_date",
        )
        self._indexes_ready = True

    @staticmethod
    def identity_of(document: Dict[str, Any]) -> Dict[str, Any]:
        return {field: document.get(field) for field in IDENTITY_FIELDS}

    async def save(self, document: Dict[str, Any], upload_event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Store a parsed report, replacing any earlier upload of the same report.

        Returns the stored document, and whether it replaced one, so the screen
        can tell the operator they have just overwritten yesterday's copy.
        """
        await self._ensure_indexes()
        identity = self.identity_of(document)

        existing = await self.db[COLLECTION].find_one(identity, {"_id": 1})
        await self.db[COLLECTION].update_one(
            identity,
            {
                "$set": document,
                "$push": {"upload_history": {"$each": [upload_event], "$slice": -20}},
                "$inc": {"upload_count": 1},
            },
            upsert=True,
        )
        stored = await self.db[COLLECTION].find_one(identity)
        return {"document": stored, "replaced": existing is not None}

    async def find_many(
        self,
        filters: Dict[str, Any],
        limit: int = 50,
        skip: int = 0,
        sort_field: str = "delivery_date_iso",
    ) -> List[Dict[str, Any]]:
        cursor = (
            self.db[COLLECTION]
            .find(filters, LIST_PROJECTION)
            # Sorted by whatever the list is keyed on, so the newest row of the
            # period being looked at is the first one read.
            .sort([(sort_field, -1), ("uploaded_at", -1)])
            .skip(skip)
            .limit(limit)
        )
        return await cursor.to_list(length=limit)

    async def count(self, filters: Dict[str, Any]) -> int:
        return await self.db[COLLECTION].count_documents(filters)

    async def dates_with_reports(
        self,
        filters: Dict[str, Any],
        limit: int = 60,
        date_field: str = "delivery_date_iso",
    ) -> List[Dict[str, Any]]:
        """
        Which dates have reports, newest first.

        The daily view is a date picker, and a plain date input cannot say
        which days were actually uploaded - so an operator picking a quiet
        Sunday sees an empty screen with no way to tell "nothing was uploaded"
        from "I picked the wrong day". This answers that in one round trip,
        and it groups rather than paging 200 summary rows the caller would
        only reduce to a set of dates anyway.
        """
        pipeline = [
            {"$match": filters},
            {
                "$group": {
                    "_id": f"${date_field}",
                    "report_count": {"$sum": 1},
                    "markets": {"$addToSet": "$market_type"},
                    "exchanges": {"$addToSet": "$exchange"},
                    "last_uploaded_at": {"$max": "$uploaded_at"},
                }
            },
            {"$sort": {"_id": -1}},
            {"$limit": limit},
        ]
        grouped = await self.db[COLLECTION].aggregate(pipeline).to_list(length=limit)
        return [
            {
                # Named for what it is rather than for one of the two bases:
                # under `date_basis=uploaded` a key called `delivery_date`
                # would be holding an upload date.
                "date": row["_id"],
                "report_count": row.get("report_count", 0),
                "markets": sorted(filter(None, row.get("markets") or [])),
                "exchanges": sorted(filter(None, row.get("exchanges") or [])),
                "last_uploaded_at": row.get("last_uploaded_at"),
            }
            for row in grouped
        ]

    async def find_by_identity(self, identity: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return await self.db[COLLECTION].find_one(identity)


exchange_report_repository = ExchangeReportRepository()
