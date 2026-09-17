"""HTTP surface for the analytics dashboard.

The routes are thin, and the interesting part of them is the error mapping. Three
failures have to arrive at the client as something it can act on, and the default
for all three is a 500 that looks like a broken server:

* **Over budget → 413**, carrying the estimate and its suggestions. The budget is
  arithmetic done before any database call, so this refusal costs about a
  millisecond; what makes it useful is that it names the numbers ("153 days x 96
  blocks x 34 plants = 513,940 values") and the two narrowings that would fit. The
  frontend opens its budget dialog on this status specifically, so it must not be a
  400 or a 422.

* **A dataset with no storage → 422**, naming the dataset. `WEEKLY_FORECAST` and the
  other `absent` entries are legal codes — the request model accepts them because
  they exist in the registry — and the planner refuses them by name. Left unmapped
  that is a 500; rendered as an empty column it would be a report that is silently
  missing a series.

* **A plant lookup that failed → 503**, never an empty list. Everywhere else in this
  service a failed read returns `[]`; here that would leave Apply permanently
  disabled with no explanation.

Nothing on this router returns 200 with an error envelope in the body. That pattern
already costs PTC real bids: `submit-for-approval` answers 200 on failure and the
frontend never checks, so a lost bid is indistinguishable from a saved one.

Authentication is `AuthMiddleware`'s job, exactly as for every other v2 router:
`get_user_context` returns an *anonymous* context rather than raising, so it is the
absence of `/api/v2/analytics` from main.py's exclude lists that protects these
paths. Authorization is per dataset and happens in the service, not here — one
request can carry datasets under two different grants, so there is nothing a route
guard could usefully assert.

The export routes in the second half of this file are the exception: a job row has
a single owner, so those five routes *do* carry an authorization check of their own.
Their reasoning is set out above `POST /export`.
"""
import logging
import os
import secrets
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.background import BackgroundTask
from starlette.responses import FileResponse, JSONResponse, StreamingResponse

from libs.audit_queue.simple_logger import AuditLogger

from ....core.dependencies import get_audit_logger
from ...analytics import planner, registry
from ...analytics.models import AnalyticsQueryRequest, ExportRequest
from ...controller.analytics_controller import (DatasetHasNoPlantsError,
                                                EntityListUnavailableError,
                                                analytics_controller)
from ...dependencies import get_user_context
from ...helpers.analytics_xlsx import ExportWriteError
from ...repository.analytics_job_repository import (ACTIVE_STATUSES,
                                                    STATUS_CANCELLED,
                                                    STATUS_EXPIRED, STATUS_FAILED,
                                                    STATUS_QUEUED, STATUS_READY)
from ...services.analytics_export_service import (MEDIA_TYPES, MODE_INLINE,
                                                  MODE_JOB, MODE_STREAM,
                                                  AnalyticsExportService,
                                                  ExportStorageExhausted)
from ...services.analytics_export_service import cleanup as cleanup_export
from ...services.analytics_service import BudgetExceeded

logger = logging.getLogger(__name__)

# Plain integers rather than `fastapi.status` constants: Starlette renamed both
# HTTP_413_* and HTTP_422_* and deprecated the old spellings, so either name emits
# a warning on one of the two versions this code has to run on.
HTTP_PAYLOAD_TOO_LARGE = 413
HTTP_UNPROCESSABLE = 422
HTTP_SERVICE_UNAVAILABLE = 503

# prefix and tags belong to the router: main.py includes it bare.
router = APIRouter(
    prefix="/analytics",
    tags=["Analytics"],
)


@router.get(
    "/datasets",
    summary="Dataset catalogue",
    response_description="Every dataset with its availability, cardinality and grant",
)
async def list_datasets(
    user_context: Dict[str, Any] = Depends(get_user_context),
):
    return await analytics_controller.datasets(user_context)


