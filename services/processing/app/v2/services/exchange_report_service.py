"""
The Report Uploader's rules.

An uploaded report is either trustworthy or it is not stored. That is the
decision this file implements, and every check below exists to keep a report
that looks right but is not out of the reports section.

The checks run in cost order - category, extension, size, then the parse - so
a wrong file is refused before it is read. The last check is the important
one: the parser cross-references each extracted figure against a total printed
elsewhere in the same document, and unless every one of those agrees the
upload is refused with the failing check named. A report that is silently one
time block short would otherwise be indistinguishable from a good one.

Of the operator's four choices only the delivery date is checked against the
file. Market and exchange come from dropdowns and are taken at their word -
see `_require_delivery_date_matches` for why the available evidence is not
good enough to overrule them.
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from libs.audit_queue.timezone_utils import utc_to_ist
from libs.parsers.exchange_obligation_report_parser import (
    ExchangeObligationReportParser,
    ObligationReportParseError,
)

from ..exchange_reports import registry
from ..exchange_reports.registry import (
    DEFAULT_DATE_BASIS,
    date_basis_of,
    resolve_date_field,
)
from ..repository.exchange_report_repository import exchange_report_repository

logger = logging.getLogger(__name__)

#: How the form's fields are named to the operator. The message they read
#: should use the labels on the screen, not the names of the stored fields.
FIELD_LABELS = {
    "delivery_date": "Delivery Date",
}

#: A time block is 15 minutes, so there are 96 in a day and block 1 starts at
#: 00:00. Same convention as everywhere else in this service.
BLOCKS_PER_HOUR = 4
MINUTES_PER_BLOCK = 15


def _iso_or_none(value: Any) -> Optional[str]:
    """A timestamp as text, for anything that has to survive JSON."""
    if isinstance(value, datetime):
        return value.isoformat()
    return value if value is None or isinstance(value, str) else str(value)


def describe_upload_moment(uploaded_at: datetime) -> Dict[str, Any]:
    """
    When the upload happened, in the terms the shift report works in.

    The shift report is laid out by time block, so a timestamp alone cannot be
    placed on it. The block is derived here, once, at the moment of upload -
    deriving it later from `uploaded_at` would mean every reader repeating the
    same UTC-to-IST conversion, and the day boundary makes that easy to get
    wrong: an upload at 23:10 UTC is block 19 of the *next* IST day.

    The stored UTC instant stays authoritative; these are its IST reading.
    """
    ist = utc_to_ist(uploaded_at)
    block = ist.hour * BLOCKS_PER_HOUR + ist.minute // MINUTES_PER_BLOCK + 1
    return {
        "uploaded_at_ist": ist.isoformat(),
        "uploaded_on_ist_date": ist.date().isoformat(),
        "uploaded_in_time_block": block,
        "uploaded_at_ist_time": ist.strftime("%H:%M"),
    }


def resolve_upload_moment(declared_at: Optional[datetime] = None) -> Dict[str, Any]:
    """
    The upload fields for one stored report, given what the operator declared.

    The Report Uploader lets the operator set the Uploaded Date and time. That
    is deliberate - reports are often put in during a later shift than the one
    they belong to, and the shift view is laid out by the upload time block, so
    a file that arrived at 23:50 and was typed in at 00:10 belongs on the
    earlier shift or it is on the wrong page entirely.

    Both instants are kept, and that is the point of this function existing
    rather than the caller writing one timestamp:

    * ``uploaded_at`` is what the operator declared, and is what every screen
      reads and every listing is keyed on.
    * ``received_at`` is the server clock at the moment the bytes arrived, and
      is never writable from the form.

    Keeping only the declared one would make the audit trail something the form
    can rewrite: a file could be recorded as having landed at any time, with
    nothing left to contradict it. ``uploaded_at_declared`` says which of the
    two cases a row is, so a reader is never left guessing whether a timestamp
    was typed or observed.
    """
    received_at = datetime.now(timezone.utc)
    effective = declared_at or received_at
    return {
        "uploaded_at": effective,
        "received_at": received_at,
        "uploaded_at_declared": declared_at is not None,
        **describe_upload_moment(effective),
    }


class UploadRejected(Exception):
    """
    An upload that will not be stored.

    `reason` is a machine-readable code the frontend can branch on; `message`
    is what the operator reads; `details` carries whatever they need in order
    to fix it - the checks that failed, the value the file actually held.
    """

    def __init__(
        self,
        reason: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.details = details or {}


class CategoryNotAvailable(UploadRejected):
    """A category that is listed on the screen but has no parser yet."""


class AlreadyUploaded(UploadRejected):
    """
    This report has been uploaded before, and the caller has not said to
    replace it.

    Not an error - a question. Overwriting is usually what the operator wants,
    because the exchange reissues corrected reports under the same identity.
    But it silently changes a day's numbers in the reports section, so it
    should be a decision rather than a side effect of pressing Submit twice.
    """


class FileTooLarge(UploadRejected):
    """Bigger than the drop zone's stated limit."""


