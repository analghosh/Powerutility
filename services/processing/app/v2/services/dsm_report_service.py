"""
The DSM Report category: parse the statement, store it day by day.

Unlike every other category in the uploader, the operator is asked for no date
at all. A deviation settlement statement is block-by-block and carries its own
Date column — the sample PTC supplied covers seven settlement days in one sheet
— so the days are read out of the file rather than typed into the form. Asking
for a Delivery Date would record one of those days as though it were the whole
upload.

Exchange and market are not asked for either, for the reason a store-only
category does not ask: a deviation is settled against the grid, not against an
exchange, so an IEX/DAM on the row would be an answer nobody meant.

Three things are kept, and each is read by something different:

* **The days.** One document per settlement date, holding that date's blocks.
  This is what the DSM Report screen shows and what the export is built from.
* **The upload.** Who sent which file, when, and which window it covered.
* **The file itself**, byte for byte, so the statement behind a figure is still
  downloadable months later.

A re-upload replaces the settlement days it carries and leaves the others
alone. That is what a correction actually is: a reissued statement rarely
covers exactly the same window, and filing the figures under the upload would
leave two documents both claiming the same day with nothing to say which is
current.
"""

import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..exchange_reports import dsm_parser, registry
from ..repository.dsm_report_repository import dsm_report_repository
from .exchange_report_service import (
    AlreadyUploaded,
    ExchangeReportService,
    UploadRejected,
    resolve_upload_moment,
)
from .other_report_service import DEFAULT_CONTENT_TYPE, _guess_content_type

logger = logging.getLogger(__name__)

#: Which date a list of settlement rows is keyed on.
#:
#: The screen offers both, because they answer different questions. "Show me
#: 1-15 April" is about the days the power deviated; "show me what came in
#: yesterday" is about the day somebody uploaded the statement. Neither is a
#: safe default for the other — a statement for March uploaded in May appears
#: under March on one basis and May on the other, and a reader given only one
#: of those would conclude the other month is empty.
DATE_BASIS_REPORT = "report"
DATE_BASIS_UPLOADED = "uploaded"

DEFAULT_DATE_BASIS = DATE_BASIS_REPORT

#: Basis -> the field on a stored day that carries it as YYYY-MM-DD. Both are
#: plain strings, so a range compares lexically.
DATE_BASIS_FIELDS: Dict[str, str] = {
    DATE_BASIS_REPORT: "report_date",
    DATE_BASIS_UPLOADED: "uploaded_on_ist_date",
}


def resolve_date_field(date_basis: Optional[str]) -> str:
    """The day field a `date_basis` selects. Unknown values fall back."""
    return DATE_BASIS_FIELDS.get(
        (date_basis or DEFAULT_DATE_BASIS).strip().lower(),
        DATE_BASIS_FIELDS[DEFAULT_DATE_BASIS],
    )


def date_basis_of(date_field: str) -> str:
    """The basis name for a field — echoed back so the caller can see which one
    actually applied when it sent something unrecognised."""
    for basis, field in DATE_BASIS_FIELDS.items():
        if field == date_field:
            return basis
    return DEFAULT_DATE_BASIS


