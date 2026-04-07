# In /routes/weather_routes.py

from fastapi import APIRouter, Query, HTTPException, status
from typing import Dict, Any, List
from pydantic import BaseModel
import logging

from ....controller.weather_controller import weather_controller

router = APIRouter(
    prefix="/forecast",
    tags=["Weather"]
)

logger = logging.getLogger(__name__)

# Pydantic models updated for a fully dynamic response
class DynamicForecastResponse(BaseModel):
    latitude: float
    longitude: float
    generationtime_ms: float
    utc_offset_seconds: int
    timezone: str
    timezone_abbreviation: str
    elevation: float
    hourly_units: Dict[str, str]
    hourly: Dict[str, List[Any]]


@router.get(
    "/",
    response_model=DynamicForecastResponse,
    summary="Get hourly weather forecast",
    description="Provides all available hourly weather forecast data for a given location and date range."
)
async def get_weather_forecast(
    latitude: float = Query(..., example=10.92, description="Latitude for the forecast"),
    longitude: float = Query(..., example=79.83, description="Longitude for the forecast"),
    forecast_days: int = Query(1, ge=1, le=16, description="Number of days for the forecast"),
    timezone: str = Query("auto", example="auto", description="Timezone for the forecast"),
    type: str = Query("IBM", example="IBM", description="Type of weather data to fetch"),
):
    """
    **Get Hourly Weather Forecast**

    This endpoint retrieves all available weather metrics from the database for the specified day(s)
    and formats the response similar to the Open-Meteo API.
    """
    try:
        result = weather_controller.get_forecast(
            latitude=latitude,
            longitude=longitude,
            forecast_days=forecast_days,
            timezone=timezone,
            type=type
        )
        return result
    except Exception:
        logger.exception("Error in weather forecast route")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch weather forecast data."
        )