class ExchangeReportService:
    def __init__(self) -> None:
        self._obligation_parser = ExchangeObligationReportParser()

    # -- upload ------------------------------------------------------------

    async def upload(
        self,
        *,
        data: bytes,
        filename: str,
        report_category: str,
        exchange: str,
        market_type: str,
        delivery_date: str,
        actor: Dict[str, Any],
        confirm_replace: bool = False,
        uploaded_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        category = self._require_available_category(report_category)
        self._require_accepted_extension(category, filename)
        self._require_within_size_limit(data)
        self._require_known_choice("exchange", exchange, registry.EXCHANGES)
        self._require_known_choice("market_type", market_type, registry.MARKETS)

        parsed = self._parse(category, data, filename)

        self._require_delivery_date_matches(parsed, delivery_date=delivery_date)
        self._require_every_check_passed(parsed)

        if not confirm_replace:
            await self._require_not_already_uploaded(
                parsed,
                category=category,
                exchange=exchange,
                market_type=market_type,
                delivery_date=delivery_date,
                data=data,
            )

        document = self._to_document(
            parsed,
            category=category,
            exchange=exchange,
            market_type=market_type,
            filename=filename,
            data=data,
            actor=actor,
            uploaded_at=uploaded_at,
        )
        # The same shape as the document's own upload fields, so anything that
        # can render "when this landed" for the current upload renders an
        # earlier one without a second code path. The split IST date and time
        # are carried rather than left to be derived from `uploaded_at`: a
        # reader localising the UTC instant would print a clock time that
        # contradicts the time block beside it.
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
        result = await exchange_report_repository.save(document, upload_event)
        stored = result["document"]

        logger.info(
            "Stored %s %s %s for %s (replaced=%s)",
            category.code,
            exchange,
            market_type,
            document["delivery_date_iso"],
            result["replaced"],
        )
        return {
            "id": str(stored.get("_id")),
            "replaced_previous_upload": result["replaced"],
            "upload_count": stored.get("upload_count"),
            "summary": self.summarise(stored),
        }

    # -- individual rules --------------------------------------------------

    @staticmethod
    def _require_available_category(code: str) -> registry.ReportCategory:
        category = registry.get_category(code)
        if category is None:
            raise UploadRejected(
                "unknown_category",
                f"'{code}' is not a report category.",
                {"known_categories": [c.code for c in registry.CATEGORIES.values()]},
            )
        if not category.implemented:
            raise CategoryNotAvailable(
                "category_not_available",
                f"{category.label} cannot be uploaded yet. "
                f"{category.unavailable_reason}",
                {"report_category": category.code},
            )
        return category

    @staticmethod
    def _require_accepted_extension(category: registry.ReportCategory, filename: str) -> None:
        name = (filename or "").lower()
        if any(name.endswith(extension) for extension in category.accepted_extensions):
            return
        accepted = ", ".join(category.accepted_extensions)
        # "is published as" is right for a report the exchange issues in one
        # fixed format, and wrong for a catch-all that simply takes a list -
        # or for any category that accepts more than one type, where there is
        # no single format it is published as.
        verb = (
            "accepts"
            if category.stores_file_only or len(category.accepted_extensions) > 1
            else "is published as"
        )
        raise UploadRejected(
            "unsupported_file_type",
            f"{category.label} {verb} {accepted}. "
            f"'{filename}' is not one of those.",
            {"accepted_extensions": list(category.accepted_extensions)},
        )

    @staticmethod
    def _require_within_size_limit(data: bytes) -> None:
        if len(data) <= registry.MAX_UPLOAD_BYTES:
            return
        raise FileTooLarge(
            "file_too_large",
            f"The file is {len(data) / 1024 / 1024:.1f} MB; the limit is "
            f"{registry.MAX_UPLOAD_BYTES // 1024 // 1024} MB.",
            {"size_bytes": len(data), "limit_bytes": registry.MAX_UPLOAD_BYTES},
        )

    @staticmethod
    def _require_known_choice(field: str, value: str, allowed: List[str]) -> None:
        if value in allowed:
            return
        raise UploadRejected(
            f"unknown_{field}",
            f"'{value}' is not a recognised {field.replace('_', ' ')}.",
            {"allowed": allowed},
        )

    def _parse(
        self,
        category: registry.ReportCategory,
        data: bytes,
        filename: str,
    ) -> Dict[str, Any]:
        if category.code != registry.OBLIGATION_REPORT:
            # Unreachable while Obligation Report is the only implemented
            # category; here so that adding one without its parser fails
            # loudly rather than storing an empty document.
            raise CategoryNotAvailable(
                "category_not_available",
                f"No parser is wired up for {category.label}.",
                {"report_category": category.code},
            )
        try:
            return self._obligation_parser.parse_pdf(data, filename=filename)
        except ObligationReportParseError as exc:
            raise UploadRejected(
                "could_not_parse",
                str(exc),
                {"source_filename": filename},
            ) from exc

    @staticmethod
    def _require_delivery_date_matches(
        parsed: Dict[str, Any],
        *,
        delivery_date: str,
    ) -> None:
        """
        Refuse a file delivered on a different day than the one picked.

        This is the one field worth cross-checking. The exchange prints the
        delivery date on the page, and it is an easy one to get wrong: the
        file name carries the TRADING date, so a day-ahead report downloaded
        on the 18th is delivered on the 19th. Filed under the wrong day, a
        day's obligation lands on top of another day's in the reports section.

        Market and exchange are deliberately not cross-checked. They are
        chosen from dropdowns, which is what makes them authoritative, and the
        evidence available to contradict them is not good enough to overrule
        the operator: the file name is only a naming convention the exchange
        can change, and the page itself names no market at all except G-DAM -
        HPDAM in particular is indistinguishable from DAM in the text, so
        checking against it would have refused every HPDAM report ever
        uploaded.
        """
        file_delivery_date = parsed.get("delivery_date_iso")
        if not file_delivery_date or file_delivery_date == delivery_date:
            return

        raise UploadRejected(
            "file_does_not_match_selection",
            f"Delivery Date: you selected {delivery_date}, the report is for "
            f"{file_delivery_date}.",
            {
                "mismatches": [
                    {
                        "field": "delivery_date",
                        "label": FIELD_LABELS["delivery_date"],
                        "selected": delivery_date,
                        "found_in_file": file_delivery_date,
                    }
                ]
            },
        )

    @staticmethod
    async def _require_not_already_uploaded(
        parsed: Dict[str, Any],
        *,
        category: registry.ReportCategory,
        exchange: str,
        market_type: str,
        delivery_date: str,
        data: bytes,
    ) -> None:
        """
        Stop and ask if this report is already stored.

        The identity can only be checked after parsing, because the portfolio
        is printed inside the report and is part of what makes one report
        distinct from another - the form has no field for it.

        Two different situations are reported separately, because they mean
        different things to the person deciding. The same file again is almost
        always a double submit and replacing it changes nothing. A *different*
        file for the same report is the exchange having reissued it, or the
        wrong file about to land on a day's numbers, and that is the one worth
        pausing over.
        """
        identity = {
            "report_category": category.code,
            "exchange": exchange,
            "market_type": market_type,
            "delivery_date_iso": delivery_date,
            "portfolio_code": parsed.get("portfolio_code"),
        }
        existing = await exchange_report_repository.find_by_identity(identity)
        if not existing:
            return

        digest = hashlib.sha256(data).hexdigest()
        identical = existing.get("file_sha256") == digest
        previous_name = existing.get("source_filename") or "an earlier file"

        message = (
            f"'{previous_name}' has already been uploaded for "
            f"{market_type} {exchange} on {delivery_date}"
        )
        message += (
            ". This is the same file. Upload it again to replace it."
            if identical
            else ". Uploading this one will replace it."
        )

        raise AlreadyUploaded(
            "already_uploaded",
            message,
            {
                "identical_file": identical,
                "previous": {
                    "source_filename": existing.get("source_filename"),
                    "file_sha256": existing.get("file_sha256"),
                    # Rendered, not passed through: this dict becomes an HTTP
                    # error body, and a Mongo datetime is not JSON.
                    "uploaded_at": _iso_or_none(existing.get("uploaded_at")),
                    "uploaded_at_ist": existing.get("uploaded_at_ist"),
                    "uploaded_in_time_block": existing.get("uploaded_in_time_block"),
                    "uploaded_by_username": existing.get("uploaded_by_username"),
                    "upload_count": existing.get("upload_count"),
                },
            },
        )

    @staticmethod
    def _require_every_check_passed(parsed: Dict[str, Any]) -> None:
        validation = parsed.get("validation") or {}
        if validation.get("passed"):
            return
        failed = [check for check in validation.get("checks", []) if not check["passed"]]
        described = "; ".join(f"{check['check']} ({check['detail']})" for check in failed)
        raise UploadRejected(
            "failed_validation",
            f"The report did not pass its consistency checks - {described}. "
            "Nothing has been stored.",
            {"failed_checks": failed},
        )

    # -- storage shape -----------------------------------------------------

    @staticmethod
    def _to_document(
        parsed: Dict[str, Any],
        *,
        category: registry.ReportCategory,
        exchange: str,
        market_type: str,
        filename: str,
        data: bytes,
        actor: Dict[str, Any],
        uploaded_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Build the stored document.

        The PDF's own bytes are not kept - only their SHA-256, which is enough
        to tell one upload from a re-upload of the same file, and to prove
        later which file a figure came from. The originals stay downloadable
        from the exchange, and the deployment is a single box with no backup.
        """
        moment = resolve_upload_moment(uploaded_at)
        document = dict(parsed)
        document.update(
            {
                "report_category": category.code,
                "report_category_label": category.label,
                # Counted at write time because listings project the block
                # arrays away; derived on read, a row would report 0 blocks
                # and read as a report that stored nothing.
                "block_count": len(parsed.get("trade_blocks") or []),
                "sell_block_count": len(parsed.get("sell_blocks") or []),
                "exchange": exchange,
                "market_type": market_type,
                "source_filename": filename,
                "file_sha256": hashlib.sha256(data).hexdigest(),
                "file_size_bytes": len(data),
                "uploaded_by": actor.get("user_id"),
                "uploaded_by_username": actor.get("username"),
                **moment,
            }
        )
        return document

    # -- reads -------------------------------------------------------------

    @staticmethod
    def summarise(document: Dict[str, Any]) -> Dict[str, Any]:
        """The headline figures a listing row and the success panel both show."""
        totals = document.get("trade_totals") or {}
        obligation = document.get("obligation_summary") or {}
        return {
            "report_category": document.get("report_category"),
            "report_category_label": document.get("report_category_label"),
            "exchange": document.get("exchange"),
            "market_type": document.get("market_type"),
            "trading_date": document.get("trading_date_iso"),
            "delivery_date": document.get("delivery_date_iso"),
            "entity_name": document.get("entity_name"),
            "portfolio_code": document.get("portfolio_code"),
            "portfolio_name": document.get("portfolio_name"),
            "funds_payin_payout": obligation.get("funds_payin_payout"),
            "total": obligation.get("total"),
            "buy_mwh": totals.get("buy_mwh"),
            "sell_mwh": totals.get("sell_mwh"),
            "gross_mwh": totals.get("gross_mwh"),
            "block_count": document.get(
                "block_count", len(document.get("trade_blocks") or [])
            ),
            "sell_block_count": document.get(
                "sell_block_count", len(document.get("sell_blocks") or [])
            ),
            "source_filename": document.get("source_filename"),
            "uploaded_at": document.get("uploaded_at"),
            "uploaded_at_ist": document.get("uploaded_at_ist"),
            "uploaded_at_ist_time": document.get("uploaded_at_ist_time"),
            "uploaded_on_ist_date": document.get("uploaded_on_ist_date"),
            "uploaded_in_time_block": document.get("uploaded_in_time_block"),
            # Whether the upload time was typed on the form or observed by the
            # server. Carried on every listing row so a reader is never left
            # guessing which of the two a stamp is.
            "uploaded_at_declared": document.get("uploaded_at_declared"),
            "uploaded_by_username": document.get("uploaded_by_username"),
        }

    async def list_reports(
        self,
        *,
        report_category: Optional[str] = None,
        exchange: Optional[str] = None,
        market_type: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        date_basis: str = DEFAULT_DATE_BASIS,
        limit: int = 50,
        skip: int = 0,
    ) -> Dict[str, Any]:
        date_field = resolve_date_field(date_basis)
        filters: Dict[str, Any] = {}
        if report_category:
            filters["report_category"] = report_category
        if exchange:
            filters["exchange"] = exchange
        if market_type:
            filters["market_type"] = market_type
        if date_from or date_to:
            window: Dict[str, Any] = {}
            if date_from:
                window["$gte"] = date_from
            if date_to:
                window["$lte"] = date_to
            filters[date_field] = window

        documents = await exchange_report_repository.find_many(
            filters, limit=limit, skip=skip, sort_field=date_field
        )
        total = await exchange_report_repository.count(filters)
        return {
            "items": [
                {"id": str(document["_id"]), **self.summarise(document)}
                for document in documents
            ],
            "total": total,
            "limit": limit,
            "skip": skip,
            "date_basis": date_basis_of(date_field),
        }

    async def delivery_dates(
        self,
        *,
        report_category: Optional[str] = None,
        date_basis: str = DEFAULT_DATE_BASIS,
        limit: int = 60,
    ) -> Dict[str, Any]:
        """
        The dates that have reports, newest first.

        The daily view picks one date, so it needs to know which dates are
        worth picking. Each entry also carries the markets and exchanges
        uploaded that day, which is what the operator is really asking when
        they open a date: "is RTM in yet?"
        """
        date_field = resolve_date_field(date_basis)
        filters: Dict[str, Any] = {}
        if report_category:
            filters["report_category"] = report_category
        dates = await exchange_report_repository.dates_with_reports(
            filters, limit=limit, date_field=date_field
        )
        return {
            "items": dates,
            "total": len(dates),
            "date_basis": date_basis_of(date_field),
        }

    async def get_report(self, report_id: str) -> Optional[Dict[str, Any]]:
        from ..repository.exchange_report_repository import COLLECTION

        document = await exchange_report_repository.find_by_id(COLLECTION, report_id)
        if not document:
            return None
        document["id"] = str(document.pop("_id", ""))
        return document


exchange_report_service = ExchangeReportService()
