"""
The month-keyed categories — LTOA and STOA: store the file, hand it back.

Same deal as Other Reports, filed differently. Nothing is parsed, so no figures
from these files reach any other screen; the file *is* the deliverable and the
only useful thing to do with it is give it back byte for byte. What is
different is the key: these reports cover a calendar month, not a delivery day.

That matters more than it looks. Every other category in this service is filed
under a delivery date, and putting a September report on one arbitrary day of
September would make it findable only by someone who guessed the same day.
The operator picks a Year and a Month on the uploader and the same Year and
Month on the report screen, and those are the only two things that have to
agree.

Three checks are deliberately absent, as in Other Reports, and each is worth
saying out loud rather than leaving as an absence:

* **No parse.** Nothing uploaded here feeds any other screen. The LTOA / STOA
  grid stays empty until a sample file is supplied and a parser written for it.
* **No cross-check of the period against the file**, because nothing is read
  out of the file to check it with. The operator's Year and Month are taken at
  their word.
* **No validation checks**, because there is no expected shape to check against.

Nor is an exchange or a market asked for: an open-access report is about a
transmission licensee's corridor, not about a power exchange.
"""

import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from ..exchange_reports import registry
from ..repository.period_report_repository import period_report_repository
from .exchange_report_service import (
    ExchangeReportService,
    UploadRejected,
    resolve_upload_moment,
)
from .other_report_service import OtherReportService, _guess_content_type

logger = logging.getLogger(__name__)

DEFAULT_CONTENT_TYPE = "application/octet-stream"