class DsmReportService:
    # -- upload ------------------------------------------------------------

    async def upload(
        self,
        *,
        data: bytes,
        filename: str,
        actor: Dict[str, Any],
        uploaded_at: Optional[datetime] = None,
        confirm_replace: bool = False,
    ) -> Dict[str, Any]:
        category = registry.get_category(registry.DSM_REPORT)

        self._require_accepted_extension(category, filename)
        self._require_within_size_limit(data)
        self._require_filename(filename)

        parsed = self._parse(data, filename)

        if not confirm_replace:
            await self._require_days_not_already_stored(parsed, filename=filename)

        document = self._to_document(
            parsed,
            category=category,
            filename=filename,
            data=data,
            actor=actor,
            uploaded_at=uploaded_at,
        )
        # The same shape as the document's own upload fields, so anything that
        # can render "when this landed" for the current upload renders an
        # earlier one without a second code path.
        upload_event = {
            key: document[key]
            for key in (
                "uploaded_at",
                "uploaded_at_ist",
                "uploaded_on_ist_date",
                "uploaded_at_ist_time",
                "uploaded_in_time_block",
                "uploaded_at_declared",
                "received_at",
                "uploaded_by",
                "uploaded_by_username",
                "file_sha256",
            )
        }
        upload_event["source_filename"] = filename

        days = [self._to_day_document(day, document) for day in parsed["days"]]

        result = await dsm_report_repository.save(
            document,
            days=days,
            content=data,
            content_type=document["content_type"],
            upload_event=upload_event,
        )
        stored = result["document"]

        logger.info(
            "Stored DSM report %s covering %s..%s (%s days, %s blocks, replaced_days=%s)",
            filename,
            parsed["date_from"],
            parsed["date_to"],
            len(days),
            parsed["row_count"],
            len(result["replaced_days"]),
        )
        return {
            "id": str(stored.get("_id")),
            "replaced_previous_upload": result["replaced"],
            # Named separately from the upload-level replace because they mean
            # different things: the upload row being replaced is a re-send of
            # the same statement, while a replaced day is a figure on the
            # screen that has just changed.
            "replaced_days": result["replaced_days"],
            "upload_count": stored.get("upload_count"),
            "summary": self.summarise(stored),
        }

    # -- individual rules --------------------------------------------------
    #
    # Extension and size are the same rules every other flow applies, so they
    # are the same code. A second copy would only drift, and the wording of a
    # refusal is what the operator reads.

    _require_accepted_extension = staticmethod(
        ExchangeReportService._require_accepted_extension
    )
    _require_within_size_limit = staticmethod(
        ExchangeReportService._require_within_size_limit
    )

    @staticmethod
    def _require_filename(filename: str) -> None:
        """The filename is part of the upload's identity, and is what the
        operator will save the statement back under."""
        if not (filename or "").strip():
            raise UploadRejected(
                "missing_filename",
                "That file arrived without a name, so it cannot be stored.",
                {"field": "file"},
            )

    @staticmethod
    def _parse(data: bytes, filename: str) -> Dict[str, Any]:
        """Read the workbook, turning a parse failure into a refusal the screen
        can render beside the field it names."""
        try:
            return dsm_parser.parse(data, filename)
        except dsm_parser.DsmParseError as failure:
            raise UploadRejected(
                failure.reason, failure.message, failure.details
            ) from failure

    @staticmethod
    async def _require_days_not_already_stored(
        parsed: Dict[str, Any], *, filename: str
    ) -> None:
        """
        Stop and ask if any of these settlement days already has figures.

        Overwriting is usually right — a corrected statement is reissued for
        the same days — but it silently changes what the DSM screen shows, so
        it should be a decision rather than the result of pressing Submit
        twice. The days that would change are named, because "this overlaps
        something" is not enough for the operator to judge by.
        """
        report_dates = [day["report_date"] for day in parsed["days"]]
        existing = await dsm_report_repository.existing_days(report_dates)
        if not existing:
            return

        overlapping = [row.get("report_date") for row in existing]
        # The most recently uploaded of the overlapping days is the one shown
        # as `previous`, matching the shape the duplicate modal already reads.
        latest = max(existing, key=lambda row: row.get("uploaded_at") or datetime.min)
        source = await dsm_report_repository.find_one_by_id(
            str(latest.get("source_report_id"))
        ) if latest.get("source_report_id") else None

        one = len(overlapping) == 1
        span = overlapping[0] if one else f"{overlapping[0]} to {overlapping[-1]}"
        raise AlreadyUploaded(
            "already_uploaded",
            (
                f"{len(overlapping)} settlement {'day' if one else 'days'} in "
                f"that file ({span}) already "
                f"{'has' if one else 'have'} figures stored."
            ),
            {
                "overlapping_days": overlapping,
                "identical_file": bool(
                    source and source.get("source_filename") == filename
                ),
                "previous": {
                    "source_filename": (source or {}).get("source_filename")
                    or latest.get("source_filename"),
                    "uploaded_at_ist": (source or {}).get("uploaded_at_ist"),
                    "uploaded_in_time_block": (source or {}).get(
                        "uploaded_in_time_block"
                    ),
                    "uploaded_by_username": (source or {}).get("uploaded_by_username"),
                    "upload_count": (source or {}).get("upload_count"),
                },
            },
        )

    # -- storage shape -----------------------------------------------------

    @staticmethod
    def _to_document(
        parsed: Dict[str, Any],
        *,
        category: registry.ReportCategory,
        filename: str,
        data: bytes,
        actor: Dict[str, Any],
        uploaded_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        return {
            "report_category": category.code,
            "report_category_label": category.label,
            "report_date_from": parsed["date_from"],
            "report_date_to": parsed["date_to"],
            "report_dates": [day["report_date"] for day in parsed["days"]],
            "day_count": len(parsed["days"]),
            "block_count": parsed["row_count"],
            "source_filename": filename,
            "content_type": _guess_content_type(filename),
            "file_sha256": hashlib.sha256(data).hexdigest(),
            "file_size_bytes": len(data),
            "uploaded_by": actor.get("user_id"),
            "uploaded_by_username": actor.get("username"),
            **resolve_upload_moment(uploaded_at),
        }

    @staticmethod
    def _to_day_document(
        day: Dict[str, Any], upload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        One settlement day, carrying enough of its upload to be read alone.

        The upload fields are copied rather than joined to at read time: the
        screen filters on the upload day, and a filter that needs a lookup per
        row is a filter that cannot use an index.
        """
        return {
            "report_date": day["report_date"],
            "blocks": day["blocks"],
            "block_count": day["block_count"],
            "complete": day["complete"],
            "source_filename": upload["source_filename"],
            "uploaded_at": upload["uploaded_at"],
            "uploaded_at_ist": upload["uploaded_at_ist"],
            "uploaded_on_ist_date": upload["uploaded_on_ist_date"],
            "uploaded_at_ist_time": upload["uploaded_at_ist_time"],
            "uploaded_in_time_block": upload["uploaded_in_time_block"],
            "uploaded_at_declared": upload["uploaded_at_declared"],
            "uploaded_by_username": upload["uploaded_by_username"],
        }

    # -- reads -------------------------------------------------------------

    @staticmethod
    def summarise(document: Dict[str, Any]) -> Dict[str, Any]:
        """One uploaded statement, as the success panel and the file list read it."""
        return {
            "report_category": document.get("report_category"),
            "report_category_label": document.get("report_category_label"),
            # The screens branch on these rather than on the category code, so
            # a second self-dated category later needs no frontend change.
            "stores_file_only": False,
            "dates_from_file": True,
            "report_date_from": document.get("report_date_from"),
            "report_date_to": document.get("report_date_to"),
            "day_count": document.get("day_count"),
            "block_count": document.get("block_count"),
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

    @staticmethod
    def _window(
        date_from: Optional[str], date_to: Optional[str], date_basis: Optional[str]
    ) -> Dict[str, Any]:
        field = resolve_date_field(date_basis)
        filters: Dict[str, Any] = {}
        if date_from or date_to:
            window: Dict[str, Any] = {}
            if date_from:
                window["$gte"] = date_from
            if date_to:
                window["$lte"] = date_to
            filters[field] = window
        return filters

    @staticmethod
    def _as_day_row(document: Dict[str, Any]) -> Dict[str, Any]:
        """
        One stored day, ready to serialise.

        Both ids have to be said as strings. `_id` and `source_report_id` are
        ObjectIds on the way out of Mongo, and FastAPI cannot serialise that
        type at all — the route answers 500 rather than dropping the field, so
        a missed one is not a cosmetic slip.
        """
        # Both popped before the dict is built. `{**document, "id":
        # str(document.pop("_id"))}` looks equivalent and is not: the spread is
        # evaluated first, so the raw ObjectId is already in the result by the
        # time the pop runs.
        document_id = document.pop("_id")
        source_id = document.pop("source_report_id", None)
        return {
            **document,
            "id": str(document_id),
            "source_report_id": str(source_id) if source_id else None,
        }

    async def list_days(
        self,
        *,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        date_basis: Optional[str] = None,
        limit: int = 62,
        skip: int = 0,
    ) -> Dict[str, Any]:
        """
        The settlement days in a window, without their blocks.

        Used for the screen's day summary and for the "which days does this
        window actually hold" question. The blocks are left out because a
        fortnight of them is 1,440 rows, and a summary that carries them would
        be megabytes of numbers nothing displays.
        """
        field = resolve_date_field(date_basis)
        filters = self._window(date_from, date_to, date_basis)
        documents = await dsm_report_repository.find_days(
            filters, limit=limit, skip=skip, with_blocks=False
        )
        total = await dsm_report_repository.count_days(filters)
        return {
            "items": [self._as_day_row(document) for document in documents],
            "total": total,
            "limit": limit,
            "skip": skip,
            "date_basis": date_basis_of(field),
        }

    async def rows(
        self,
        *,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        date_basis: Optional[str] = None,
        limit: int = 62,
        skip: int = 0,
    ) -> Dict[str, Any]:
        """
        Every block in a window, flattened into the table the screen draws.

        `limit` counts settlement days rather than rows, because that is the
        unit the operator picks: a from/to of 1-15 April is fifteen days, and
        a cap expressed in rows would cut a day in half.
        """
        field = resolve_date_field(date_basis)
        filters = self._window(date_from, date_to, date_basis)
        documents = await dsm_report_repository.find_days(
            filters, limit=limit, skip=skip, with_blocks=True
        )
        total_days = await dsm_report_repository.count_days(filters)

        items: List[Dict[str, Any]] = []
        for document in documents:
            for block in document.get("blocks", []):
                items.append(
                    {
                        "report_date": document.get("report_date"),
                        "uploaded_on": document.get("uploaded_on_ist_date"),
                        "uploaded_at_ist": document.get("uploaded_at_ist"),
                        "uploaded_at_ist_time": document.get("uploaded_at_ist_time"),
                        "uploaded_by_username": document.get("uploaded_by_username"),
                        "source_filename": document.get("source_filename"),
                        **block,
                    }
                )

        return {
            "items": items,
            "row_count": len(items),
            "day_count": len(documents),
            "total_days": total_days,
            "limit": limit,
            "skip": skip,
            "date_basis": date_basis_of(field),
            # The column order the screen and the export both use, so neither
            # has to restate the report's own layout.
            "columns": [
                {"field": field_name, "label": dsm_parser.FIELD_LABELS[field_name]}
                for field_name in dsm_parser.NUMERIC_FIELDS
            ],
        }

    async def dates(self, *, limit: int = 400) -> Dict[str, Any]:
        """
        Which days have figures, on both bases.

        Both are returned in one call because the screen offers both and needs
        the hints for whichever the operator switches to — a second round trip
        on every toggle would be a spinner where a dropdown should be.
        """
        report_dates = await dsm_report_repository.day_dates(limit=limit)
        upload_dates = await dsm_report_repository.upload_dates(limit=limit)
        return {
            "report_dates": report_dates,
            "upload_dates": upload_dates,
            "default_date_basis": DEFAULT_DATE_BASIS,
        }

    async def list_uploads(
        self,
        *,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 50,
        skip: int = 0,
    ) -> Dict[str, Any]:
        """The statements themselves, newest upload first — the provenance list
        under the figures, and where the original file is downloaded from."""
        filters: Dict[str, Any] = {}
        if date_from or date_to:
            window: Dict[str, Any] = {}
            if date_from:
                window["$gte"] = date_from
            if date_to:
                window["$lte"] = date_to
            filters["uploaded_on_ist_date"] = window

        documents = await dsm_report_repository.find_many(
            filters, limit=limit, skip=skip
        )
        total = await dsm_report_repository.count(filters)
        return {
            "items": [
                {"id": str(document["_id"]), **self.summarise(document)}
                for document in documents
            ],
            "total": total,
            "limit": limit,
            "skip": skip,
            "date_basis": DATE_BASIS_UPLOADED,
        }

    async def get_file(self, report_id: str) -> Optional[Dict[str, Any]]:
        """
        The stored statement, ready to be sent back.

        Returns None when there is no such row, and raises when the row exists
        but its bytes do not — a distinction the caller needs, because the
        second case is a storage fault rather than a bad link.
        """
        document = await dsm_report_repository.find_one_by_id(report_id)
        if not document:
            return None

        stored = await dsm_report_repository.find_file(report_id)
        if not stored or stored.get("content") is None:
            raise UploadRejected(
                "file_missing",
                (
                    "That statement is recorded but its file is not in "
                    "storage. Upload it again."
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
                or f"dsm-report-{report_id}"
            ),
        }


dsm_report_service = DsmReportService()
