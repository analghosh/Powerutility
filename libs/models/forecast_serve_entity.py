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

FORECAST_SOURCE_DISPLAY_AUTO = "Auto"
FORECAST_SOURCE_DISPLAY_MANUAL = "Manual"


def map_forecast_source_display(source_forecast: Optional[str]) -> Optional[str]:
    """Map demand_serve.source_forecast to API forecast_source label."""
    if source_forecast == FORECAST_SOURCE_RTM_API:
        return FORECAST_SOURCE_DISPLAY_AUTO
    if source_forecast == FORECAST_SOURCE_CSV:
        return FORECAST_SOURCE_DISPLAY_MANUAL
    return None


class ForecastServeEntity(BaseModel):
    """One forecast document per (scheduled_at, time_block, type, source, forecast_revision)."""

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
    forecast_revision: Optional[int] = Field(
        None,
        description="Per delivery-day ingest revision (1 on first API/CSV batch, then increments)",
    )
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class ForecastServeHistoricalEntity(ForecastServeEntity):
    """Archived copy of a forecast_serve row (all fields including forecast_revision)."""

    archived_at: datetime = Field(default_factory=datetime.now)
    archived_timestamp: datetime = Field(
        default_factory=datetime.now,
        description="Time-series index field for historical collection",
    )


def ensure_forecast_serve_indexes(collection) -> None:
    """Ensure indexes for forecast_serve collection (append-only per revision)."""
    import logging

    logger = logging.getLogger(__name__)
    try:
        for old_name in ("idx_unique_forecast_serve_key",):
            try:
                collection.drop_index(old_name)
            except Exception:
                pass
        collection.create_index(
            [
                ("type", 1),
                ("scheduled_at", 1),
                ("time_block", 1),
                ("source", 1),
                ("forecast_revision", 1),
            ],
            unique=True,
            name="idx_unique_forecast_serve_revision_key",
            background=True,
        )
        collection.create_index(
            [("scheduled_at", 1), ("time_block", 1), ("type", 1)],
            name="idx_forecast_serve_block_lookup",
            background=True,
        )
        collection.create_index(
            [("type", 1), ("scheduled_at", 1), ("source", 1), ("forecast_revision", -1)],
            name="idx_forecast_serve_day_source_revision",
            background=True,
        )
        logger.debug("forecast_serve collection indexes ensured")
    except Exception as e:
        logger.warning("Failed to create forecast_serve indexes: %s", e)


def ensure_forecast_serve_historical_indexes(collection) -> None:
    """Ensure indexes for forecast_serve_historical collection."""
    import logging

    logger = logging.getLogger(__name__)
    try:
        collection.create_index([("scheduled_at", 1)], background=True)
        collection.create_index([("archived_at", -1)], background=True)
        collection.create_index([("archived_timestamp", -1)], background=True)
        collection.create_index("source", background=True)
        collection.create_index(
            [("scheduled_at", 1), ("type", 1), ("source", 1)],
            background=True,
        )
        collection.create_index("time_block", background=True)
        collection.create_index(
            [
                ("type", 1),
                ("scheduled_at", 1),
                ("time_block", 1),
                ("source", 1),
                ("forecast_revision", 1),
            ],
            unique=True,
            name="idx_unique_forecast_serve_historical_revision_key",
            background=True,
        )
        collection.create_index(
            [("scheduled_at", 1), ("source", 1), ("forecast_revision", -1)],
            name="idx_forecast_serve_historical_day_source_revision",
            background=True,
        )
        logger.debug("forecast_serve_historical collection indexes ensured")
    except Exception as e:
        logger.warning("Failed to create forecast_serve_historical indexes: %s", e)
