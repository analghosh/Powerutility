"""
Forecast Serve Entity
Stores IDF demand forecasts per source (rtm_forecast_api, csv) in forecast_serve collection.
"""

from datetime import datetime
from typing import Optional, Any, Literal
from pydantic import BaseModel, Field, ConfigDict
from bson import ObjectId

FORECAST_SOURCE_RTM_API = "rtm_forecast_api"
FORECAST_SOURCE_CSV = "csv"

ForecastSource = Literal["rtm_forecast_api", "csv"]


class ForecastServeEntity(BaseModel):
    """One forecast document per (scheduled_at, time_block, type, source)."""

    model_config = ConfigDict(
        validate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={
            ObjectId: str,
            datetime: lambda v: v.isoformat(),
        },
    )

    id: Optional[Any] = Field(default_factory=ObjectId, alias="_id")
    scheduled_at: datetime = Field(..., description="Schedule date (midnight)")
    time_block: int = Field(..., description="Time block 1-96")
    type: Literal["IDF"] = Field(default="IDF", description="Market type")
    forecast: Optional[float] = Field(None, description="Forecast demand MW")
    forecasted_frequency: Optional[float] = Field(
        None, description="Forecasted frequency (RTM API source only)"
    )
    source: ForecastSource = Field(..., description="Data source identifier")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


def ensure_forecast_serve_indexes(collection) -> None:
    """Ensure indexes for forecast_serve collection."""
    import logging

    logger = logging.getLogger(__name__)
    try:
        collection.create_index(
            [("type", 1), ("scheduled_at", 1), ("time_block", 1), ("source", 1)],
            unique=True,
            name="idx_unique_forecast_serve_key",
            background=True,
        )
        collection.create_index(
            [("scheduled_at", 1), ("time_block", 1), ("type", 1)],
            name="idx_forecast_serve_block_lookup",
            background=True,
        )
        logger.debug("forecast_serve collection indexes ensured")
    except Exception as e:
        logger.warning("Failed to create forecast_serve indexes: %s", e)
