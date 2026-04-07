"""
Weather V2 Router
Enhanced weather endpoints with JWT authentication and correlation tracking
"""

from fastapi import APIRouter, Query, HTTPException, status, Request, Depends
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from datetime import datetime
import logging

from ...controller.weather_controller import weather_controller
from ...dependencies import get_user_context
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger
from libs.middleware import get_correlation_id

router = APIRouter(
    prefix="/forecast",
    tags=["Weather V2"]
)

logger = logging.getLogger(__name__)

# Pydantic models for a fully dynamic response
class DynamicForecastData(BaseModel):
    generationtime_ms: float
    utc_offset_seconds: int
    timezone: str
    timezone_abbreviation: str
    elevation: float
    hourly_units: Dict[str, str]
    hourly: Dict[str, List[Any]]
    

class DynamicForecastResponse(BaseModel):
    data: DynamicForecastData
    timestamp: str = Field(..., description="Response timestamp in ISO format")
    correlation_id: Optional[str] = Field(None, description="Request correlation ID")


@router.get(
    "/",
    response_model=DynamicForecastResponse,
    summary="Get hourly weather forecast (V2)",
    description="Provides all available hourly weather forecast data for a given city and date range.",
    response_description="Returns structured weather forecast data.",
    responses={
        200: {"description": "Weather forecast data retrieved successfully"},
        401: {"description": "Unauthorized - Invalid or missing JWT token"},
        500: {"description": "Internal Server Error - Failed to fetch weather forecast data"},
    },
)
async def get_weather_forecast(
    request: Request,
    forecast_days: int = Query(1, ge=1, le=16, description="Number of days for the forecast"),
    timezone: str = Query("auto", example="auto", description="Timezone for the forecast"),
    weather_type: str = Query("IBM", alias="type", example="IBM", description="Type of weather data to fetch"),
    city: str = Query(..., example="mahe", description="City key as stored in weather_serve (case-sensitive)"),
    date: Optional[str] = Query(..., description="Date in YYYY-MM-DD format (e.g., 2026-03-22)" ),  
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    **Get Hourly Weather Forecast (V2)**

    This endpoint retrieves all available weather metrics from the database for the specified day(s)
    and formats the response similar to the Open-Meteo API.
    Enhanced with JWT authentication and correlation tracking.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        audit_logger.log_system_operation(
            service="processing",
            operation="weather_forecast_fetch",
            message="Starting weather forecast data fetch operation",
            correlation_id=correlation_id,
            user_id=user_id,
            forecast_days=forecast_days,
            city=city,
        )

        result = await weather_controller.get_forecast(
            forecast_days=forecast_days,
            timezone=timezone,
            weather_type=weather_type,
            city=city,
            date=date
        )

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="weather_forecast_view",
            message="User successfully fetched weather forecast data",
            username=username,
            correlation_id=correlation_id,
            success=True
        )

        logger.info(f"Weather forecast data fetched successfully for user {user_id}")

        return DynamicForecastResponse(
            data=result,
            timestamp=datetime.now().isoformat(),
            correlation_id=correlation_id
        )
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="WeatherForecastFetchError",
            error_message=f"Failed to fetch weather forecast data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        logger.exception(f"Error in weather forecast route for user {user_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch weather forecast data."
        )
