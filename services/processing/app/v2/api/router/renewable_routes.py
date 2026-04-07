"""
Renewable V2 Router
Endpoints for renewable energy data with JWT authentication and correlation tracking
"""

from fastapi import APIRouter, Request, HTTPException, status, Depends, Query
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from datetime import datetime
import logging

from ...controller.renewable_controller import renewable_controller
from ...dependencies import get_user_context
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger
from libs.middleware import get_correlation_id

router = APIRouter(
    prefix="/renewable",
    tags=["Renewable V2"]
)

logger = logging.getLogger(__name__)


class RenewableResponse(BaseModel):
    """
    Response schema for Renewable V2 APIs.
    """
    data: Dict[str, Any] = Field(..., description="Renewable data payload")
    timestamp: str = Field(..., description="Response timestamp in ISO format")
    correlation_id: Optional[str] = Field(None, description="Request correlation ID")


@router.get(
    "/schedule",
    response_model=RenewableResponse,
    status_code=status.HTTP_200_OK,
    summary="Renewable Schedule Data (V2)",
    description="""
    Fetches **renewable schedule data** for a specific date.
    
    Returns grouped SCHEDULE for a day (both technologies: solar & wind; both types: IDF & DAF).
    
    **Response Structure:**
    ```json
    {
        "data": [
            {
                "schedule_date": "2025-10-21",
                "technology": "solar|wind",
                "type": "IDF|DAF",
                "data": [
                    {"time_block": 1, "scheduled_volume": 150.5},
                    {"time_block": 2, "scheduled_volume": 200.0},
                    ...
                ]
            }
        ]
    }
    ```
    
    **Authentication:** Requires valid JWT token.
    **Audit:** All requests are logged with user context and correlation ID.
    """,
    response_description="Returns structured renewable schedule data for the specified date.",
)
async def get_renewable_schedule(
    request: Request,
    date: Optional[str] = Query(
        None,
        description="YYYY-MM-DD (IST). Defaults to today's date in Asia/Kolkata timezone.",
        example="2025-10-21",
        regex=r"^\d{4}-\d{2}-\d{2}$"
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Get renewable schedule data for a specific date.
    
    This endpoint provides renewable energy schedule data including:
    - Solar and Wind technologies
    - IDF (Intra-Day Forecast) and DAF (Day-Ahead Forecast) types
    - 96 time blocks per day (15-minute intervals)
    - Scheduled volume data for each time block
    
    The data is sourced from the renewable_serve collection with a 4-day rolling window.
    """
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="renewable_service",
            operation="fetch_renewable_schedule",
            message=f"User {user_id} requested renewable schedule for date: {date}",
            correlation_id=correlation_id,
            user_id=user_id,
            endpoint="/v2/renewable/schedule",
            method="GET",
            success=True
        )
        
        logger.info(f"Fetching renewable schedule data for date: {date}, user: {user_id}, correlation_id: {correlation_id}")
        
        # Call controller to get data
        result = await renewable_controller.get_renewable_schedule(date)
        
        # Log successful user action
        audit_logger.log_user_action(
            service="renewable_service",
            user_id=user_id,
            action="fetch_renewable_schedule",
            message=f"Successfully fetched renewable schedule data for date: {date}",
            correlation_id=correlation_id,
            endpoint="/v2/renewable/schedule",
            success=True,
            response_size=len(str(result)) if result else 0
        )
        
        logger.info(f"Successfully fetched renewable schedule data for user: {user_id}, date: {date}")
        
        return RenewableResponse(
            data=result,
            timestamp=datetime.now().isoformat(),
            correlation_id=correlation_id
        )
        
    except HTTPException as he:
        # Log HTTP exceptions (business logic errors)
        audit_logger.log_user_action(
            service="renewable_service",
            user_id=user_id,
            action="fetch_renewable_schedule_failed",
            message=f"HTTP error fetching renewable schedule: {he.detail}",
            correlation_id=correlation_id,
            endpoint="/v2/renewable/schedule",
            success=False,
            error_code=he.status_code,
            error_message=he.detail
        )
        
        logger.warning(f"HTTP error for user {user_id}, date: {date}, error: {he.detail}")
        raise he
        
    except Exception as e:
        # Log unexpected errors
        audit_logger.log_user_action(
            service="renewable_service",
            user_id=user_id,
            action="fetch_renewable_schedule_error",
            message=f"Unexpected error fetching renewable schedule: {str(e)}",
            correlation_id=correlation_id,
            endpoint="/v2/renewable/schedule",
            success=False,
            exception_type=type(e).__name__,
            error_message=str(e)
        )
        
        logger.exception(f"Unexpected error fetching renewable schedule for user {user_id}, date: {date}")
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch renewable schedule data. Please try again later."
        )


@router.get(
    "/availability",
    response_model=RenewableResponse,
    status_code=status.HTTP_200_OK,
    summary="Renewable Availability Data (V2)",
    description="""
    Fetches **renewable availability data** for a specific date.
    
    Returns a single day's (96 blocks) renewable availability data.
    
    **Response Structure:**
    ```json
    {
        "data": {
            "schedule_date": "2025-10-21",
            "availability_data": [
                {"time_block": 1, "available_capacity": 500.0},
                {"time_block": 2, "available_capacity": 600.0},
                ...
            ]
        }
    }
    ```
    
    **Authentication:** Requires valid JWT token.
    **Audit:** All requests are logged with user context and correlation ID.
    """,
    response_description="Returns renewable availability data for the specified date.",
)
async def get_renewable_availability(
    request: Request,
    date: Optional[str] = Query(
        None,
        description="YYYY-MM-DD (IST). Defaults to today's date in Asia/Kolkata timezone.",
        example="2025-10-21",
        regex=r"^\d{4}-\d{2}-\d{2}$"
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Get renewable availability data for a specific date.
    
    This endpoint provides renewable energy availability data including:
    - Available capacity for each time block
    - 96 time blocks per day (15-minute intervals)
    - Real-time availability status
    
    The data is sourced from the renewable_serve collection with a 4-day rolling window.
    """
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="renewable_service",
            operation="fetch_renewable_availability",
            message=f"User {user_id} requested renewable availability for date: {date}",
            correlation_id=correlation_id,
            user_id=user_id,
            endpoint="/v2/renewable/availability",
            method="GET",
            success=True
        )
        
        logger.info(f"Fetching renewable availability data for date: {date}, user: {user_id}, correlation_id: {correlation_id}")
        
        # Call controller to get data
        result = await renewable_controller.get_renewable_availability(date)
        
        # Log successful user action
        audit_logger.log_user_action(
            service="renewable_service",
            user_id=user_id,
            action="fetch_renewable_availability",
            message=f"Successfully fetched renewable availability data for date: {date}",
            correlation_id=correlation_id,
            endpoint="/v2/renewable/availability",
            success=True,
            response_size=len(str(result)) if result else 0
        )
        
        logger.info(f"Successfully fetched renewable availability data for user: {user_id}, date: {date}")
        
        return RenewableResponse(
            data=result,
            timestamp=datetime.now().isoformat(),
            correlation_id=correlation_id
        )
        
    except HTTPException as he:
        # Log HTTP exceptions (business logic errors)
        audit_logger.log_user_action(
            service="renewable_service",
            user_id=user_id,
            action="fetch_renewable_availability_failed",
            message=f"HTTP error fetching renewable availability: {he.detail}",
            correlation_id=correlation_id,
            endpoint="/v2/renewable/availability",
            success=False,
            error_code=he.status_code,
            error_message=he.detail
        )
        
        logger.warning(f"HTTP error for user {user_id}, date: {date}, error: {he.detail}")
        raise he
        
    except Exception as e:
        # Log unexpected errors
        audit_logger.log_user_action(
            service="renewable_service",
            user_id=user_id,
            action="fetch_renewable_availability_error",
            message=f"Unexpected error fetching renewable availability: {str(e)}",
            correlation_id=correlation_id,
            endpoint="/v2/renewable/availability",
            success=False,
            exception_type=type(e).__name__,
            error_message=str(e)
        )
        
        logger.exception(f"Unexpected error fetching renewable availability for user {user_id}, date: {date}")
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch renewable availability data. Please try again later."
        )


