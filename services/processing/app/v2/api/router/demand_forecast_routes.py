"""
V2 Demand Forecast API Routes
Cloned from v1 with proper authorization and audit logging
"""
from typing import Dict, Any, Optional
from fastapi import APIRouter, Request, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field

from ...dependencies import get_user_context
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger
from libs.middleware import get_correlation_id
from ....v2.controller.demand_forecast_controller import DemandForecastController
import logging

router = APIRouter(
    prefix="/demand-forecast",
    tags=["Demand Forecast V2"]
)

logger = logging.getLogger(__name__)

# ---------- Response Schemas ----------
class DemandForecastResponse(BaseModel):
    """Response schema for demand forecast APIs"""
    data: Dict[str, Any] = Field(..., description="Payload data")

# ---------- Endpoints ----------

@router.get(
    "/",
    response_model=DemandForecastResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Demand Forecast Data",
    response_description="Demand Forecast data with count and records",
    responses={
        200: {"description": "OK"},
        422: {"description": "Validation error"},
        500: {"description": "Failed to fetch demand forecast data."},
    },
)
async def get_demand_forecast(
    request: Request,
    schedule_date: Optional[str] = Query(
        default=None,
        description="The schedule date to filter demand forecast data. Format: YYYY-MM-DD",
        example="2025-01-15"
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Fetch DAF & IDF demand forecast data.
    
    Behavior:
    - Always return 96 time blocks (1..96) in 'blocks'.
    - If schedule_date is today (IST):
        * For blocks <= current 15-min block: include full data (incl. MAPE_*).
        * For blocks > current block: include only comp_act_demand, comp_udm, forecast_DAF, forecast_IDF;
          set MAPE_* to None.
        * Error analysis (percent bins) is computed only on blocks <= current block.
    - If schedule_date is not today:
        * 'blocks' still lists 1..96; fields are filled when data exists, else None.
        * Error analysis uses only blocks that actually have MAPE values.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="demand_forecast_fetch",
            message="Starting demand forecast data fetch operation",
            correlation_id=correlation_id,
            user_id=user_id,
            schedule_date=schedule_date
        )
        
        controller = DemandForecastController()
        result = await controller.demand_forecast(schedule_date, user_id, username)
        
        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="demand_forecast_view",
            message="User successfully fetched demand forecast data",
            username=username,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            blocks_count=len(result.get("data", {}).get("blocks", [])) if isinstance(result, dict) else 0,
            success=True
        )
        
        logger.info(f"Demand forecast data fetched successfully for user {user_id}, schedule_date: {schedule_date}")
        
        return DemandForecastResponse(data=result)
        
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="DemandForecastDataFetchError",
            error_message=f"Failed to fetch demand forecast data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            error_type=type(e).__name__
        )
        
        logger.exception(f"Error fetching demand forecast data for user {user_id}, schedule_date: {schedule_date}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch demand forecast data."
        )

@router.get(
    "/monthly-average-mape",
    response_model=DemandForecastResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Monthly Average MAPE (DAF & IDF)",
    response_description="Only the monthly average MAPE for DAF and IDF from historical data",
    responses={
        200: {"description": "OK"},
        422: {"description": "Validation error"},
        500: {"description": "Failed to fetch monthly average MAPE data."},
    },
)
async def get_monthly_average_mape(
    request: Request,
    month: Optional[str] = Query(
        default=None,
        description="Month in YYYY-MM format. Defaults to current IST month",
        example="2025-01"
    ),
    zone: Optional[str] = Query(
        default=None,
        description="Zone filter (e.g., 'Puducherry')",
        example="PUDUCHERRY"
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Return only monthly average MAPE for DAF & IDF from historical data.
    If the window includes today, only count rows up to the current time block.
    Optional: filter by zone.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="monthly_average_mape_fetch",
            message="Starting monthly average MAPE data fetch operation",
            correlation_id=correlation_id,
            user_id=user_id,
            month=month,
            zone=zone
        )
        
        controller = DemandForecastController()
        result = await controller.monthly_average_mape(month, zone, user_id, username)
        
        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="monthly_average_mape_view",
            message="User successfully fetched monthly average MAPE data",
            username=username,
            correlation_id=correlation_id,
            month=month,
            zone=zone,
            result_month=result.get("month") if isinstance(result, dict) else None,
            success=True
        )
        
        logger.info(f"Monthly average MAPE data fetched successfully for user {user_id}, month: {month}, zone: {zone}")
        
        return DemandForecastResponse(data=result)
        
    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="MonthlyAverageMAPEDataFetchError",
            error_message=f"Failed to fetch monthly average MAPE data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            month=month,
            zone=zone,
            error_type=type(e).__name__
        )
        
        logger.exception(f"Error fetching monthly average MAPE data for user {user_id}, month: {month}, zone: {zone}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch monthly average MAPE data."
        )
