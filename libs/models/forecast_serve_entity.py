"""
Forecast Serve Entity
Stores demand forecasts per source in forecast_serve collection.
IDF: rtm_forecast_api, csv — DAF: dam_forecasted_api.
"""

from datetime import datetime
from typing import Optional, Any, Literal, Dict
from pydantic import BaseModel, Field, ConfigDict
from bson import ObjectId

FORECAST_SOURCE_RTM_API = "rtm_forecast_api"
FORECAST_SOURCE_CSV = "csv"
FORECAST_SOURCE_DAM_API = "dam_forecasted_api"

ForecastSource = Literal["rtm_forecast_api", "csv", "dam_forecasted_api"]

FORECAST_SOURCE_DISPLAY_AUTO = "Auto"
FORECAST_SOURCE_DISPLAY_MANUAL = "Manual"

IDF_TYPE = "IDF"
DAF_TYPE = "DAF"
IDF_FINAL_TYPE = "IDF_final"

ForecastMarketType = Literal["IDF", "DAF", "IDF_final"]


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
    type: ForecastMarketType = Field(default="IDF", description="Market type (IDF/DAF/IDF_final)")
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


class IdfFinalForecastEntity(BaseModel):
    """
    Frozen / reportable IDF forecast — one document per (scheduled_at, time_block).
    Populated from latest rtm_forecast_api; blocks before active session are frozen.
    """

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
    type: Literal["IDF_final"] = Field(default=IDF_FINAL_TYPE)
    forecast: Optional[float] = Field(None, description="Forecast demand MW")
    forecasted_frequency: Optional[float] = Field(None)
    source: Literal["rtm_forecast_api"] = Field(default=FORECAST_SOURCE_RTM_API)
    forecast_revision: Optional[int] = Field(
        None, description="rtm_forecast_api revision at last write"
    )
    info: Dict[str, Any] = Field(
        default_factory=dict,
        description="Freeze metadata: frozen, source_forecast_revision, active_session_id_at_write",
    )
    frozen_at: Optional[datetime] = Field(None, description="When block was frozen")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class ForecastServeHistoricalEntity(ForecastServeEntity):
    """Archived copy of a forecast_serve row (all fields including forecast_revision)."""

    archived_at: datetime = Field(default_factory=datetime.now)
    archived_timestamp: datetime = Field(
        default_factory=datetime.now,
        description="Time-series index field for historical collection",
    )


async def ensure_forecast_serve_indexes(collection) -> None:
    """Ensure indexes for forecast_serve collection (append-only per revision)."""
    import logging

    logger = logging.getLogger(__name__)
    try:
        for old_name in ("idx_unique_forecast_serve_key",):
            try:
                await collection.drop_index(old_name)
            except Exception:
                pass
        await collection.create_index(
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
        await collection.create_index(
            [("scheduled_at", 1), ("time_block", 1), ("type", 1)],
            name="idx_forecast_serve_block_lookup",
            background=True,
        )
        await collection.create_index(
            [("type", 1), ("scheduled_at", 1), ("source", 1), ("forecast_revision", -1)],
            name="idx_forecast_serve_day_source_revision",
            background=True,
        )
        await collection.create_index(
            [("type", 1), ("scheduled_at", 1), ("time_block", 1)],
            unique=True,
            partialFilterExpression={"type": IDF_FINAL_TYPE},
            name="idx_unique_idf_final_block",
            background=True,
        )
        logger.info("forecast_serve collection indexes ensured")
    except Exception as e:
        logger.warning("Failed to create forecast_serve indexes: %s", e)


async def ensure_forecast_serve_historical_indexes(collection) -> None:
    """
    Ensure indexes for forecast_serve_historical (time-series collection).

    MongoDB time-series collections do not support unique indexes; revision dedup
    is handled in archive_old_forecast_data() (same pattern as demand_serve_historical).
    """
    import logging

    logger = logging.getLogger(__name__)

    index_specs = [
        ([("scheduled_at", 1)], {}),
        ([("archived_at", -1)], {}),
        ([("archived_timestamp", -1)], {}),
        ("source", {}),
        ([("scheduled_at", 1), ("type", 1), ("source", 1)], {}),
        ("time_block", {}),
        (
            [
                ("type", 1),
                ("scheduled_at", 1),
                ("time_block", 1),
                ("source", 1),
                ("forecast_revision", 1),
            ],
            {"name": "idx_forecast_serve_historical_revision_key"},
        ),
        (
            [("scheduled_at", 1), ("source", 1), ("forecast_revision", -1)],
            {"name": "idx_forecast_serve_historical_day_source_revision"},
        ),
        (
            [("type", 1), ("scheduled_at", 1), ("time_block", 1)],
            {"name": "idx_forecast_serve_historical_type_day_block"},
        ),
    ]

    failed = 0
    for keys, options in index_specs:
        try:
            await collection.create_index(keys, background=True, **options)
        except Exception as e:
            failed += 1
            index_label = options.get("name") or keys
            logger.warning(
                "Failed to create forecast_serve_historical index %s: %s",
                index_label,
                e,
            )

    if failed:
        logger.warning(
            "forecast_serve_historical indexes partially ensured (%d failures)",
            failed,
        )
    else:
        logger.info("forecast_serve_historical collection indexes ensured")
