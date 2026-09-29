"""
Reports Shift Status API — PTC's shift sheet: forecasts, SRLDC net schedule, drawal,
deviation, bids and trades per block, with the shift desk's remarks.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger
from ...dependencies import get_user_context
from ...helpers import shift_status_xlsx
from ...services.reports_shift_status_service import (
    FULL_DAY_SHIFT_ID,
    RemarkLocked,
    ShiftNotFound,
    reports_shift_status_service,
)

router = APIRouter(
    prefix="/reports-shift-status",
    tags=["Reports Shift Status"],
)

logger = logging.getLogger(__name__)

_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ShiftStatusRemarksRequest(BaseModel):
    remarks: List[Dict[str, Any]] = Field(
        ...,
        description="Blocks to submit: {date: YYYY-MM-DD, time_block: 1-96, remark}. "
                    "A submitted remark is final; dates are limited to today, "
                    "yesterday and the day before (IST).",
    )


def _audit(audit_logger: AuditLogger, request: Request, user_context: Dict[str, Any], *,
           action: str, success: bool, message: str, **data: Any) -> None:
    forwarded = request.headers.get("X-Forwarded-For")
    ip = forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else None)
    audit_logger.log_user_action(
        service="processing",
        user_id=user_context.get("user_id", "anonymous"),
        username=user_context.get("username", "anonymous"),
        action=action,
        message=message,
        success=success,
        ip_address=ip,
        correlation_id=user_context.get("correlation_id", "unknown"),
        data_type="reports_shift_status",
        request_path=str(request.url.path),
        request_method=request.method,
        **data,
    )


async def _report(from_date: str, to_date: str, shift_id: Optional[str]) -> dict:
    try:
        return await reports_shift_status_service.get_report(from_date, to_date, shift_id)
    except ShiftNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


def _query_params(
    from_date: str = Query(..., regex=_DATE_PATTERN, description="First date (YYYY-MM-DD)",
                           example="2026-09-28"),
    to_date: str = Query(..., regex=_DATE_PATTERN, description="Last date, inclusive",
                         example="2026-09-28"),
    shift_id: str = Query(FULL_DAY_SHIFT_ID,
                          description="Shift id from Masters > Parameters, or full_day"),
) -> Dict[str, str]:
    return {"from_date": from_date, "to_date": to_date, "shift_id": shift_id}


@router.get(
    "/",
    response_model=None,
    status_code=status.HTTP_200_OK,
    summary="Shift status per date and block",
    responses={404: {"description": "Unknown shift"}, 422: {"description": "Bad range"}},
)
async def get_shift_status(
    request: Request,
    params: Dict[str, str] = Depends(_query_params),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    One section per date — the date the chosen shift starts — with one row per block
    of the shift. A shift that runs past midnight carries the next day's blocks, and
    every row states its own date.

    **Range limit:** 31 days.
    """
    try:
        report = await _report(**params)
    except HTTPException as exc:
        _audit(audit_logger, request, user_context, action="reports_shift_status_view",
               success=False, message="Shift status report request refused",
               failure_reason=str(exc.detail), http_status=exc.status_code, **params)
        raise
    except Exception as exc:
        logger.exception("Shift status report failed for %s", params)
        _audit(audit_logger, request, user_context, action="reports_shift_status_view",
               success=False, message="Shift status report request failed",
               failure_reason=str(exc), http_status=500, **params)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch shift status report data.") from exc

    _audit(audit_logger, request, user_context, action="reports_shift_status_view",
           success=True, message="User fetched shift status report",
           section_count=len(report["sections"]), **params)
    return report


@router.get(
    "/download",
    response_model=None,
    status_code=status.HTTP_200_OK,
    summary="Shift status report as an Excel file in the shift sheet's layout",
    responses={200: {"content": {XLSX_MEDIA_TYPE: {}}}},
)
async def download_shift_status(
    request: Request,
    params: Dict[str, str] = Depends(_query_params),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """The same sections as the screen, one under the other on a single sheet."""
    try:
        report = await _report(**params)
        content = shift_status_xlsx.build_workbook(report)
    except HTTPException as exc:
        _audit(audit_logger, request, user_context, action="reports_shift_status_download",
               success=False, message="Shift status download refused",
               failure_reason=str(exc.detail), http_status=exc.status_code, **params)
        raise
    except Exception as exc:
        logger.exception("Shift status download failed for %s", params)
        _audit(audit_logger, request, user_context, action="reports_shift_status_download",
               success=False, message="Shift status download failed",
               failure_reason=str(exc), http_status=500, **params)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to build the shift status download.") from exc

    filename = shift_status_xlsx.filename_for(
        params["from_date"], params["to_date"], report["shift"].get("shift_name"))
    _audit(audit_logger, request, user_context, action="reports_shift_status_download",
           success=True, message="User downloaded shift status report",
           section_count=len(report["sections"]), **params)
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.put(
    "/remarks",
    response_model=None,
    status_code=status.HTTP_200_OK,
    summary="Submit the shift desk's remarks for a set of blocks",
    responses={401: {"description": "Not signed in"},
               409: {"description": "A block already has a remark"},
               422: {"description": "Bad remark or date outside the window"}},
)
async def save_shift_status_remarks(
    request: Request,
    body: ShiftStatusRemarksRequest,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Write each block's remark once. A submitted remark cannot be changed or cleared,
    and blocks can only be remarked for today, yesterday and the day before (IST).
    Nothing is written if any block in the request already has a remark.
    """
    user_id = user_context.get("user_id")
    if not user_id or user_id == "anonymous":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Sign in to save remarks.")
    try:
        result = await reports_shift_status_service.save_remarks(
            body.remarks, user_id=str(user_id),
            username=user_context.get("username") or str(user_id))
    except RemarkLocked as exc:
        _audit(audit_logger, request, user_context, action="reports_shift_status_remarks",
               success=False, message="Shift status remarks refused: already submitted",
               failure_reason=str(exc), http_status=409)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "locked": exc.blocks}) from exc
    except ValueError as exc:
        _audit(audit_logger, request, user_context, action="reports_shift_status_remarks",
               success=False, message="Shift status remarks refused",
               failure_reason=str(exc), http_status=422)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Saving shift status remarks failed")
        _audit(audit_logger, request, user_context, action="reports_shift_status_remarks",
               success=False, message="Shift status remarks failed",
               failure_reason=str(exc), http_status=500)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save remarks.") from exc

    _audit(audit_logger, request, user_context, action="reports_shift_status_remarks",
           success=True, message="User submitted shift status remarks",
           remark_blocks=[f"{r.get('date')}#{r.get('time_block')}" for r in body.remarks],
           **result)
    return result
