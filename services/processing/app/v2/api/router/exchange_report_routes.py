"""
HTTP surface for the Report Uploader.

A refused upload has to tell the operator what to change, so nothing here
returns a bare 400. Every refusal carries a `reason` code the screen branches
on, a sentence the operator reads, and the detail they need to act:

* **Wrong file, unreadable file, file that contradicts the form, or a report
  that failed its consistency checks → 400**, naming what disagreed. These are
  all "fix the input and try again", which is why they share a status.
* **A category with no parser yet → 422.** The screen lists every category PTC
  asked for, and one of them still has no sample file; choosing it is a request
  the server understands but cannot carry out.
* **Already uploaded → 409**, naming the file that is already stored and when
  it landed. Not a failure: the caller resends with `confirm_replace` once the
  operator has said yes. Overwriting is usually right, because the exchange
  reissues corrected reports under the same identity, but it silently changes
  a day's numbers and should be a decision rather than a double-click.

* **Over the size limit → 413**, with the limit, so the message can be exact.
* **A form field the chosen category needs but did not get → 400**, naming the
  field as the screen labels it. Which fields those are depends on the
  category: a delivery-dated one needs a Delivery Date, a month-keyed one
  needs a Year and a Month, and neither should be answered with FastAPI's own
  `body -> period_year`.

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
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query,
                     Request, Response, UploadFile, status)
from fastapi.responses import FileResponse

from libs.audit_queue.simple_logger import AuditLogger

from ....core.dependencies import get_audit_logger
from ...dependencies import get_user_context
from ...exchange_reports import registry
from ...exchange_reports.registry import DEFAULT_DATE_BASIS
from ...services.exchange_report_service import (AlreadyUploaded,
                                                 CategoryNotAvailable,
                                                 FileTooLarge, UploadRejected,
                                                 exchange_report_service)
from ...services import dsm_report_service as dsm_service
from ...services.dsm_report_service import dsm_report_service
from ...services.other_report_service import other_report_service
from ...services.period_report_service import period_report_service

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


#: India Standard Time. The uploader's Uploaded Date is a wall clock the
#: operator reads off the screen in front of them, so it is IST, not UTC and
#: not the browser's zone - a laptop left on a foreign timezone would
#: otherwise file a report hours into the wrong shift.
IST = timezone(timedelta(hours=5, minutes=30))

#: How far ahead of the server clock a declared upload time may be. Wide
#: enough for ordinary clock skew between the operator's machine and the box,
#: narrow enough that a mistyped year is still refused.
FUTURE_UPLOAD_TOLERANCE = timedelta(minutes=5)


def _parse_upload_moment(value: str) -> datetime:
    """
    The operator's Uploaded Date and time, as the UTC instant it names.

    Read as IST because that is the clock the form shows. The browser sends a
    `datetime-local` value with no zone on it at all, so something has to
    decide what zone it meant, and deciding here means one answer rather than
    one per reader's machine.

    A time in the future is refused rather than clamped. The shift view is laid
    out by upload time block, and a report recorded as arriving in a block that
    has not happened yet would sit on a shift nobody has worked - a silent
    wrong answer, where a refusal is one the operator can fix.
    """
    try:
        parsed = datetime.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "reason": "invalid_upload_time",
                "message": (
                    f"'{value}' is not a date and time. Uploaded Date must be "
                    "YYYY-MM-DDTHH:MM."
                ),
                "field": "uploaded_at",
            },
        )

    # A value that already carries a zone is respected; a bare one is IST.
    localised = parsed if parsed.tzinfo else parsed.replace(tzinfo=IST)
    as_utc = localised.astimezone(timezone.utc)

    if as_utc > datetime.now(timezone.utc) + FUTURE_UPLOAD_TOLERANCE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "reason": "upload_time_in_future",
                "message": (
                    "The Uploaded Date is in the future. A report cannot be "
                    "recorded as having arrived before it did."
                ),
                "field": "uploaded_at",
                "uploaded_at": localised.isoformat(),
            },
        )
    return as_utc


def _require_period(
    period_year: Optional[int],
    period_month: Optional[int],
    *,
    label: str,
) -> None:
    """
    Year and Month for a month-keyed category.

    Checked here rather than in the signature so the refusal names the field
    on the screen. FastAPI's own 422 for a missing form field says
    `body -> period_year`, which is not a label the operator has ever seen.
    """
    if period_year is None or period_month is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "reason": "missing_period",
                "message": (
                    f"{label} covers a calendar month, so it needs a Year and "
                    "a Month."
                ),
                "field": "period_year" if period_year is None else "period_month",
            },
        )
    if not 1 <= period_month <= 12:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "reason": "invalid_period",
                "message": f"'{period_month}' is not a month. Months are 1 to 12.",
                "field": "period_month",
            },
        )
    # An upper bound as well as a lower one: a report can be filed for next
    # month, but a year beyond that is a typo rather than a forward filing.
    latest = date.today().year + 1
    if not registry.MIN_PERIOD_YEAR <= period_year <= latest:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "reason": "invalid_period",
                "message": (
                    f"'{period_year}' is not a year this report can be filed "
                    f"under. Use {registry.MIN_PERIOD_YEAR} to {latest}."
                ),
                "field": "period_year",
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


#: Content types for the sample files. Stated rather than guessed from the
#: host's mime table, which on a bare server may not know .xlsx at all.
SAMPLE_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
}


@router.get(
    "/categories/{category_code}/sample",
    summary="Download the sample file for one report category",
    responses={404: {"description": "No such category, or it has no sample file"}},
)
async def download_category_sample(category_code: str) -> FileResponse:
    """
    What a file for this category should look like.

    Offered on the uploader for the parsed categories only, so an operator
    can see the layout the parser expects before their upload is refused
    for not matching it.
    """
    category = registry.get_category(category_code)
    path = category.sample_path if category else None
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="No sample file for that category")

    return FileResponse(
        path,
        media_type=SAMPLE_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        headers={
            "Content-Disposition": f'attachment; filename="{category.sample_file}"'
        },
    )


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
    # Optional at the signature, required by the parsing flow's own rules.
    # A store-only category asks for neither: the file can be about anything,
    # so a forced IEX/DAM would be an answer nobody meant. The obligation flow
    # still rejects a missing one, with its own message.
    exchange: Optional[str] = Form(None, description="IEX, PXIL or HPX"),
    market_type: Optional[str] = Form(None, description="DAM, GDAM, RTM or HPDAM"),
    # Optional at the signature, required by whichever flow the category
    # selects. A month-keyed category asks for Year and Month instead: a
    # report covering September has no one day the power flowed on, so a
    # delivery date there would be a value nobody chose.
    delivery_date: Optional[str] = Form(None, description="Delivery date, YYYY-MM-DD"),
    period_year: Optional[int] = Form(None, description="Year, for a month-keyed category"),
    period_month: Optional[int] = Form(None, description="Month 1-12, for a month-keyed category"),
    # The operator's own answer to "when did this land", defaulting on the
    # screen to now. Reports are often typed in during a later shift than the
    # one they belong to, and the shift view is laid out by upload time block.
    # Absent, the server clock stands - see `resolve_upload_moment`, which
    # keeps the observed arrival time either way.
    uploaded_at: Optional[str] = Form(
        None,
        description="Uploaded date and time as an IST wall clock, YYYY-MM-DDTHH:MM",
    ),
    confirm_replace: bool = Form(
        False,
        description="Replace a report already stored for this identity",
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
) -> Dict[str, Any]:
    audit = _optional_audit_logger()
    filename = file.filename or ""
    declared_upload_moment = (
        _parse_upload_moment(uploaded_at) if uploaded_at else None
    )

    # One uploader screen, three destinations. A store-only category has no
    # parser and no parsed identity, so it cannot go through the obligation
    # flow at all, and a month-keyed one is filed under a month rather than a
    # day - the category decides, rather than the caller having to know which
    # endpoint to post to.
    selected = registry.get_category(report_category)

    if selected is not None and selected.period_keyed:
        _require_period(period_year, period_month, label=selected.label)
        data = await file.read()
        return await _upload_period_report(
            request=request,
            audit=audit,
            data=data,
            filename=filename,
            report_category=selected.code,
            period_year=period_year,
            period_month=period_month,
            uploaded_at=declared_upload_moment,
            user_context=user_context,
        )

    if selected is not None and selected.dates_from_file:
        data = await file.read()
        return await _upload_dsm_report(
            request=request,
            audit=audit,
            data=data,
            filename=filename,
            uploaded_at=declared_upload_moment,
            confirm_replace=confirm_replace,
            user_context=user_context,
        )

    if not delivery_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "reason": "missing_delivery_date",
                "message": "A Delivery Date is needed for this report category.",
                "field": "delivery_date",
            },
        )
    normalised_date = _parse_iso_date(delivery_date, "delivery_date")
    data = await file.read()

    if selected is not None and selected.stores_file_only:
        return await _upload_other_report(
            request=request,
            audit=audit,
            data=data,
            filename=filename,
            delivery_date=normalised_date,
            uploaded_at=declared_upload_moment,
            user_context=user_context,
        )

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
            uploaded_at=declared_upload_moment,
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


async def _upload_other_report(
    *,
    request: Request,
    audit: Optional[AuditLogger],
    data: bytes,
    filename: str,
    delivery_date: str,
    uploaded_at: Optional[datetime],
    user_context: Dict[str, Any],
) -> Dict[str, Any]:
    """Store the file as uploaded, and audit it the same way a parse is."""
    try:
        result = await other_report_service.upload(
            data=data,
            filename=filename,
            delivery_date=delivery_date,
            actor=user_context,
            uploaded_at=uploaded_at,
        )
    except UploadRejected as rejection:
        _audit(
            audit,
            request,
            user_context,
            action="upload",
            message=f"Rejected {filename}: {rejection.message}",
            success=False,
            report_category=registry.OTHER_REPORTS,
            delivery_date=delivery_date,
            rejection_reason=rejection.reason,
        )
        raise _as_http_error(rejection) from rejection

    _audit(
        audit,
        request,
        user_context,
        action="upload",
        message=f"Stored Other Report for {delivery_date} from {filename}",
        success=True,
        report_category=registry.OTHER_REPORTS,
        delivery_date=delivery_date,
        replaced_previous_upload=result["replaced_previous_upload"],
    )
    return {"data": result}


async def _upload_dsm_report(
    *,
    request: Request,
    audit: Optional[AuditLogger],
    data: bytes,
    filename: str,
    uploaded_at: Optional[datetime],
    confirm_replace: bool,
    user_context: Dict[str, Any],
) -> Dict[str, Any]:
    """Parse the statement, store its days, and audit it like any other parse."""
    try:
        result = await dsm_report_service.upload(
            data=data,
            filename=filename,
            actor=user_context,
            uploaded_at=uploaded_at,
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
            report_category=registry.DSM_REPORT,
            rejection_reason=rejection.reason,
        )
        raise _as_http_error(rejection) from rejection

    summary = result["summary"]
    _audit(
        audit,
        request,
        user_context,
        action="upload",
        message=(
            f"Stored DSM Report for {summary['report_date_from']} to "
            f"{summary['report_date_to']} ({summary['day_count']} days, "
            f"{summary['block_count']} blocks) from {filename}"
        ),
        success=True,
        report_category=registry.DSM_REPORT,
        delivery_date=summary["report_date_from"],
        record_count=summary["block_count"],
        replaced_previous_upload=result["replaced_previous_upload"],
        replaced_days=result["replaced_days"],
    )
    return {"data": result}


async def _upload_period_report(
    *,
    request: Request,
    audit: Optional[AuditLogger],
    data: bytes,
    filename: str,
    report_category: str,
    period_year: int,
    period_month: int,
    uploaded_at: Optional[datetime],
    user_context: Dict[str, Any],
) -> Dict[str, Any]:
    """Store a month's file as uploaded, and audit it the same way a parse is."""
    period = registry.period_label(period_year, period_month)
    try:
        result = await period_report_service.upload(
            data=data,
            filename=filename,
            report_category=report_category,
            period_year=period_year,
            period_month=period_month,
            actor=user_context,
            uploaded_at=uploaded_at,
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
            period=period,
            rejection_reason=rejection.reason,
        )
        raise _as_http_error(rejection) from rejection

    _audit(
        audit,
        request,
        user_context,
        action="upload",
        message=(
            f"Stored {result['summary']['report_category_label']} for "
            f"{period} from {filename}"
        ),
        success=True,
        report_category=report_category,
        period=period,
        replaced_previous_upload=result["replaced_previous_upload"],
    )
    return {"data": result}


