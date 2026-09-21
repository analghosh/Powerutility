"""
HTTP surface for the Report Uploader.

A refused upload has to tell the operator what to change, so nothing here
returns a bare 400. Every refusal carries a `reason` code the screen branches
on, a sentence the operator reads, and the detail they need to act:

* **Wrong file, unreadable file, file that contradicts the form, or a report
  that failed its consistency checks → 400**, naming what disagreed. These are
  all "fix the input and try again", which is why they share a status.
* **A category with no parser yet → 422.** The screen lists all four categories
  PTC asked for; three of them have no sample file, and choosing one is a
  request the server understands but cannot carry out.
* **Already uploaded → 409**, naming the file that is already stored and when
  it landed. Not a failure: the caller resends with `confirm_replace` once the
  operator has said yes. Overwriting is usually right, because the exchange
  reissues corrected reports under the same identity, but it silently changes
  a day's numbers and should be a decision rather than a double-click.

* **Over the size limit → 413**, with the limit, so the message can be exact.

Nothing on this router answers 200 with an error in the body. That pattern is
already costing PTC real bids elsewhere in this service, where
`submit-for-approval` returns 200 on failure and the frontend never looks.

Authentication is `AuthMiddleware`'s job, as on every other v2 router: these
paths are absent from main.py's exclude lists, so a request reaches here only
with a valid token. There is no per-route authorization because this branch
has none to call - the permission layer lives on the unmerged VAPT branch. An
upload writes to the reports section, so this router wants a permission of its
own the moment that layer lands.
"""

import logging
from datetime import date, datetime
from typing import Any, Dict, Optional

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query,
                     Request, UploadFile, status)

from libs.audit_queue.simple_logger import AuditLogger

from ....core.dependencies import get_audit_logger
from ...dependencies import get_user_context
from ...exchange_reports import registry
from ...services.exchange_report_service import (AlreadyUploaded,
                                                 CategoryNotAvailable,
                                                 FileTooLarge, UploadRejected,
                                                 exchange_report_service)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/exchange-reports",
    tags=["Exchange Reports"],
)

AUDIT_SERVICE = "processing"

#: Refusal codes that are a server-understood request it cannot fulfil, rather
#: than a malformed one.
UNPROCESSABLE_REASONS = {"category_not_available"}


def _rejection_status(rejection: UploadRejected) -> int:
    if isinstance(rejection, AlreadyUploaded):
        return status.HTTP_409_CONFLICT
    if isinstance(rejection, FileTooLarge):
        return status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    if isinstance(rejection, CategoryNotAvailable) or rejection.reason in UNPROCESSABLE_REASONS:
        return status.HTTP_422_UNPROCESSABLE_ENTITY
    return status.HTTP_400_BAD_REQUEST


def _as_http_error(rejection: UploadRejected) -> HTTPException:
    return HTTPException(
        status_code=_rejection_status(rejection),
        detail={
            "reason": rejection.reason,
            "message": rejection.message,
            **rejection.details,
        },
    )


def _parse_iso_date(value: str, field: str) -> str:
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "reason": "invalid_date",
                "message": f"'{value}' is not a date. {field} must be YYYY-MM-DD.",
            },
        )


def _audit(
    audit: Optional[AuditLogger],
    request: Request,
    user_context: Dict[str, Any],
    *,
    action: str,
    message: str,
    success: bool,
    **extra: Any,
) -> None:
    """Record the attempt. A broken audit logger must not fail the upload."""
    if audit is None:
        return
    forwarded = request.headers.get("X-Forwarded-For")
    ip = (
        forwarded.split(",")[0].strip()
        if forwarded
        else (request.client.host if request.client else None)
    )
    try:
        audit.log_user_action(
            service=AUDIT_SERVICE,
            user_id=str(user_context.get("user_id") or "anonymous"),
            action=action,
            message=message,
            username=user_context.get("username"),
            ip_address=ip,
            correlation_id=user_context.get("correlation_id"),
            success=success,
            data_type="exchange_uploaded_report",
            request_path=str(request.url.path),
            **extra,
        )
    except Exception as exc:  # pragma: no cover - broker failure only
        logger.error("Exchange report audit logging failed: %s", exc)


