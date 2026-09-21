"""
The "Other Reports" category: store the file, hand it back unchanged.

Every other category is a parse. A sample file is supplied, a parser is written
for its layout, and what lands in Mongo is the figures — the file itself is
discarded once its SHA-256 is recorded. This one is the opposite and is meant
to be: it is the category for everything with no fixed layout to parse, so the
file *is* the deliverable and the only useful thing to do with it is give it
back byte for byte.

That makes three checks fall away, and each is worth saying out loud rather
than leaving as an absence:

* **No parse**, so no figures reach any other screen. Nothing uploaded here
  feeds the daily, weekly or shift views.
* **No delivery-date cross-check.** The obligation flow reads the delivery date
  out of the PDF and refuses an upload whose form contradicts it. There is
  nothing to read here, so the operator's date is taken at its word.
* **No validation checks**, because there is no expected shape to check
  against.

Nor is an exchange or a market asked for. An Other Report can be about
anything - a desk note, a regulatory circular, a settlement working - so
forcing a choice of IEX/PXIL/HPX and DAM/RTM would only record an answer
nobody meant.

What is kept: who uploaded it, when, which day it is for, the original
filename, its size and type, and a SHA-256 so a re-upload of an identical file
is recognisable.
"""

import hashlib
import logging
import mimetypes
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ..exchange_reports import registry
from ..repository.other_report_repository import other_report_repository
from .exchange_report_service import (
    ExchangeReportService,
    UploadRejected,
    describe_upload_moment,
)

logger = logging.getLogger(__name__)

DEFAULT_CONTENT_TYPE = "application/octet-stream"


def _guess_content_type(filename: str) -> str:
    """
    The media type to serve the file back as.

    Guessed from the name rather than sniffed from the bytes: the name is what
    the operator will save it under, and a wrong guess only affects how the
    browser offers it — the download is sent as an attachment either way.
    """
    guessed, _ = mimetypes.guess_type(filename or "")
    return guessed or DEFAULT_CONTENT_TYPE