def _require_period_category(code: str) -> registry.ReportCategory:
    """The category a read route names, refusing one that is not month-keyed."""
    category = registry.get_category(code)
    if category is None or not category.period_keyed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "reason": "not_a_period_category",
                "message": (
                    f"'{code}' is not a report category filed by month."
                ),
                "period_categories": [
                    entry.code
                    for entry in registry.CATEGORIES.values()
                    if entry.period_keyed
                ],
            },
        )
    return category


@router.get(
    "/period",
    summary="List files uploaded under a month-keyed category (LTOA, STOA)",
)
async def list_period_reports(
    report_category: str = Query(..., description="ltoa or stoa"),
    year: Optional[int] = Query(None, ge=registry.MIN_PERIOD_YEAR),
    month: Optional[int] = Query(None, ge=1, le=12),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
    user_context: Dict[str, Any] = Depends(get_user_context),
) -> Dict[str, Any]:
    """
    Declared before `/{report_id}` so the path is not read as an id.

    Year and month are optional so a tab can also ask "what is filed under
    this category at all", which is what the empty state needs in order to
    suggest a month that has something in it.
    """
    category = _require_period_category(report_category)
    result = await period_report_service.list_reports(
        report_category=category.code,
        period_year=year,
        period_month=month,
        limit=limit,
        skip=skip,
    )
    return {"data": result}