@router.get(
    "/renewable-schedule",
    response_model=RenewableResponse,
    status_code=status.HTTP_200_OK,
    summary="Renewable Schedule Data (V2) - V1 Compatible",
    description="""
    Fetches **renewable schedule data** for a specific date.
    
    **This endpoint matches the v1 `/renewable-schedule` path for backward compatibility.**
    
    Returns grouped SCHEDULE for a day (both technologies: solar & wind; both types: IDF & DAF).
    
    **Response Structure:**
    ```json
    {
        "data": [
            {
                "schedule_date": "2025-10-21",
                "technology": "solar|wind",
                "type": "IDF|DAF",
                "data": [
                    {"time_block": 1, "scheduled_volume": 150.5},
                    {"time_block": 2, "scheduled_volume": 200.0},
                    ...
                ]
            }
        ]
    }
    ```
    
    **Authentication:** Requires valid JWT token.
    **Audit:** All requests are logged with user context and correlation ID.
    """,
    response_description="Returns structured renewable schedule data for the specified date.",
)
async def get_renewable_schedule_v1_compatible(
    request: Request,
    date: Optional[str] = Query(
        None,
        description="YYYY-MM-DD (IST). Defaults to today's date in Asia/Kolkata timezone.",
        example="2025-10-21",
        regex=r"^\d{4}-\d{2}-\d{2}$"
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Get renewable schedule data for a specific date - V1 compatible endpoint.
    
    This endpoint provides the same functionality as `/schedule` but with the v1 path
    `/renewable-schedule` for backward compatibility.
    
    Returns renewable energy schedule data including:
    - Solar and Wind technologies
    - IDF (Intra-Day Forecast) and DAF (Day-Ahead Forecast) types
    - 96 time blocks per day (15-minute intervals)
    - Scheduled volume data for each time block
    
    The data is sourced from the renewable_serve collection with a 4-day rolling window.
    """
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation start
        audit_logger.log_system_operation(
            service="renewable_service",
            operation="fetch_renewable_schedule_v1_compat",
            message=f"User {user_id} requested renewable schedule (v1 compat) for date: {date}",
            correlation_id=correlation_id,
            user_id=user_id,
            endpoint="/v2/renewable/renewable-schedule",
            method="GET",
            success=True
        )
        
        logger.info(f"Fetching renewable schedule data (v1 compat) for date: {date}, user: {user_id}, correlation_id: {correlation_id}")
        
        # Call controller to get data (same as /schedule endpoint)
        result = await renewable_controller.get_renewable_schedule(date)
        
        # Log successful user action
        audit_logger.log_user_action(
            service="renewable_service",
            user_id=user_id,
            action="fetch_renewable_schedule_v1_compat",
            message=f"Successfully fetched renewable schedule data (v1 compat) for date: {date}",
            correlation_id=correlation_id,
            endpoint="/v2/renewable/renewable-schedule",
            success=True,
            response_size=len(str(result)) if result else 0
        )
        
        logger.info(f"Successfully fetched renewable schedule data (v1 compat) for user: {user_id}, date: {date}")
        
        return RenewableResponse(
            data=result,
            timestamp=datetime.now().isoformat(),
            correlation_id=correlation_id
        )
        
    except HTTPException as he:
        # Log HTTP exceptions (business logic errors)
        audit_logger.log_user_action(
            service="renewable_service",
            user_id=user_id,
            action="fetch_renewable_schedule_v1_compat_failed",
            message=f"HTTP error fetching renewable schedule (v1 compat): {he.detail}",
            correlation_id=correlation_id,
            endpoint="/v2/renewable/renewable-schedule",
            success=False,
            error_code=he.status_code,
            error_message=he.detail
        )
        
        logger.warning(f"HTTP error for user {user_id}, date: {date}, error: {he.detail}")
        raise he
        
    except Exception as e:
        # Log unexpected errors
        audit_logger.log_user_action(
            service="renewable_service",
            user_id=user_id,
            action="fetch_renewable_schedule_v1_compat_error",
            message=f"Unexpected error fetching renewable schedule (v1 compat): {str(e)}",
            correlation_id=correlation_id,
            endpoint="/v2/renewable/renewable-schedule",
            success=False,
            exception_type=type(e).__name__,
            error_message=str(e)
        )
        
        logger.exception(f"Unexpected error fetching renewable schedule (v1 compat) for user {user_id}, date: {date}")
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch renewable schedule data. Please try again later."
        )