@router.get(
    "/plants",
    summary="Plant list for a dataset",
    response_description="The plant ids present in the collection this dataset reads",
)
async def list_plants(
    dataset: str = Query(
        ...,
        description="Dataset code whose plant universe is wanted, e.g. DC_PLANT. "
                    "supply_serve and serve_dam_scheduling do not hold the same plants.",
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
):
    try:
        return await analytics_controller.plants(dataset, user_context)
    except registry.UnknownDatasetError:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE,
            detail={"code": "ANALYTICS_UNKNOWN_DATASET",
                    "message": f"Unknown dataset code: {dataset}"},
        )
    except DatasetHasNoPlantsError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE,
            detail={"code": "ANALYTICS_DATASET_HAS_NO_PLANTS",
                    "message": str(exc), "dataset": dataset},
        )
    except EntityListUnavailableError as exc:
        # Not an empty list: the selector must be able to say "unavailable".
        raise HTTPException(
            status_code=HTTP_SERVICE_UNAVAILABLE,
            detail={"code": "ANALYTICS_PLANTS_UNAVAILABLE",
                    "message": str(exc), "dataset": dataset},
        )


@router.get(
    "/block-bands",
    summary="Configured block bands",
    response_description="Block grouping bands, with the inactive ones flagged",
)
async def list_block_bands(
    user_context: Dict[str, Any] = Depends(get_user_context),
):
    return await analytics_controller.block_bands(user_context)


@router.post(
    "/query",
    summary="Run an analytics query",
    response_description="axes + series + meta, or 413 when the selection is too large",
)
async def run_query(
    request: AnalyticsQueryRequest,
    user_context: Dict[str, Any] = Depends(get_user_context),
):
    try:
        return await analytics_controller.query(request, user_context)

    except BudgetExceeded as exc:
        # 413 specifically: the frontend opens its budget dialog on this status.
        raise HTTPException(
            status_code=HTTP_PAYLOAD_TOO_LARGE,
            detail={
                "code": "ANALYTICS_BUDGET_EXCEEDED",
                "message": str(exc),
                "estimate": asdict(exc.estimate),
                "suggestions": exc.suggestions,
            },
        )

    except planner.UnavailableDatasetError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE,
            detail={"code": "ANALYTICS_DATASET_UNAVAILABLE",
                    "message": str(exc), "datasets": exc.codes},
        )

    except registry.UnknownDatasetError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE,
            detail={"code": "ANALYTICS_UNKNOWN_DATASET",
                    "message": f"Unknown dataset code: {exc.args[0]}"},
        )

    except ValueError as exc:
        # An illegal filter combination the request model could not see — an
        # inactive block band, a block range outside a custom bucket. The user's
        # input is wrong, so 422 rather than 500.
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE,
            detail={"code": "ANALYTICS_INVALID_REQUEST", "message": str(exc)},
        )

    except Exception as exc:
        logger.exception("Analytics query failed")
        raise HTTPException(
            status_code=500,
            detail={"code": "ANALYTICS_QUERY_FAILED",
                    "message": f"The analytics query could not be completed: {exc}"},
        )


# ===========================================================================
# Export
# ===========================================================================
#
# `POST /export` is three responses wearing one URL — a streamed CSV, a finished
# workbook, or a 202 receipt — and the four routes after it exist only to get a
# queued file back to the person who asked for it. That handover is where the
# security of this feature lives.
#
# **A job id is not a capability.** Ids are short and they travel: in a URL, in a
# support ticket, in a screenshot of the My Exports panel. An export can hold five
# months of plant-level scheduling for the whole portfolio, so serving any job to
# any logged-in caller would be the same shape as `submit-to-exchange` shipping
# with no authorization — authenticated, therefore assumed authorized. Every job
# route is checked against the `user_id` stored on the row, and that check comes
# before the download token, so a wrong token can never downgrade an authorization
# failure into "bad link".
#
# **The token is a second factor, not a session.** It binds a link to one job and
# expires with the file (72h). It is deliberately *not* an excuse to exclude this
# path from `AuthMiddleware`: a file route is the tempting one to make public so a
# plain `<a href>` can fetch it, and that is how an id-guessing hole gets shipped.
#
# **Expiry is read as UTC whichever shape Mongo returns.** Motor hands back naive
# datetimes unless the client is built `tz_aware`; comparing one against a naive
# *local* now shifts every expiry by the UTC offset, which is exactly the defect
# that made every EPMS access token last six hours instead of thirty minutes.
#
# **A refusal is never a 200.** A cancel that changed nothing answers 409, not a
# cheerful 200 — `update-status` on a lapsed bid already taught PTC what a silent
# no-op costs. An expired file answers 410 carrying the filters that would rebuild
# it, because the row is kept for 90 days after the file goes precisely so that
# Regenerate works; 404 would throw that away and read as "your export never
# existed".