@router.get(
    "/period/months",
    summary="Months that have files for a month-keyed category, newest first",
)
async def get_period_months(
    report_category: str = Query(..., description="ltoa or stoa"),
    limit: int = Query(60, ge=1, le=365),
    user_context: Dict[str, Any] = Depends(get_user_context),
) -> Dict[str, Any]:
    """
    The picker's hints. A Year/Month pair cannot tell "nothing was uploaded
    for September" from "I picked the wrong month" on its own.
    """
    category = _require_period_category(report_category)
    result = await period_report_service.periods(
        report_category=category.code, limit=limit
    )
    return {"data": result}


@router.get(
    "/period/{report_id}/file",
    summary="Download one LTOA / STOA file exactly as it was uploaded",
    responses={404: {"description": "No such report, or its file is not in storage"}},
)
async def download_period_report_file(
    report_id: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
) -> Response:
    try:
        stored = await period_report_service.get_file(report_id)
    except UploadRejected as rejection:
        raise _as_http_error(rejection) from rejection

    if stored is None:
        raise HTTPException(status_code=404, detail="No such report")

    # Sent as an attachment whatever the type, so a PDF or a spreadsheet is
    # saved rather than rendered in place. The name is quoted because uploaded
    # filenames contain spaces.
    return Response(
        content=stored["content"],
        media_type=stored["content_type"],
        headers={
            "Content-Disposition": f'attachment; filename="{stored["filename"]}"'
        },
    )