class PeriodReportService:
    # -- upload ------------------------------------------------------------

    async def upload(
        self,
        *,
        data: bytes,
        filename: str,
        report_category: str,
        period_year: int,
        period_month: int,
        actor: Dict[str, Any],
        uploaded_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        category = self._require_period_category(report_category)

        self._require_accepted_extension(category, filename)
        self._require_within_size_limit(data)
        self._require_filename(filename)

        document = self._to_document(
            category=category,
            period_year=period_year,
            period_month=period_month,
            filename=filename,
            data=data,
            actor=actor,
            uploaded_at=uploaded_at,
        )
        upload_event = {
            "uploaded_at": document["uploaded_at"],
            "uploaded_at_ist": document["uploaded_at_ist"],
            "uploaded_on_ist_date": document["uploaded_on_ist_date"],
            "uploaded_at_ist_time": document["uploaded_at_ist_time"],
            "uploaded_in_time_block": document["uploaded_in_time_block"],
            "uploaded_at_declared": document["uploaded_at_declared"],
            "received_at": document["received_at"],
            "uploaded_by": document["uploaded_by"],
            "uploaded_by_username": document["uploaded_by_username"],
            "source_filename": filename,
            "file_sha256": document["file_sha256"],
        }

        result = await period_report_repository.save(
            document,
            content=data,
            content_type=document["content_type"],
            upload_event=upload_event,
        )
        stored = result["document"]

        logger.info(
            "Stored %s report %s for %s (replaced=%s)",
            category.code,
            filename,
            document["period_label"],
            result["replaced"],
        )
        return {
            "id": str(stored.get("_id")),
            # Replacing here means "same category, same month, same filename".
            # It happens without a confirmation step because the earlier file
            # is, by that definition, the same document uploaded again.
            "replaced_previous_upload": result["replaced"],
            "upload_count": stored.get("upload_count"),
            "summary": self.summarise(stored),
        }

    # -- individual rules --------------------------------------------------
    #
    # Extension, size and filename are the same rules the other two flows
    # apply, so they are the same code. A second copy would only drift, and
    # the wording of a refusal is what the operator reads.

    _require_accepted_extension = staticmethod(
        ExchangeReportService._require_accepted_extension
    )
    _require_within_size_limit = staticmethod(
        ExchangeReportService._require_within_size_limit
    )
    _require_filename = staticmethod(OtherReportService._require_filename)

    @staticmethod
    def _require_period_category(code: str) -> registry.ReportCategory:
        """
        Guards the flow rather than the caller.

        The route dispatches on `period_keyed`, so this should never fire from
        the screen. It exists because a service that silently files a
        delivery-dated category under a month would produce rows nothing ever
        lists again.
        """
        category = registry.get_category(code)
        if category is None:
            raise UploadRejected(
                "unknown_category",
                f"'{code}' is not a report category.",
                {"known_categories": [c.code for c in registry.CATEGORIES.values()]},
            )
        if not category.period_keyed:
            raise UploadRejected(
                "not_a_period_category",
                f"{category.label} is filed by delivery date, not by month.",
                {"report_category": category.code},
            )
        return category

    # -- storage shape -----------------------------------------------------

    @staticmethod
    def _to_document(
        *,
        category: registry.ReportCategory,
        period_year: int,
        period_month: int,
        filename: str,
        data: bytes,
        actor: Dict[str, Any],
        uploaded_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        moment = resolve_upload_moment(uploaded_at)
        return {
            "report_category": category.code,
            "report_category_label": category.label,
            "period_year": period_year,
            "period_month": period_month,
            # Stored rather than derived on read: a listing that has to build
            # "September 2021" from two integers is a second place for the
            # month names to disagree with the uploader's dropdown.
            "period_label": registry.period_label(period_year, period_month),
            "source_filename": filename,
            "content_type": _guess_content_type(filename),
            "file_sha256": hashlib.sha256(data).hexdigest(),
            "file_size_bytes": len(data),
            "uploaded_by": actor.get("user_id"),
            "uploaded_by_username": actor.get("username"),
            **moment,
        }

    # -- reads -------------------------------------------------------------

    @staticmethod
    def summarise(document: Dict[str, Any]) -> Dict[str, Any]:
        """
        One row of an LTOA / STOA file list.

        Deliberately the same field names the other two listings use for the
        columns they share, so a file's provenance renders the same way on
        every report screen without a second set of accessors.
        """
        return {
            "report_category": document.get("report_category"),
            "report_category_label": document.get("report_category_label"),
            # Both the success panel and the lists branch on this rather than
            # on the category code, so a further store-only category needs no
            # frontend change.
            "stores_file_only": True,
            "period_keyed": True,
            "period_year": document.get("period_year"),
            "period_month": document.get("period_month"),
            "period_label": document.get("period_label"),
            "source_filename": document.get("source_filename"),
            "content_type": document.get("content_type"),
            "file_size_bytes": document.get("file_size_bytes"),
            "file_sha256": document.get("file_sha256"),
            "uploaded_at": document.get("uploaded_at"),
            "uploaded_at_ist": document.get("uploaded_at_ist"),
            "uploaded_on_ist_date": document.get("uploaded_on_ist_date"),
            "uploaded_at_ist_time": document.get("uploaded_at_ist_time"),
            "uploaded_in_time_block": document.get("uploaded_in_time_block"),
            "uploaded_at_declared": document.get("uploaded_at_declared"),
            "uploaded_by_username": document.get("uploaded_by_username"),
            "upload_count": document.get("upload_count"),
        }

    async def list_reports(
        self,
        *,
        report_category: str,
        period_year: Optional[int] = None,
        period_month: Optional[int] = None,
        limit: int = 50,
        skip: int = 0,
    ) -> Dict[str, Any]:
        filters: Dict[str, Any] = {"report_category": report_category}
        if period_year is not None:
            filters["period_year"] = period_year
        if period_month is not None:
            filters["period_month"] = period_month

        documents = await period_report_repository.find_many(
            filters, limit=limit, skip=skip
        )
        total = await period_report_repository.count(filters)
        return {
            "items": [
                {"id": str(document["_id"]), **self.summarise(document)}
                for document in documents
            ],
            "total": total,
            "limit": limit,
            "skip": skip,
            "report_category": report_category,
            "period_year": period_year,
            "period_month": period_month,
        }

    async def periods(
        self,
        *,
        report_category: str,
        limit: int = 60,
    ) -> Dict[str, Any]:
        found = await period_report_repository.periods(
            {"report_category": report_category}, limit=limit
        )
        return {
            "items": [
                {
                    **entry,
                    "label": registry.period_label(entry["year"], entry["month"]),
                }
                for entry in found
            ],
            "total": len(found),
            "report_category": report_category,
        }

    async def get_file(self, report_id: str) -> Optional[Dict[str, Any]]:
        """
        The stored file, ready to be sent back.

        Returns None when there is no such row, and raises when the row exists
        but its bytes do not — a distinction the caller needs, because the
        second case is a storage fault rather than a bad link.
        """
        document = await period_report_repository.find_one_by_id(report_id)
        if not document:
            return None

        stored = await period_report_repository.find_file(report_id)
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
                or f"{document.get('report_category', 'period')}-report-{report_id}"
            ),
        }


period_report_service = PeriodReportService()