HTTP_ACCEPTED = 202
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
HTTP_CONFLICT = 409
HTTP_GONE = 410
HTTP_INSUFFICIENT_STORAGE = 507

#: `get_user_context` answers with this instead of raising when no token was
#: presented, so every route below has to refuse it explicitly. It would otherwise
#: become the owner of a queued job, and an unowned job is downloadable by whoever
#: asks next.
ANONYMOUS_USER_ID = "anonymous"

DEFAULT_EXPORT_LIST_LIMIT = 50
MAX_EXPORT_LIST_LIMIT = 200

AUDIT_SERVICE = "processing"
AUDIT_DOWNLOAD = "analytics_export_download"
AUDIT_DOWNLOAD_DENIED = "analytics_export_download_denied"

#: Fields of a job row the client is allowed to see. Everything else — the
#: absolute path of the result, the worker id, the temp part files — stays server
#: side; a filesystem path in a JSON payload is a gift to anyone probing for a
#: traversal, and the client has no use for it.
JOB_VIEW_FIELDS = (
    "job_id", "status", "label", "cells_estimate", "chunks_done", "chunks_total",
    "attempts", "max_attempts", "created_at", "started_at", "finished_at",
    "expires_at", "error", "download_token", "token_expires_at", "filters",
)
JOB_DATE_FIELDS = ("created_at", "started_at", "finished_at", "expires_at",
                   "token_expires_at")
RESULT_VIEW_FIELDS = ("filename", "bytes", "row_count")

_SAFE_FILENAME_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")

#: The export service singleton the routes talk to. Like the controller, it builds
#: nothing at import: a Motor client constructed here would bind to the wrong event
#: loop and every query would answer `[]` instead of raising.
analytics_export_service = AnalyticsExportService()


def get_optional_audit_logger() -> Optional[AuditLogger]:
    """The audit logger, or None if it could not be built.

    `get_audit_logger` raises when the unified queue service cannot start, and as a
    hard dependency that would turn an audit-transport outage into a download
    outage. The entry matters, but it is not worth denying a file the caller is
    already authorized to have; a missing logger is logged loudly instead.
    """
    try:
        return get_audit_logger()
    except Exception as exc:  # pragma: no cover - broker/config failure only
        logger.error("Analytics export audit logging unavailable: %s", exc)
        return None


def _caller_id(user_context: Optional[dict]) -> str:
    """The authenticated caller's id, or 401. `anonymous` is not an owner."""
    user_id = (user_context or {}).get("user_id")
    if not user_id or str(user_id) == ANONYMOUS_USER_ID:
        raise HTTPException(
            status_code=HTTP_UNAUTHORIZED,
            detail={"code": "ANALYTICS_AUTH_REQUIRED",
                    "message": "An export is owned by the user who asked for it, so "
                               "this route needs an authenticated session."},
        )
    return str(user_id)


