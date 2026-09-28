"""
System Health API — the data behind the System Health page.

One read-only call returns every service's status and a verdict for every
scheduled job, so the page never has to reach the other services itself.
Authentication is ``AuthMiddleware``'s job, as for every other v2 router; this
route also checks the caller may see the page, because it names every job,
data source and error in the system.
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status

from ...dependencies import get_user_context
from ...services.system_health_service import system_health_service

router = APIRouter(
    prefix="/system-health",
    tags=["System Health"],
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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "SYSTEM_HEALTH_FORBIDDEN",
                "message": "System Health is for administrators.",
            },
        )
    return await system_health_service.get_system_health()
