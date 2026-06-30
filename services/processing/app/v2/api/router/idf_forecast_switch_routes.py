"""
IDF Forecast Switch API — toggle RTM API vs CSV merge source.
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from libs.audit_queue.simple_logger import AuditLogger, AuditLogLevel
from libs.helpers.idf_forecast_audit import request_audit_context, switch_change_audit_fields
from libs.middleware import get_correlation_id
from ....core.dependencies import get_audit_logger
from ...controller.idf_forecast_switch_controller import (
    ManualSwitchNotAllowedError,
    idf_forecast_switch_controller,
)
from ...dependencies import get_authenticated_user, get_user_context

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


class MergeResult(BaseModel):
    merged: int
    skipped: int
    failed: int
    status: str = Field(
        ...,
        description="success | partial | error",
    )


class IdfForecastSwitchResponse(BaseModel):
    use_rtm_api: bool
    active_source: str
    para_key: str
    para_value: str
    updated_at: Optional[str] = None
    updated_by: Optional[str] = None
    auto_fallback_message: Optional[str] = Field(
        None,
        description=(
            "Set when the system auto-switched from Manual to Auto "
            "(e.g. active session CSV zero/empty)"
        ),
    )
    merge_result: Optional[MergeResult] = Field(
        None,
        description="Instant demand_serve merge outcome after PUT /switch",
    )
    correlation_id: Optional[str] = None
    timestamp: Optional[str] = None


def _user_id_from_context(user_context: Dict[str, Any]) -> str:
    user = user_context.get("user") or {}
    return str(
        user_context.get("user_id")
        or user.get("id")
        or user.get("user_id")
        or "anonymous"
    )


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
    user_id = _user_id_from_context(user_context)
    username = user_context.get("username") or user_id
    correlation_id = user_context.get("correlation_id") or get_correlation_id()

    try:
        data = await idf_forecast_switch_controller.get_switch()
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="idf_forecast_switch_get",
            message="IDF forecast switch fetched",
            correlation_id=correlation_id,
            session_id=correlation_id,
            username=username,
            success=True,
            **request_audit_context(request),
            use_rtm_api=data.get("use_rtm_api"),
            active_source=data.get("active_source"),
            para_value=data.get("para_value"),
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
    current_user: Dict[str, Any] = Depends(get_authenticated_user),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    user_id = str(current_user.get("id") or current_user.get("user_id") or "unknown")
    username = current_user.get("username") or user_id
    correlation_id = getattr(request.state, "correlation_id", None) or get_correlation_id()

    try:
        data = await idf_forecast_switch_controller.set_switch(
            use_rtm_api=body.use_rtm_api,
            updated_by=user_id,
        )
        previous = {
            "use_rtm_api": data.get("previous_use_rtm_api"),
            "active_source": data.get("previous_active_source"),
            "para_value": data.get("previous_para_value"),
            "para_key": data.get("para_key"),
        }
        new_state = {
            "use_rtm_api": data.get("use_rtm_api"),
            "active_source": data.get("active_source"),
            "para_value": data.get("para_value"),
            "para_key": data.get("para_key"),
        }
        changed = (
            previous.get("use_rtm_api") != new_state.get("use_rtm_api")
            or previous.get("para_value") != new_state.get("para_value")
        )
        merge_result = data.get("merge_result") or {}
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="idf_forecast_switch_update",
            message=(
                "IDF forecast switch changed"
                if changed
                else "IDF forecast switch set (no value change)"
            ),
            correlation_id=correlation_id,
            session_id=correlation_id,
            username=username,
            success=True,
            level=AuditLogLevel.HIGH if changed else AuditLogLevel.MEDIUM,
            change_source="api",
            value_changed=changed,
            requested_use_rtm_api=body.use_rtm_api,
            merge_merged=merge_result.get("merged"),
            merge_skipped=merge_result.get("skipped"),
            merge_failed=merge_result.get("failed"),
            merge_status=merge_result.get("status"),
            **request_audit_context(request),
            **switch_change_audit_fields(previous, new_state),
        )
        response_data = {k: v for k, v in data.items() if not str(k).startswith("previous_")}
        return IdfForecastSwitchResponse(
            **response_data,
            correlation_id=correlation_id,
            timestamp=datetime.now().isoformat(),
        )
    except ManualSwitchNotAllowedError as e:
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="idf_forecast_switch_update_rejected",
            message=str(e.message),
            correlation_id=correlation_id,
            session_id=correlation_id,
            username=username,
            success=False,
            level=AuditLogLevel.MEDIUM,
            change_source="api",
            requested_use_rtm_api=body.use_rtm_api,
            **request_audit_context(request),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=e.message,
        ) from e
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