def _as_utc(value: Any) -> Optional[datetime]:
    """Read a stored timestamp as UTC, tz-aware, whatever shape it arrived in."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        # Motor returns naive datetimes unless the client is tz_aware, and every
        # timestamp this service writes is UTC.
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: Any) -> Optional[str]:
    moment = _as_utc(value)
    return moment.isoformat() if moment else None


def _safe_filename(name: Any, fallback: str) -> str:
    """A basename safe to put in a response header.

    The stored filename is generated by `analytics_csv.filename_for`, but it is
    read back out of Mongo, and a header is the one place where an unexpected
    newline stops being a cosmetic problem.
    """
    text = os.path.basename(str(name or "")).strip()
    cleaned = "".join(ch if ch in _SAFE_FILENAME_CHARS else "_" for ch in text)
    cleaned = cleaned.strip("._")
    return cleaned or fallback


def _disposition(filename: str) -> str:
    return f'attachment; filename="{filename}"'


def _progress(job: Dict[str, Any]) -> float:
    total = int(job.get("chunks_total") or 0)
    done = int(job.get("chunks_done") or 0)
    if job.get("status") == STATUS_READY:
        return 1.0
    if total <= 0:
        return 0.0
    return round(min(done / total, 1.0), 4)


def _job_view(job: Dict[str, Any]) -> Dict[str, Any]:
    """The client's view of a job row: progress, outcome, and nothing from disk."""
    view = {field: job.get(field) for field in JOB_VIEW_FIELDS}
    for field in JOB_DATE_FIELDS:
        view[field] = _iso(job.get(field))

    result = job.get("result") or {}
    view["result"] = (
        {field: result.get(field) for field in RESULT_VIEW_FIELDS} if result else None
    )
    view["progress"] = _progress(job)
    view["format"] = (job.get("filters") or {}).get("format")
    view["can_download"] = job.get("status") == STATUS_READY
    view["can_cancel"] = job.get("status") in ACTIVE_STATUSES
    # The row outlives the file by 87 days so that this stays true.
    view["can_regenerate"] = bool(job.get("filters"))
    return view


def _regenerate_hint(job: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "job_id": job.get("job_id"),
        "filters": job.get("filters") or {},
        "message": "The generated file has been deleted, but the filters that "
                   "produced it are kept — re-run this export to rebuild it.",
    }


def _audit_download(audit: Optional[AuditLogger], http_request: Request,
                    user_context: Optional[dict], *, action: str, job_id: str,
                    message: str, success: bool, **extra) -> None:
    """Record a download attempt. Never raises: logging must not deny the file."""
    if audit is None:
        return
    ctx = user_context or {}
    forwarded = http_request.headers.get("X-Forwarded-For")
    ip = (forwarded.split(",")[0].strip() if forwarded
          else (http_request.client.host if http_request.client else None))
    try:
        audit.log_user_action(
            service=AUDIT_SERVICE,
            user_id=str(ctx.get("user_id") or ANONYMOUS_USER_ID),
            action=action,
            message=message,
            username=ctx.get("username"),
            ip_address=ip,
            correlation_id=ctx.get("correlation_id"),
            success=success,
            data_type="analytics_export",
            job_id=job_id,
            user_agent=http_request.headers.get("User-Agent"),
            request_path=str(http_request.url.path),
            **extra,
        )
    except Exception as exc:  # pragma: no cover - audit transport failure
        logger.error("Analytics export audit entry failed (%s, job %s): %s",
                     action, job_id, exc)


async def _load_job(job_id: str) -> Dict[str, Any]:
    job = await analytics_export_service.jobs.get(job_id)
    if not job:
        raise HTTPException(
            status_code=HTTP_NOT_FOUND,
            detail={"code": "ANALYTICS_EXPORT_NOT_FOUND",
                    "message": f"No export job {job_id}", "job_id": job_id},
        )
    return job


def _owns(job: Dict[str, Any], caller: str) -> bool:
    return str(job.get("user_id")) == caller


def _forbidden(job_id: str) -> HTTPException:
    return HTTPException(
        status_code=HTTP_FORBIDDEN,
        detail={"code": "ANALYTICS_EXPORT_FORBIDDEN",
                "message": "This export belongs to another user.",
                "job_id": job_id},
    )