@router.get(
    "/other",
    summary="List files uploaded under Other Reports, newest upload first",
)
async def list_other_reports(
    date_from: Optional[str] = Query(None, description="Upload date, YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="Upload date, YYYY-MM-DD"),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
    user_context: Dict[str, Any] = Depends(get_user_context),
) -> Dict[str, Any]:
    """
    Declared before `/{report_id}` so the path is not read as an id.

    Always keyed on the upload day: nothing is read out of these files, so
    their delivery date carries no independent meaning.
    """
    result = await other_report_service.list_reports(
        date_from=_parse_iso_date(date_from, "date_from") if date_from else None,
        date_to=_parse_iso_date(date_to, "date_to") if date_to else None,
        limit=limit,
        skip=skip,
    )
    return {"data": result}


@router.get(
    "/other/dates",
    summary="Upload dates that have Other Reports, newest first",
)
async def get_other_report_dates(
    limit: int = Query(60, ge=1, le=365),
    user_context: Dict[str, Any] = Depends(get_user_context),
) -> Dict[str, Any]:
    result = await other_report_service.upload_dates(limit=limit)
    return {"data": result}


@router.get(
    "/other/{report_id}/file",
    summary="Download one Other Report exactly as it was uploaded",
    responses={404: {"description": "No such report, or its file is not in storage"}},
)
async def download_other_report_file(
    report_id: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
) -> Response:
    try:
        stored = await other_report_service.get_file(report_id)
    except UploadRejected as rejection:
        raise _as_http_error(rejection) from rejection

    if stored is None:
        raise HTTPException(status_code=404, detail="No such report")

    # Sent as an attachment whatever the type, so a PDF or a spreadsheet is
    # saved rather than rendered in place. The name is quoted because uploaded
    # filenames contain spaces.
    return Response(
        content=stored["content"],
        media_type=stored["content_type"],
        headers={
            "Content-Disposition": f'attachment; filename="{stored["filename"]}"'
        },
    )


def _dsm_windows(
    *,
    report_from: Optional[str],
    report_to: Optional[str],
    uploaded_from: Optional[str],
    uploaded_to: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    date_basis: Optional[str],
) -> tuple:
    """
    The settlement window and the upload window a DSM read asks for.

    Two windows rather than one window and a basis, because they are two
    different questions and an operator can mean both at once: "of the
    statements that came in yesterday, show me April". A basis could only ever
    express one of them.

    `date_from`/`date_to`/`date_basis` are the earlier single-window form and
    still work — they fill in whichever window the basis names, and only when
    that window was not given explicitly, so a caller mixing the two forms
    gets the explicit one rather than a silent override.
    """
    report = dsm_service.DateWindow(
        _parse_iso_date(report_from, "report_from") if report_from else None,
        _parse_iso_date(report_to, "report_to") if report_to else None,
    )
    uploaded = dsm_service.DateWindow(
        _parse_iso_date(uploaded_from, "uploaded_from") if uploaded_from else None,
        _parse_iso_date(uploaded_to, "uploaded_to") if uploaded_to else None,
    )

    if date_from or date_to:
        legacy = dsm_service.DateWindow(
            _parse_iso_date(date_from, "date_from") if date_from else None,
            _parse_iso_date(date_to, "date_to") if date_to else None,
        )
        if dsm_service.resolve_date_field(date_basis) == "report_date":
            report = report if report.is_set else legacy
        else:
            uploaded = uploaded if uploaded.is_set else legacy

    for window, label in ((report, "Settlement Date"), (uploaded, "Uploaded Date")):
        # Refused rather than answered with nothing. A backwards window returns
        # zero rows on every basis, which reads as "there is no data" instead
        # of "those two dates are the wrong way round".
        if window.start and window.end and window.start > window.end:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "reason": "backwards_window",
                    "message": (
                        f"The {label} window starts after it ends: "
                        f"{window.start} to {window.end}."
                    ),
                    "field": label,
                },
            )
    return report, uploaded


