"""
System Health API — the data behind the System Health page.

One read-only call returns every service's status and a verdict for every
scheduled job, so the page never has to reach the other services itself.
Authentication is ``AuthMiddleware``'s job, as for every other v2 router.
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends

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
    return await system_health_service.get_system_health()