@router.post(
    "/export",
    summary="Export an analytics query",
    response_description="A streamed CSV, a finished workbook, or 202 with a job id",
)
async def start_export(
    request: ExportRequest,
    http_request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
):
    _caller_id(user_context)

    try:
        outcome = await analytics_export_service.export(request, user_context)

    except HTTPException:
        raise

    except BudgetExceeded as exc:
        # Above ANALYTICS_CELLS_MAX queueing only moves the failure to the worker
        # and spends the disk budget getting there.
        raise HTTPException(
            status_code=HTTP_PAYLOAD_TOO_LARGE,
            detail={"code": "ANALYTICS_BUDGET_EXCEEDED", "message": str(exc),
                    "estimate": asdict(exc.estimate), "suggestions": exc.suggestions},
        )

    except ExportStorageExhausted as exc:
        # 507, not 503: the server is fine, its export volume is not, and the
        # message already names the directory, the megabytes and the env var.
        raise HTTPException(
            status_code=HTTP_INSUFFICIENT_STORAGE,
            detail={"code": "ANALYTICS_EXPORT_STORAGE_FULL", "message": str(exc),
                    "used_bytes": exc.used_bytes, "budget_bytes": exc.budget_bytes},
        )

    except planner.UnavailableDatasetError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE,
            detail={"code": "ANALYTICS_DATASET_UNAVAILABLE",
                    "message": str(exc), "datasets": exc.codes},
        )

    except registry.UnknownDatasetError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE,
            detail={"code": "ANALYTICS_UNKNOWN_DATASET",
                    "message": f"Unknown dataset code: {exc.args[0]}"},
        )

    except ExportWriteError as exc:
        # The directory, its permissions or the disk. A 500 is right — nothing the
        # user picked caused it — but it has to say which.
        logger.exception("Analytics export could not be written")
        raise HTTPException(
            status_code=500,
            detail={"code": "ANALYTICS_EXPORT_WRITE_FAILED", "message": str(exc)},
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE,
            detail={"code": "ANALYTICS_INVALID_REQUEST", "message": str(exc)},
        )

    except Exception as exc:
        logger.exception("Analytics export failed")
        raise HTTPException(
            status_code=500,
            detail={"code": "ANALYTICS_EXPORT_FAILED",
                    "message": f"The export could not be produced: {exc}"},
        )

    return _export_response(outcome)


def _export_response(outcome: Dict[str, Any]):
    """Turn one of the service's three outcomes into its HTTP shape."""
    denied = outcome.get("denied_datasets") or []
    headers = {"X-Analytics-Cells": str(outcome.get("estimated_cells") or 0)}
    if denied:
        # A file has no envelope to carry `meta.denied_datasets`, and a dataset
        # dropped for a missing grant must not disappear silently.
        headers["X-Analytics-Denied-Datasets"] = ",".join(denied)

    mode = outcome.get("mode")

    if mode == MODE_STREAM:
        filename = _safe_filename(outcome.get("filename"), "analytics_export.csv")
        headers["Content-Disposition"] = _disposition(filename)
        # The iterator is a generator: rows leave as they are produced, so a
        # 500k-cell CSV costs constant memory and starts arriving immediately.
        return StreamingResponse(outcome["iterator"],
                                 media_type=outcome["media_type"], headers=headers)

    if mode == MODE_INLINE:
        filename = _safe_filename(outcome.get("filename"), "analytics_export.xlsx")
        return FileResponse(
            outcome["path"],
            media_type=outcome["media_type"],
            filename=filename,
            headers=headers,
            # After the file has been sent, never before: deleting the temp
            # directory any earlier serves a truncated download.
            background=BackgroundTask(cleanup_export, outcome),
        )

    return JSONResponse(
        status_code=HTTP_ACCEPTED,
        headers=headers,
        content={"data": {
            "mode": MODE_JOB,
            "job_id": outcome.get("job_id"),
            "status": outcome.get("status"),
            # "you are already in this queue" — not an error, and the UI says so.
            "deduped": bool(outcome.get("deduped")),
            # Diagnostic only: USE_RABBITMQ_JOBS is false here, so False is the
            # ordinary answer and means "the sweep will claim it within a minute".
            "published": bool(outcome.get("published")),
            "chunks_total": outcome.get("chunks_total"),
            "estimated_cells": outcome.get("estimated_cells"),
            "band": outcome.get("band"),
            "denied_datasets": denied,
        }},
    )