@router.get(
    "/dsm",
    summary="Settlement days that have DSM figures, without their blocks",
)
async def list_dsm_days(
    report_from: Optional[str] = Query(None, description="Settlement day, from, YYYY-MM-DD"),
    report_to: Optional[str] = Query(None, description="Settlement day, to, YYYY-MM-DD"),
    uploaded_from: Optional[str] = Query(None, description="Upload day, from, YYYY-MM-DD"),
    uploaded_to: Optional[str] = Query(None, description="Upload day, to, YYYY-MM-DD"),
    date_from: Optional[str] = Query(None, description="Single-window form: start"),
    date_to: Optional[str] = Query(None, description="Single-window form: end"),
    date_basis: str = Query(
        dsm_service.DEFAULT_DATE_BASIS,
        description=(
            "Which date `date_from`/`date_to` apply to: `report` (the "
            "settlement day, the default) or `uploaded`"
        ),
    ),
    limit: int = Query(62, ge=1, le=400),
    skip: int = Query(0, ge=0),
    user_context: Dict[str, Any] = Depends(get_user_context),
) -> Dict[str, Any]:
    """
    Declared before `/{report_id}` so the path is not read as an id.

    The blocks are left out: a fortnight of them is 1,440 rows, and a summary
    that carried them would be megabytes of numbers nothing displays.
    """
    report, uploaded = _dsm_windows(
        report_from=report_from,
        report_to=report_to,
        uploaded_from=uploaded_from,
        uploaded_to=uploaded_to,
        date_from=date_from,
        date_to=date_to,
        date_basis=date_basis,
    )
    result = await dsm_report_service.list_days(
        report=report, uploaded=uploaded, limit=limit, skip=skip
    )
    return {"data": result}


