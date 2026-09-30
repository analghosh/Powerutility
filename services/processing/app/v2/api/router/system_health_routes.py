"""
System Health API — the data behind the System Health page.

One read-only call returns every service's status and a verdict for every
scheduled job, so the page never has to reach the other services itself.
Authentication is ``AuthMiddleware``'s job, as for every other v2 router; this
route also checks the caller may see the page, because it names every job,
data source and error in the system.
"""

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ...dependencies import get_user_context
from ...services.system_health_service import system_health_service

router = APIRouter(
    prefix="/system-health",
    tags=["System Health"],
)


def _forbidden() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "SYSTEM_HEALTH_FORBIDDEN", "message": "System Health is for administrators."},
    )


@router.get(
    "",
    summary="Service and scheduled-job health",
    response_description=(
        "Overall verdict, each service's status and response time, and each "
        "scheduled job's state with its latest run"
    ),
)
async def get_system_health(
    user_context: Dict[str, Any] = Depends(get_user_context),
) -> Dict[str, Any]:
    if not await system_health_service.can_view(user_context):
        raise _forbidden()
    return await system_health_service.get_system_health()


@router.get(
    "/jobs/{job_type}/runs",
    summary="One scheduled job's run history",
    response_description=(
        "The job's runs newest first, one entry per run, with a summary of the "
        "last day and a cursor for older runs"
    ),
)
async def get_job_runs(
    job_type: str,
    before: Optional[datetime] = Query(None, description="Return runs that started before this time (cursor)"),
    limit: int = Query(50, ge=1, le=200),
    failed_only: bool = Query(False, description="Only runs that failed, partly failed or didn't finish"),
    user_context: Dict[str, Any] = Depends(get_user_context),
) -> Dict[str, Any]:
    if not await system_health_service.can_view(user_context):
        raise _forbidden()
    result = await system_health_service.get_job_runs(job_type, before, limit, failed_only)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SYSTEM_HEALTH_UNKNOWN_JOB", "message": f"No scheduled job '{job_type}'."},
        )
    return result
