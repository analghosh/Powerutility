"""
RTM Scheduling Snapshot Router (V2)
Standalone snapshot data/excel endpoints under a separate prefix.
"""

from datetime import datetime, timezone, timedelta
import logging
from typing import Dict, Any, Optional

from fastapi import APIRouter, HTTPException, status, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ...controller.rtm_scheduling_snapshot_controller import rtm_scheduling_snapshot_controller
from ...dependencies import get_user_context
from ...services.rtm_scheduling_snapshot_services import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger

router = APIRouter(
    prefix="/rtm_scheduling_snapshot_v2",
    tags=["RTM Scheduling Snapshot V2"],
)

logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> datetime:
    return datetime.now(IST)


class V2Response(BaseModel):
    data: Any
    timestamp: str
    correlation_id: Optional[str]


@router.get(
    "/rtm_scheduling_snapshot",
    response_model=V2Response,
    status_code=status.HTTP_200_OK,
    summary="RTM scheduling raw snapshot by session/date (V2, standalone)",
)
async def get_rtm_scheduling_snapshot(
    session_id: Optional[str] = Query(None, description="RTM session identifier (optional)"),
    run_id: Optional[str] = Query(None, description="Snapshot run_id (optional; narrows to one run)"),
    schedule_date: str = Query(..., description="Schedule date in YYYY-MM-DD format"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(
        DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description=f"Rows per page (max {MAX_PAGE_SIZE})",
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id")
    try:
        audit_logger.log_system_operation(
            service="processing",
            operation="rtm_scheduling_snapshot_v2_fetch",
            message="Starting standalone RTM scheduling snapshot fetch",
            correlation_id=correlation_id,
            user_id=user_id,
        )
        result = await rtm_scheduling_snapshot_controller.get_rtm_scheduling_snapshot_data(
            session_id=session_id,
            schedule_date=schedule_date,
            page=page,
            page_size=page_size,
            run_id=run_id,
        )
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="rtm_scheduling_snapshot_v2_view",
            message="Successfully fetched standalone RTM scheduling snapshot data",
            username=username,
            correlation_id=correlation_id,
            success=True,
        )
        return V2Response(data=result, timestamp=now_ist().isoformat(), correlation_id=correlation_id)
    except Exception as e:
        logger.exception("Error fetching standalone RTM scheduling snapshot for user %s", user_id)
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMSchedulingSnapshotV2FetchError",
            error_message=f"Failed to fetch standalone RTM scheduling snapshot: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__,
        )
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch standalone RTM scheduling snapshot data.",
        )


@router.get(
    "/rtm_scheduling_snapshot/excel",
    status_code=status.HTTP_200_OK,
    summary="Download RTM scheduling snapshot excel (V2, standalone)",
)
async def download_rtm_scheduling_snapshot_excel(
    session_id: Optional[str] = Query(None, description="RTM session identifier (required unless run_id is set)"),
    run_id: Optional[str] = Query(None, description="Snapshot run_id (required unless session_id is set)"),
    schedule_date: str = Query(..., description="Schedule date in YYYY-MM-DD format"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id")
    try:
        audit_logger.log_system_operation(
            service="processing",
            operation="rtm_scheduling_snapshot_v2_excel_export",
            message="Starting standalone RTM scheduling snapshot excel export",
            correlation_id=correlation_id,
            user_id=user_id,
        )
        export_data = await rtm_scheduling_snapshot_controller.get_rtm_scheduling_snapshot_excel(
            session_id=session_id,
            schedule_date=schedule_date,
            run_id=run_id,
        )
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="rtm_scheduling_snapshot_v2_excel_download",
            message="Successfully exported standalone RTM scheduling snapshot excel",
            username=username,
            correlation_id=correlation_id,
            success=True,
        )
        return StreamingResponse(
            iter([export_data["file_bytes"]]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=\"{export_data['file_name']}\""},
        )
    except Exception as e:
        logger.exception("Error exporting standalone RTM scheduling snapshot excel for user %s", user_id)
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMSchedulingSnapshotV2ExcelExportError",
            error_message=f"Failed to export standalone RTM scheduling snapshot excel: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__,
        )
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to export standalone RTM scheduling snapshot excel.",
        )
