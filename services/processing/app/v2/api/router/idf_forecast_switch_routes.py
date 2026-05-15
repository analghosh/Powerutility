"""
IDF Forecast Switch API — toggle RTM API vs CSV merge source.
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from libs.audit_queue.simple_logger import AuditLogger
from libs.middleware import get_correlation_id
from ....core.dependencies import get_audit_logger
from ...controller.idf_forecast_switch_controller import idf_forecast_switch_controller
from ...dependencies import get_user_context

router = APIRouter(
    prefix="/idf-forecast",
    tags=["IDF Forecast"],
)

logger = logging.getLogger(__name__)


class IdfForecastSwitchUpdate(BaseModel):
    use_rtm_api: bool = Field(
        ...,
        description="True: merge from rtm_forecast_api; False: merge from csv",
    )


class IdfForecastSwitchResponse(BaseModel):
    use_rtm_api: bool
    active_source: str
    para_key: str
    para_value: str
    updated_at: Optional[str] = None
    updated_by: Optional[str] = None
    correlation_id: Optional[str] = None
    timestamp: Optional[str] = None


@router.get(
    "/switch",
    response_model=IdfForecastSwitchResponse,
    status_code=status.HTTP_200_OK,
    summary="Get IDF forecast source switch",
)
async def get_idf_forecast_switch(
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id") or get_correlation_id()

    try:
        data = await idf_forecast_switch_controller.get_switch()
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="idf_forecast_switch_get",
            message="IDF forecast switch fetched",
            correlation_id=correlation_id,
            success=True,
        )
        return IdfForecastSwitchResponse(
            **data,
            correlation_id=correlation_id,
            timestamp=datetime.now().isoformat(),
        )
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="IdfForecastSwitchGetError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
        )
        logger.exception("Error fetching IDF forecast switch")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch IDF forecast switch",
        ) from e


@router.put(
    "/switch",
    response_model=IdfForecastSwitchResponse,
    status_code=status.HTTP_200_OK,
    summary="Set IDF forecast source switch",
)
async def put_idf_forecast_switch(
    request: Request,
    body: IdfForecastSwitchUpdate,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id") or get_correlation_id()

    try:
        data = await idf_forecast_switch_controller.set_switch(
            use_rtm_api=body.use_rtm_api,
            updated_by=user_id,
        )
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="idf_forecast_switch_update",
            message=f"IDF forecast switch set use_rtm_api={body.use_rtm_api}",
            correlation_id=correlation_id,
            use_rtm_api=body.use_rtm_api,
            success=True,
        )
        return IdfForecastSwitchResponse(
            **data,
            correlation_id=correlation_id,
            timestamp=datetime.now().isoformat(),
        )
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="IdfForecastSwitchUpdateError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
        )
        logger.exception("Error updating IDF forecast switch")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update IDF forecast switch",
        ) from e