@router.get(
    "/exports",
    summary="This user's export jobs",
    response_description="The caller's jobs, newest first",
)
async def list_exports(
    limit: int = Query(DEFAULT_EXPORT_LIST_LIMIT, ge=1, le=MAX_EXPORT_LIST_LIMIT),
    user_context: Dict[str, Any] = Depends(get_user_context),
):
    caller = _caller_id(user_context)
    jobs = await analytics_export_service.jobs.list_for_user(caller, limit)
    return {"data": {"jobs": [_job_view(job) for job in jobs],
                     "count": len(jobs), "limit": limit}}


@router.get(
    "/export/{job_id}",
    summary="Export job status",
    response_description="Status, progress and the filters that would rebuild it",
)
async def export_status(
    job_id: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
):
    caller = _caller_id(user_context)
    job = await _load_job(job_id)
    if not _owns(job, caller):
        # A label names the datasets, the date range and the plant count. Polling
        # someone else's job would leak what they are analysing.
        raise _forbidden(job_id)
    return {"data": _job_view(job)}


@router.get(
    "/export/{job_id}/download",
    summary="Download a finished export",
    response_description="The generated file",
)
async def download_export(
    job_id: str,
    http_request: Request,
    token: str = Query("", description="The job's download_token, from its status"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit: Optional[AuditLogger] = Depends(get_optional_audit_logger),
):
    caller = _caller_id(user_context)
    job = await _load_job(job_id)

    # Ownership first, and independently of the token: this is the authorization
    # boundary of the feature, and a wrong token must never be able to turn a
    # "not yours" into a "bad link".
    if not _owns(job, caller):
        _audit_download(audit, http_request, user_context,
                        action=AUDIT_DOWNLOAD_DENIED, job_id=job_id, success=False,
                        message="User attempted to download another user's analytics "
                                "export",
                        failure_reason="not_owner")
        raise _forbidden(job_id)

    status = job.get("status")
    if status == STATUS_EXPIRED:
        raise HTTPException(
            status_code=HTTP_GONE,
            detail={"code": "ANALYTICS_EXPORT_EXPIRED",
                    "message": "This export has expired and its file was deleted.",
                    "job_id": job_id, "regenerate": _regenerate_hint(job)},
        )
    if status != STATUS_READY:
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={"code": ("ANALYTICS_EXPORT_NOT_READY"
                             if status in ACTIVE_STATUSES
                             else "ANALYTICS_EXPORT_NOT_AVAILABLE"),
                    "message": f"Export {job_id} is {status}, so there is no file "
                               f"to download.",
                    "job_id": job_id, "status": status, "error": job.get("error"),
                    "progress": _progress(job)},
        )

    expected = job.get("download_token")
    if not expected or not secrets.compare_digest(str(expected), str(token or "")):
        _audit_download(audit, http_request, user_context,
                        action=AUDIT_DOWNLOAD_DENIED, job_id=job_id, success=False,
                        message="Analytics export download rejected: bad token",
                        failure_reason="token_invalid")
        raise HTTPException(
            status_code=HTTP_FORBIDDEN,
            detail={"code": "ANALYTICS_EXPORT_TOKEN_INVALID",
                    "message": "This download link is not valid for this export.",
                    "job_id": job_id},
        )

    expires_at = _as_utc(job.get("token_expires_at"))
    if expires_at and expires_at <= datetime.now(timezone.utc):
        raise HTTPException(
            status_code=HTTP_FORBIDDEN,
            detail={"code": "ANALYTICS_EXPORT_TOKEN_EXPIRED",
                    "message": "This download link has expired. Open My Exports to "
                               "get a current one.",
                    "job_id": job_id, "expired_at": _iso(expires_at)},
        )

    result = job.get("result") or {}
    path = result.get("path")
    if not path or not os.path.isfile(path):
        # The reaper deletes the file and the row keeps saying `ready` until the
        # sweep catches up. A FileResponse on a missing path is an unhandled
        # OSError, i.e. a 500 that looks like a broken server.
        logger.warning("Analytics export %s is ready but its file is gone: %r",
                       job_id, path)
        raise HTTPException(
            status_code=HTTP_GONE,
            detail={"code": "ANALYTICS_EXPORT_FILE_MISSING",
                    "message": "The generated file is no longer on disk.",
                    "job_id": job_id, "regenerate": _regenerate_hint(job)},
        )

    fmt = str((job.get("filters") or {}).get("format") or "csv").lower()
    filename = _safe_filename(result.get("filename"),
                              f"analytics_export.{fmt if fmt in MEDIA_TYPES else 'csv'}")

    _audit_download(audit, http_request, user_context,
                    action=AUDIT_DOWNLOAD, job_id=job_id, success=True,
                    message=f"User downloaded analytics export {job_id}",
                    filename=filename, response_format=fmt,
                    bytes=result.get("bytes"), row_count=result.get("row_count"))

    return FileResponse(
        path,
        media_type=MEDIA_TYPES.get(fmt, MEDIA_TYPES["csv"]),
        filename=filename,
        # The file belongs to the job, not to this request: the reaper deletes it
        # at retention, so there is no cleanup task here.
    )