@router.get(
    "/dsm/rows",
    summary="Every DSM block in a window, as the screen and the export read it",
)
async def list_dsm_rows(
    report_from: Optional[str] = Query(None, description="Settlement day, from, YYYY-MM-DD"),
    report_to: Optional[str] = Query(None, description="Settlement day, to, YYYY-MM-DD"),
    uploaded_from: Optional[str] = Query(None, description="Upload day, from, YYYY-MM-DD"),
    uploaded_to: Optional[str] = Query(None, description="Upload day, to, YYYY-MM-DD"),
    date_from: Optional[str] = Query(None, description="Single-window form: start"),
    date_to: Optional[str] = Query(None, description="Single-window form: end"),
    date_basis: str = Query(
        dsm_service.DEFAULT_DATE_BASIS,
        description="Which date `date_from`/`date_to` apply to: `report` or `uploaded`",
    ),
    limit: int = Query(62, ge=1, le=400, description="Settlement days, not rows"),
    skip: int = Query(0, ge=0),
    user_context: Dict[str, Any] = Depends(get_user_context),
) -> Dict[str, Any]:
    """
    Both windows are optional and are ANDed when both are given, which is how
    "of the statements that came in yesterday, show me April" is asked.

    `limit` counts settlement days rather than rows, because that is the unit
    the operator picks: 1-15 April is fifteen days, and a cap expressed in rows
    would cut a day in half.
    """
    report, uploaded = _dsm_windows(
        report_from=report_from,
        report_to=report_to,
        uploaded_from=uploaded_from,
        uploaded_to=uploaded_to,
        date_from=date_from,
        date_to=date_to,
        date_basis=date_basis,
    )
    result = await dsm_report_service.rows(
        report=report, uploaded=uploaded, limit=limit, skip=skip
    )
    return {"data": result}


@router.get(
    "/dsm/dates",
    summary="Which days have DSM figures, on both the settlement and the upload basis",
)
async def get_dsm_dates(
    limit: int = Query(400, ge=1, le=1000),
    user_context: Dict[str, Any] = Depends(get_user_context),
) -> Dict[str, Any]:
    """Both bases in one call: the screen offers both, and a second round trip
    on every toggle would be a spinner where a dropdown should be."""
    result = await dsm_report_service.dates(limit=limit)
    return {"data": result}


@router.get(
    "/dsm/uploads",
    summary="DSM statements as uploaded, newest first",
)
async def list_dsm_uploads(
    date_from: Optional[str] = Query(None, description="Upload date, YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="Upload date, YYYY-MM-DD"),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
    user_context: Dict[str, Any] = Depends(get_user_context),
) -> Dict[str, Any]:
    result = await dsm_report_service.list_uploads(
        date_from=_parse_iso_date(date_from, "date_from") if date_from else None,
        date_to=_parse_iso_date(date_to, "date_to") if date_to else None,
        limit=limit,
        skip=skip,
    )
    return {"data": result}


@router.get(
    "/dsm/{report_id}/file",
    summary="Download one DSM statement exactly as it was uploaded",
    responses={404: {"description": "No such statement, or its file is not in storage"}},
)
async def download_dsm_report_file(
    report_id: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
) -> Response:
    try:
        stored = await dsm_report_service.get_file(report_id)
    except UploadRejected as rejection:
        raise _as_http_error(rejection) from rejection

    if stored is None:
        raise HTTPException(status_code=404, detail="No such report")

    return Response(
        content=stored["content"],
        media_type=stored["content_type"],
        headers={
            "Content-Disposition": f'attachment; filename="{stored["filename"]}"'
        },
    )


@router.get(
    "",
    summary="List uploaded reports, newest delivery date first",
)
async def list_reports(
    report_category: Optional[str] = Query(None),
    exchange: Optional[str] = Query(None),
    market_type: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, description="Start of the date window, YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="End of the date window, YYYY-MM-DD"),
    date_basis: str = Query(
        DEFAULT_DATE_BASIS,
        description=(
            "Which date `date_from`/`date_to` apply to: `uploaded` (the day the "
            "report was put in, the default) or `delivery` (the day the power flows)"
        ),
    ),
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
        date_basis=date_basis,
        limit=limit,
        skip=skip,
    )
    return {"data": result}


@router.get(
    "/delivery-dates",
    summary="Dates that have uploaded reports, newest first",
)
async def get_delivery_dates(
    report_category: Optional[str] = Query("obligation_report"),
    date_basis: str = Query(
        DEFAULT_DATE_BASIS,
        description="`uploaded` (default) or `delivery` — which date to group by",
    ),
    limit: int = Query(60, ge=1, le=365),
    user_context: Dict[str, Any] = Depends(get_user_context),
) -> Dict[str, Any]:
    """
    Declared before `/{report_id}` so the path is not read as an id.

    The path keeps its original name so links and the shipped client keep
    working; `date_basis` decides which date the days are actually grouped by.
    """
    result = await exchange_report_service.delivery_dates(
        report_category=report_category,
        date_basis=date_basis,
        limit=limit,
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