class OtherReportService:
    # -- upload ------------------------------------------------------------

    async def upload(
        self,
        *,
        data: bytes,
        filename: str,
        delivery_date: str,
        actor: Dict[str, Any],
    ) -> Dict[str, Any]:
        category = registry.get_category(registry.OTHER_REPORTS)

        self._require_accepted_extension(category, filename)
        self._require_within_size_limit(data)
        self._require_filename(filename)

        document = self._to_document(
            category=category,
            delivery_date=delivery_date,
            filename=filename,
            data=data,
            actor=actor,
        )
        upload_event = {
            "uploaded_at": document["uploaded_at"],
            "uploaded_at_ist": document["uploaded_at_ist"],
            "uploaded_on_ist_date": document["uploaded_on_ist_date"],
            "uploaded_at_ist_time": document["uploaded_at_ist_time"],
            "uploaded_in_time_block": document["uploaded_in_time_block"],
            "uploaded_by": document["uploaded_by"],
            "uploaded_by_username": document["uploaded_by_username"],
            "source_filename": filename,
            "file_sha256": document["file_sha256"],
        }

        result = await other_report_repository.save(
            document,
            content=data,
            content_type=document["content_type"],
            upload_event=upload_event,
        )
        stored = result["document"]

        logger.info(
            "Stored other report %s for %s (replaced=%s)",
            filename,
            delivery_date,
            result["replaced"],
        )
        return {
            "id": str(stored.get("_id")),
            # Replacing here means "same filename, same day, same market", not
            # the obligation flow's "the exchange re-published this report".
            # It happens without a confirmation step because the earlier file
            # is, by that definition, the same document uploaded again.
            "replaced_previous_upload": result["replaced"],
            "upload_count": stored.get("upload_count"),
            "summary": self.summarise(stored),
        }

    # -- individual rules --------------------------------------------------
    #
    # Extension and size are the same rules the obligation flow applies, so
    # they are the same code. A second copy would only drift, and the wording
    # of a refusal is what the operator reads.

    _require_accepted_extension = staticmethod(
        ExchangeReportService._require_accepted_extension
    )
    _require_within_size_limit = staticmethod(
        ExchangeReportService._require_within_size_limit
    )

    @staticmethod
    def _require_filename(filename: str) -> None:
        """
        The filename is half the identity of the row, so an upload without one
        would collide with every other nameless upload for the same day.
        """
        if not (filename or "").strip():
            raise UploadRejected(
                "missing_filename",
                "That file arrived without a name, so it cannot be stored.",
                {"field": "file"},
            )

    # -- storage shape -----------------------------------------------------

    @staticmethod
    def _to_document(
        *,
        category: registry.ReportCategory,
        delivery_date: str,
        filename: str,
        data: bytes,
        actor: Dict[str, Any],
    ) -> Dict[str, Any]:
        uploaded_at = datetime.now(timezone.utc)
        return {
            "report_category": category.code,
            "report_category_label": category.label,
            "delivery_date_iso": delivery_date,
            "source_filename": filename,
            "content_type": _guess_content_type(filename),
            "file_sha256": hashlib.sha256(data).hexdigest(),
            "file_size_bytes": len(data),
            "uploaded_at": uploaded_at,
            "uploaded_by": actor.get("user_id"),
            "uploaded_by_username": actor.get("username"),
            **describe_upload_moment(uploaded_at),
        }

    # -- reads -------------------------------------------------------------

    @staticmethod
    def summarise(document: Dict[str, Any]) -> Dict[str, Any]:
        """
        One row of the Other Reports list.

        Deliberately the same field names the obligation listing uses for the
        columns they share, so the screens can show a file's provenance the
        same way without a second set of accessors.
        """
        return {
            "report_category": document.get("report_category"),
            "report_category_label": document.get("report_category_label"),
            # The success panel and the list both branch on this rather than
            # on the category code, so a second store-only category later
            # needs no frontend change.
            "stores_file_only": True,
            "delivery_date": document.get("delivery_date_iso"),
            "source_filename": document.get("source_filename"),
            "content_type": document.get("content_type"),
            "file_size_bytes": document.get("file_size_bytes"),
            "file_sha256": document.get("file_sha256"),
            "uploaded_at": document.get("uploaded_at"),
            "uploaded_at_ist": document.get("uploaded_at_ist"),
            "uploaded_on_ist_date": document.get("uploaded_on_ist_date"),
            "uploaded_at_ist_time": document.get("uploaded_at_ist_time"),
            "uploaded_in_time_block": document.get("uploaded_in_time_block"),
            "uploaded_by_username": document.get("uploaded_by_username"),
            "upload_count": document.get("upload_count"),
        }

    async def list_reports(
        self,
        *,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 50,
        skip: int = 0,
    ) -> Dict[str, Any]:
        filters: Dict[str, Any] = {}
        if date_from or date_to:
            window: Dict[str, Any] = {}
            if date_from:
                window["$gte"] = date_from
            if date_to:
                window["$lte"] = date_to
            # Always the upload day: there is nothing in these files to give
            # the delivery date any independent meaning.
            filters["uploaded_on_ist_date"] = window

        documents = await other_report_repository.find_many(
            filters, limit=limit, skip=skip
        )
        total = await other_report_repository.count(filters)
        return {
            "items": [
                {"id": str(document["_id"]), **self.summarise(document)}
                for document in documents
            ],
            "total": total,
            "limit": limit,
            "skip": skip,
            "date_basis": registry.DATE_BASIS_UPLOADED,
        }

    async def upload_dates(self, *, limit: int = 60) -> Dict[str, Any]:
        dates = await other_report_repository.upload_dates({}, limit=limit)
        return {
            "items": dates,
            "total": len(dates),
            "date_basis": registry.DATE_BASIS_UPLOADED,
        }

    async def get_file(self, report_id: str) -> Optional[Dict[str, Any]]:
        """
        The stored file, ready to be sent back.

        Returns None when there is no such row, and raises when the row exists
        but its bytes do not — a distinction the caller needs, because the
        second case is a storage fault rather than a bad link.
        """
        document = await other_report_repository.find_one_by_id(report_id)
        if not document:
            return None

        stored = await other_report_repository.find_file(report_id)
        if not stored or stored.get("content") is None:
            raise UploadRejected(
                "file_missing",
                (
                    "That report is recorded but its file is not in storage. "
                    "Upload it again."
                ),
                {"report_id": report_id},
            )

        return {
            "content": bytes(stored["content"]),
            "content_type": (
                stored.get("content_type")
                or document.get("content_type")
                or DEFAULT_CONTENT_TYPE
            ),
            "filename": (
                document.get("source_filename")
                or stored.get("source_filename")
                or f"other-report-{report_id}"
            ),
        }


other_report_service = OtherReportService()