def _optional_audit_logger() -> Optional[AuditLogger]:
    try:
        return get_audit_logger()
    except Exception as exc:  # pragma: no cover - broker/config failure only
        logger.error("Exchange report audit logging unavailable: %s", exc)
        return None


@router.get(
    "/categories",
    summary="Report categories, exchanges and markets the uploader accepts",
)
async def get_upload_options() -> Dict[str, Any]:
    """
    Drives the screen's three dropdowns and its drop-zone hint text.

    The frontend reads this rather than hard-coding the lists, so a category
    gaining a parser, or an exchange being added, changes one table on the
    server and not two codebases.
    """
    return {
        "data": {
            "categories": registry.describe_categories(),
            "exchanges": registry.EXCHANGES,
            "markets": registry.MARKETS,
            "max_upload_bytes": registry.MAX_UPLOAD_BYTES,
        }
    }


@router.post(
    "/upload",
    status_code=status.HTTP_201_CREATED,
    summary="Upload and store one exchange report",
    responses={
        400: {"description": "The file was unreadable, contradicted the form, or failed its checks"},
        409: {"description": "Already uploaded; resend with confirm_replace to overwrite"},
        413: {"description": "Larger than the upload limit"},
        422: {"description": "That report category has no parser yet"},
    },
)
async def upload_report(
    request: Request,
    file: UploadFile = File(..., description="The report file published by the exchange"),
    report_category: str = Form(..., description="A code from GET /exchange-reports/categories"),
    exchange: str = Form(..., description="IEX, PXIL or HPX"),
    market_type: str = Form(..., description="DAM, GDAM, RTM or HPDAM"),
    delivery_date: str = Form(..., description="Delivery date, YYYY-MM-DD"),
    confirm_replace: bool = Form(
        False,
        description="Replace a report already stored for this identity",
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
) -> Dict[str, Any]:
    audit = _optional_audit_logger()
    normalised_date = _parse_iso_date(delivery_date, "delivery_date")
    filename = file.filename or ""
    data = await file.read()

    try:
        result = await exchange_report_service.upload(
            data=data,
            filename=filename,
            report_category=report_category,
            exchange=exchange,
            market_type=market_type,
            delivery_date=normalised_date,
            actor=user_context,
            confirm_replace=confirm_replace,
        )
    except UploadRejected as rejection:
        _audit(
            audit,
            request,
            user_context,
            action="upload",
            message=f"Rejected {filename}: {rejection.message}",
            success=False,
            report_category=report_category,
            exchange=exchange,
            market_type=market_type,
            delivery_date=normalised_date,
            rejection_reason=rejection.reason,
        )
        raise _as_http_error(rejection) from rejection

    _audit(
        audit,
        request,
        user_context,
        action="upload",
        message=(
            f"Stored {result['summary']['report_category_label']} "
            f"{exchange} {market_type} for {normalised_date} from {filename}"
        ),
        success=True,
        report_category=report_category,
        exchange=exchange,
        market_type=market_type,
        delivery_date=normalised_date,
        record_count=result["summary"]["block_count"],
        replaced_previous_upload=result["replaced_previous_upload"],
    )
    return {"data": result}


@router.get(
    "",
    summary="List uploaded reports, newest delivery date first",
)
async def list_reports(
    report_category: Optional[str] = Query(None),
    exchange: Optional[str] = Query(None),
    market_type: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, description="Delivery date, YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="Delivery date, YYYY-MM-DD"),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
    user_context: Dict[str, Any] = Depends(get_user_context),
) -> Dict[str, Any]:
    """
    Rows carry the headline figures only. The 96-block arrays are left out -
    a 50-row page would otherwise be megabytes of numbers nothing displays.
    """
    result = await exchange_report_service.list_reports(
        report_category=report_category,
        exchange=exchange,
        market_type=market_type,
        date_from=_parse_iso_date(date_from, "date_from") if date_from else None,
        date_to=_parse_iso_date(date_to, "date_to") if date_to else None,
        limit=limit,
        skip=skip,
    )
    return {"data": result}


@router.get(
    "/{report_id}",
    summary="One uploaded report, with all its time blocks",
    responses={404: {"description": "No such report"}},
)
async def get_report(
    report_id: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
) -> Dict[str, Any]:
    document = await exchange_report_service.get_report(report_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "reason": "not_found",
                "message": "No uploaded report with that id.",
            },
        )
    return {"data": document}