@router.post(
    "/export/{job_id}/cancel",
    summary="Cancel a queued or running export",
    response_description="The job's new status",
)
async def cancel_export(
    job_id: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
):
    caller = _caller_id(user_context)
    job = await _load_job(job_id)
    if not _owns(job, caller):
        raise _forbidden(job_id)

    status = job.get("status")
    if status == STATUS_CANCELLED:
        return {"data": {"job_id": job_id, "status": STATUS_CANCELLED,
                         "changed": False}}

    if status not in ACTIVE_STATUSES:
        # Not a 200. `update-status` on a lapsed bid answers 200 having changed
        # nothing, and the user cannot tell that from success.
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={"code": "ANALYTICS_EXPORT_NOT_CANCELLABLE",
                    "message": f"Export {job_id} is already {status}.",
                    "job_id": job_id, "status": status},
        )

    changed = await analytics_export_service.jobs.mark(job_id, STATUS_CANCELLED)
    if not changed:
        # The write matched nothing: the job finished, or the row went away,
        # between the read above and the update.
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={"code": "ANALYTICS_EXPORT_CANCEL_FAILED",
                    "message": f"Export {job_id} could not be cancelled — it "
                               f"changed state while the request was in flight.",
                    "job_id": job_id},
        )

    logger.info("Analytics export %s cancelled by %s", job_id, caller)
    # The worker checks for this between chunks; a running job stops at the next
    # chunk boundary rather than instantly.
    return {"data": {"job_id": job_id, "status": STATUS_CANCELLED, "changed": True}}


@router.post(
    "/export/{job_id}/retry",
    summary="Re-queue a failed or cancelled export as the same job",
    response_description="The job's new status",
)
async def retry_export(
    job_id: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
):
    """Retry in place rather than regenerating.

    Regenerate is right for an EXPIRED job (its file is gone, so a new job is the
    honest answer). For a FAILED one it leaves the failed row beside its
    replacement and the list grows on every attempt — so a failed job goes back
    to `queued` as itself, and the every-minute sweep picks it up.
    """
    caller = _caller_id(user_context)
    job = await _load_job(job_id)
    if not _owns(job, caller):
        raise _forbidden(job_id)

    status = job.get("status")
    if status not in (STATUS_FAILED, STATUS_CANCELLED):
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={"code": "ANALYTICS_EXPORT_NOT_RETRYABLE",
                    "message": f"Export {job_id} is {status}; only a failed or "
                               f"cancelled export can be retried. Use regenerate "
                               f"for an expired one.",
                    "job_id": job_id, "status": status},
        )

    changed = await analytics_export_service.jobs.requeue(job_id)
    if not changed:
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={"code": "ANALYTICS_EXPORT_RETRY_FAILED",
                    "message": f"Export {job_id} could not be re-queued — it "
                               f"changed state while the request was in flight.",
                    "job_id": job_id},
        )

    # Same doorbell the original enqueue rang; the sweep covers it if the broker
    # is off, exactly as on first submission.
    await analytics_export_service.notify_queued(job_id)

    logger.info("Analytics export %s re-queued by %s", job_id, caller)
    return {"data": {"job_id": job_id, "status": STATUS_QUEUED, "changed": True}}
