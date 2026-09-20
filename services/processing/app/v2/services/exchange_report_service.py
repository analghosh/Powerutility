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

The operator's own choices are checked against the file too. Picking G-DAM and
uploading a DAM report is an easy slip, and it is not one the data can survive:
the report carries no explicit market of its own, so a mis-filed report is a
wrong number in the reports section with nothing to trace it back to.
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from libs.parsers.exchange_obligation_report_parser import (
    ExchangeObligationReportParser,
    ObligationReportParseError,
)

from ..exchange_reports import registry
from ..repository.exchange_report_repository import exchange_report_repository

logger = logging.getLogger(__name__)


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
    ) -> Dict[str, Any]:
        category = self._require_available_category(report_category)
        self._require_accepted_extension(category, filename)
        self._require_within_size_limit(data)
        self._require_known_choice("exchange", exchange, registry.EXCHANGES)
        self._require_known_choice("market_type", market_type, registry.MARKETS)

        parsed = self._parse(category, data, filename)

        self._require_file_agrees_with_choices(
            parsed,
            exchange=exchange,
            market_type=market_type,
            delivery_date=delivery_date,
        )
        self._require_every_check_passed(parsed)

        document = self._to_document(
            parsed,
            category=category,
            exchange=exchange,
            market_type=market_type,
            filename=filename,
            data=data,
            actor=actor,
        )
        upload_event = {
            "uploaded_at": document["uploaded_at"],
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
        raise UploadRejected(
            "unsupported_file_type",
            f"{category.label} is published as {accepted}. "
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
    def _require_file_agrees_with_choices(
        parsed: Dict[str, Any],
        *,
        exchange: str,
        market_type: str,
        delivery_date: str,
    ) -> None:
        """
        Refuse a file that contradicts what the operator selected.

        The market is read from the report's own pages, not from its filename,
        so renaming a file cannot get it past this.
        """
        mismatches: List[Dict[str, Any]] = []

        detected_market = parsed.get("detected_market_type")
        if detected_market and detected_market != market_type:
            mismatches.append(
                {
                    "field": "market_type",
                    "selected": market_type,
                    "found_in_file": detected_market,
                }
            )

        file_exchange = (parsed.get("filename_metadata") or {}).get("exchange")
        if file_exchange and file_exchange != exchange:
            mismatches.append(
                {
                    "field": "exchange",
                    "selected": exchange,
                    "found_in_file": file_exchange,
                }
            )

        file_delivery_date = parsed.get("delivery_date_iso")
        if file_delivery_date and file_delivery_date != delivery_date:
            mismatches.append(
                {
                    "field": "delivery_date",
                    "selected": delivery_date,
                    "found_in_file": file_delivery_date,
                }
            )

        if not mismatches:
            return

        described = "; ".join(
            f"{m['field']}: you selected {m['selected']}, the file says {m['found_in_file']}"
            for m in mismatches
        )
        raise UploadRejected(
            "file_does_not_match_selection",
            f"The file does not match what was selected - {described}.",
            {"mismatches": mismatches},
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
    ) -> Dict[str, Any]:
        """
        Build the stored document.

        The PDF's own bytes are not kept - only their SHA-256, which is enough
        to tell one upload from a re-upload of the same file, and to prove
        later which file a figure came from. The originals stay downloadable
        from the exchange, and the deployment is a single box with no backup.
        """
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
                "uploaded_at": datetime.now(timezone.utc),
                "uploaded_by": actor.get("user_id"),
                "uploaded_by_username": actor.get("username"),
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
        limit: int = 50,
        skip: int = 0,
    ) -> Dict[str, Any]:
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
            filters["delivery_date_iso"] = window

        documents = await exchange_report_repository.find_many(filters, limit=limit, skip=skip)
        total = await exchange_report_repository.count(filters)
        return {
            "items": [
                {"id": str(document["_id"]), **self.summarise(document)}
                for document in documents
            ],
            "total": total,
            "limit": limit,
            "skip": skip,
        }

    async def get_report(self, report_id: str) -> Optional[Dict[str, Any]]:
        from ..repository.exchange_report_repository import COLLECTION

        document = await exchange_report_repository.find_by_id(COLLECTION, report_id)
        if not document:
            return None
        document["id"] = str(document.pop("_id", ""))
        return document


exchange_report_service = ExchangeReportService()
