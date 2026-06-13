"""
Reports Forecast API — DAF/IDF forecasts with DAM/RTM market prices for CSV export.
"""

import logging
from enum import Enum
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from libs.audit_queue.simple_logger import AuditLogger
from libs.models.forecast_serve_entity import (
    FORECAST_SOURCE_CSV,
    FORECAST_SOURCE_DISPLAY_AUTO,
    FORECAST_SOURCE_DISPLAY_MANUAL,
    FORECAST_SOURCE_RTM_API,
)
from ....core.dependencies import get_audit_logger
from ...controller.reports_forecast_controller import reports_forecast_controller
from ...dependencies import get_user_context
from ...helpers.reports_forecast_audit import (
    log_reports_forecast_access_attempt,
    log_reports_forecast_outcome,
)
from ...services.reports_forecast_service import CSV_HEADERS, reports_forecast_service

from libs.helpers.reports_forecast_format import (
    REPORTS_FORECAST_FORMAT_BOTH,
    REPORTS_FORECAST_FORMAT_CSV,
    REPORTS_FORECAST_FORMAT_JSON,
)

router = APIRouter(
    prefix="/reports-forecast",
    tags=["Reports Forecast"],
)

logger = logging.getLogger(__name__)

_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


class ReportsForecastFormat(str, Enum):
    """Response payload selection — reduces server work when only one format is needed."""

    JSON = REPORTS_FORECAST_FORMAT_JSON
    CSV = REPORTS_FORECAST_FORMAT_CSV
    BOTH = REPORTS_FORECAST_FORMAT_BOTH


class IdfForecastValues(BaseModel):
    auto: Optional[float] = Field(
        None, description="IDF auto forecast from forecast_serve (rtm_forecast_api)"
    )
    manual: Optional[float] = Field(
        None, description="IDF manual forecast from forecast_serve (csv)"
    )


class DamForecastedPrices(BaseModel):
    dam: Optional[float] = Field(None, description="DAM forecasted price (Rs/MWh)")
    gdam: Optional[float] = Field(None, description="GDAM forecasted price (Rs/MWh)")
    hpdam: Optional[float] = Field(None, description="HPDAM forecasted price (Rs/MWh)")


class RtmForecastedPrices(BaseModel):
    rtm: Optional[float] = Field(None, description="RTM forecasted price (Rs/MWh)")


class ReportsForecastFlatRecord(BaseModel):
    """Flat row used for CSV export (one row per date + block + market)."""

    date: str = Field(..., description="Schedule date (YYYY-MM-DD)")
    time_block: int = Field(..., ge=1, le=96, description="Time block (1–96)")
    auto_forecast: Optional[float] = None
    manual_forecast: Optional[float] = None
    day_ahead_forecast_daf: Optional[float] = Field(
        None, description="Populated on DAM rows only"
    )
    market: str = Field(..., description="DAM or RTM")
    rtm_forecasted_price: Optional[float] = Field(
        None, description="Populated on RTM rows only"
    )
    dam_forecasted_price: Optional[float] = Field(
        None, description="Populated on DAM rows only"
    )
    gdam_forecasted_price: Optional[float] = Field(
        None, description="Populated on DAM rows only"
    )
    hpdam_forecasted_price: Optional[float] = Field(
        None, description="Populated on DAM rows only"
    )


class DamMarketRecord(BaseModel):
    date: str
    time_block: int
    idf_forecast: IdfForecastValues
    day_ahead_forecast_daf: Optional[float] = None
    forecasted_prices: DamForecastedPrices


class RtmMarketRecord(BaseModel):
    date: str
    time_block: int
    idf_forecast: IdfForecastValues
    forecasted_prices: RtmForecastedPrices


class ReportsForecastBlockBucket(BaseModel):
    time_block: int
    idf_forecast: IdfForecastValues
    daf_forecast: Optional[float] = Field(
        None, description="DAF forecast from demand_serve (type=DAF)"
    )
    dam: Dict[str, Any] = Field(
        ...,
        description="DAM section: day_ahead_forecast_daf + dam/gdam/hpdam prices",
    )
    rtm: Dict[str, Any] = Field(
        ...,
        description="RTM section: idf_forecast + rtm forecasted price",
    )


class ReportsForecastDateBucket(BaseModel):
    date: str
    block_count: int
    blocks: List[ReportsForecastBlockBucket] = Field(default_factory=list)


class ReportsForecastQuery(BaseModel):
    from_date: str
    to_date: str
    from_block: int
    to_block: int
    date_count: int
    blocks_per_day: int
    rows_per_day: int = Field(
        ...,
        description="Flat CSV rows per day (2 per block: DAM + RTM)",
    )
    max_date_range_days: int = Field(
        90,
        description="Maximum allowed inclusive days between from_date and to_date",
    )
    format: str = Field(
        REPORTS_FORECAST_FORMAT_JSON,
        description="Requested response format: json, csv, or both",
    )
    include_flat_records: Optional[bool] = Field(
        None,
        description="When format=json, omit flat records if false (lighter UI payload)",
    )


class ForecastSourceMeta(BaseModel):
    source: str
    forecast_source: str
    records_fetched: int


class ReportsForecastSources(BaseModel):
    daf_forecast: Dict[str, Any]
    idf_forecast: Dict[str, Any]
    forecasted_prices: Dict[str, Any]


class ReportsForecastSummary(BaseModel):
    total_rows: int
    dam_rows: int
    rtm_rows: int
    dates: List[str]


class ReportsForecastMarketBucket(BaseModel):
    market: str
    label: str
    description: str
    count: int
    records: List[Any] = Field(default_factory=list)


class ReportsForecastMarkets(BaseModel):
    dam: ReportsForecastMarketBucket
    rtm: ReportsForecastMarketBucket


class ReportsForecastCsvExport(BaseModel):
    headers: List[str]
    row_count: int
    content: Optional[str] = Field(
        None,
        description="CSV file body (populated when format=both)",
    )
    filename: Optional[str] = Field(
        None,
        description="Suggested download filename (populated when format=both)",
    )


class ReportsForecastResponse(BaseModel):
    response_format: str = Field(
        ...,
        description="Resolved format: json, csv, or both",
    )
    query: ReportsForecastQuery
    sources: ReportsForecastSources
    summary: ReportsForecastSummary
    markets: Optional[ReportsForecastMarkets] = Field(
        None,
        description="Present when format is json or both",
    )
    by_date: Optional[List[ReportsForecastDateBucket]] = Field(
        None,
        description="Present when format is json or both",
    )
    csv_export: ReportsForecastCsvExport
    records: Optional[List[ReportsForecastFlatRecord]] = Field(
        None,
        description="Flat rows for CSV (omitted when format=csv or include_flat_records=false)",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "query": {
                        "from_date": "2025-05-22",
                        "to_date": "2025-05-22",
                        "from_block": 1,
                        "to_block": 2,
                        "date_count": 1,
                        "blocks_per_day": 2,
                        "rows_per_day": 4,
                        "max_date_range_days": 90,
                    },
                    "sources": {
                        "daf_forecast": {
                            "collection": "demand_serve",
                            "historical_collection": "demand_serve_historical",
                            "routing": {
                                "today": "2025-05-22",
                                "past_dates_from": None,
                                "past_dates_to": None,
                                "current_dates_from": "2025-05-22",
                                "current_dates_to": "2025-05-22",
                            },
                            "type": "DAF",
                            "field": "forecast",
                            "records_fetched": 2,
                        },
                        "idf_forecast": {
                            "collection": "forecast_serve",
                            "type": "IDF",
                            "auto": {
                                "source": FORECAST_SOURCE_RTM_API,
                                "forecast_source": FORECAST_SOURCE_DISPLAY_AUTO,
                                "records_fetched": 2,
                            },
                            "manual": {
                                "source": FORECAST_SOURCE_CSV,
                                "forecast_source": FORECAST_SOURCE_DISPLAY_MANUAL,
                                "records_fetched": 2,
                            },
                        },
                        "forecasted_prices": {
                            "collection": "limit_values",
                            "category": "Limits",
                            "markets": ["RTM", "DAM", "GDAM", "HPDAM"],
                            "price_ids": [
                                "RTM_forecasted_price",
                                "DAM_forecasted_price",
                                "GDAM_forecasted_price",
                                "HPDAM_forecasted_price",
                            ],
                            "records_fetched": 8,
                        },
                    },
                    "summary": {
                        "total_rows": 4,
                        "dam_rows": 2,
                        "rtm_rows": 2,
                        "dates": ["2025-05-22"],
                    },
                    "markets": {
                        "dam": {
                            "market": "DAM",
                            "label": "Day Ahead Market",
                            "description": "DAF demand forecast with DAM, GDAM, and HPDAM forecasted prices",
                            "count": 2,
                            "records": [
                                {
                                    "date": "2025-05-22",
                                    "time_block": 1,
                                    "idf_forecast": {"auto": 1200.5, "manual": 1198.0},
                                    "day_ahead_forecast_daf": 1215.0,
                                    "forecasted_prices": {
                                        "dam": 3500.0,
                                        "gdam": 3400.0,
                                        "hpdam": 3300.0,
                                    },
                                }
                            ],
                        },
                        "rtm": {
                            "market": "RTM",
                            "label": "Real Time Market",
                            "description": "IDF auto and manual forecasts with RTM forecasted price",
                            "count": 2,
                            "records": [
                                {
                                    "date": "2025-05-22",
                                    "time_block": 1,
                                    "idf_forecast": {"auto": 1200.5, "manual": 1198.0},
                                    "forecasted_prices": {"rtm": 3200.0},
                                }
                            ],
                        },
                    },
                    "by_date": [
                        {
                            "date": "2025-05-22",
                            "block_count": 2,
                            "blocks": [
                                {
                                    "time_block": 1,
                                    "idf_forecast": {"auto": 1200.5, "manual": 1198.0},
                                    "daf_forecast": 1215.0,
                                    "dam": {
                                        "day_ahead_forecast_daf": 1215.0,
                                        "forecasted_prices": {
                                            "dam": 3500.0,
                                            "gdam": 3400.0,
                                            "hpdam": 3300.0,
                                        },
                                    },
                                    "rtm": {
                                        "idf_forecast": {"auto": 1200.5, "manual": 1198.0},
                                        "forecasted_prices": {"rtm": 3200.0},
                                    },
                                }
                            ],
                        }
                    ],
                    "csv_export": {
                        "headers": CSV_HEADERS,
                        "row_count": 4,
                    },
                    "records": [
                        {
                            "date": "2025-05-22",
                            "time_block": 1,
                            "auto_forecast": 1200.5,
                            "manual_forecast": 1198.0,
                            "day_ahead_forecast_daf": 1215.0,
                            "market": "DAM",
                            "rtm_forecasted_price": None,
                            "dam_forecasted_price": 3500.0,
                            "gdam_forecasted_price": 3400.0,
                            "hpdam_forecasted_price": 3300.0,
                        },
                        {
                            "date": "2025-05-22",
                            "time_block": 1,
                            "auto_forecast": 1200.5,
                            "manual_forecast": 1198.0,
                            "day_ahead_forecast_daf": None,
                            "market": "RTM",
                            "rtm_forecasted_price": 3200.0,
                            "dam_forecasted_price": None,
                            "gdam_forecasted_price": None,
                            "hpdam_forecasted_price": None,
                        },
                    ],
                }
            ]
        }
    }


def _validate_block_range(from_block: int, to_block: int) -> None:
    if from_block > to_block:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="from_block must be less than or equal to to_block",
        )


def _csv_file_response(
    result: Dict[str, Any],
    from_block: int,
    to_block: int,
) -> Response:
    records = result.get("records", [])
    csv_content = reports_forecast_service.generate_csv(records)
    filename = reports_forecast_service.build_csv_filename(
        result["query"]["from_date"],
        result["query"]["to_date"],
        from_block,
        to_block,
    )
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get(
    "/",
    response_model=None,
    status_code=status.HTTP_200_OK,
    summary="Get DAF/IDF forecast report by date range and time block range",
    responses={
        200: {
            "description": "JSON report, CSV file, or JSON with embedded CSV (format param)",
            "content": {
                "application/json": {},
                "text/csv": {},
            },
        },
        422: {"description": "Validation error"},
        500: {"description": "Failed to fetch reports forecast data"},
    },
)
async def get_reports_forecast(
    request: Request,
    schedule_date: Optional[str] = Query(
        None,
        description="Single schedule date (YYYY-MM-DD). Use this OR from_date + to_date.",
        example="2025-05-22",
        regex=_DATE_PATTERN,
    ),
    from_date: Optional[str] = Query(
        None,
        description="Start date (YYYY-MM-DD), inclusive.",
        example="2025-05-20",
        regex=_DATE_PATTERN,
    ),
    to_date: Optional[str] = Query(
        None,
        description="End date (YYYY-MM-DD), inclusive.",
        example="2025-05-22",
        regex=_DATE_PATTERN,
    ),
    from_block: int = Query(
        1,
        ge=1,
        le=96,
        description="Starting time block (1–96)",
    ),
    to_block: int = Query(
        96,
        ge=1,
        le=96,
        description="Ending time block (1–96), inclusive",
    ),
    format: ReportsForecastFormat = Query(
        ReportsForecastFormat.JSON,
        description=(
            "Response format: `json` (UI sections), `csv` (file download), "
            "or `both` (JSON with embedded CSV in csv_export.content)"
        ),
    ),
    include_flat_records: bool = Query(
        True,
        description=(
            "When format=json, set false to omit the flat `records` array "
            "(use markets/by_date only — lighter payload)"
        ),
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Compose a forecast report for the given date and block range.

    **Date range limit:** inclusive range cannot exceed **90 days**.

    **Format (`format` query param):**

    - **json** (default) — structured sections for UI (`markets`, `by_date`, optional `records`)
    - **csv** — returns a CSV file (skips JSON section assembly)
    - **both** — JSON plus embedded CSV string in `csv_export.content`

    Use `include_flat_records=false` with `format=json` when the UI only needs
    `by_date` / `markets` and you want a smaller response.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    endpoint = "GET /reports-forecast/"
    response_format = format.value

    log_reports_forecast_access_attempt(
        audit_logger,
        user_id=user_id,
        username=username,
        correlation_id=correlation_id,
        endpoint=endpoint,
        request=request,
        schedule_date=schedule_date,
        from_date=from_date,
        to_date=to_date,
        from_block=from_block,
        to_block=to_block,
        response_format=response_format,
    )

    try:
        _validate_block_range(from_block, to_block)

        result = await reports_forecast_controller.get_report(
            schedule_date=schedule_date,
            from_date=from_date,
            to_date=to_date,
            from_block=from_block,
            to_block=to_block,
            response_format=response_format,
            include_flat_records=include_flat_records,
        )

        audit_action = (
            "reports_forecast_download"
            if response_format == REPORTS_FORECAST_FORMAT_CSV
            else "reports_forecast_view"
        )
        audit_message = (
            "User downloaded reports forecast CSV"
            if response_format == REPORTS_FORECAST_FORMAT_CSV
            else "User fetched reports forecast data"
        )

        log_reports_forecast_outcome(
            audit_logger,
            action=audit_action,
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=True,
            message=audit_message,
            schedule_date=schedule_date,
            from_date=result.get("query", {}).get("from_date"),
            to_date=result.get("query", {}).get("to_date"),
            from_block=from_block,
            to_block=to_block,
            response_format=response_format,
            record_count=result.get("summary", {}).get("total_rows", 0),
            date_count=result.get("query", {}).get("date_count"),
        )

        if response_format == REPORTS_FORECAST_FORMAT_CSV:
            return _csv_file_response(result, from_block, to_block)

        return ReportsForecastResponse(**result)

    except HTTPException as exc:
        log_reports_forecast_outcome(
            audit_logger,
            action="reports_forecast_view",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=False,
            message="Reports forecast view request failed",
            schedule_date=schedule_date,
            from_date=from_date,
            to_date=to_date,
            from_block=from_block,
            to_block=to_block,
            response_format=response_format,
            failure_reason=str(exc.detail),
            http_status=exc.status_code,
        )
        raise
    except ValueError as e:
        log_reports_forecast_outcome(
            audit_logger,
            action="reports_forecast_view",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=False,
            message="Reports forecast view request rejected",
            schedule_date=schedule_date,
            from_date=from_date,
            to_date=to_date,
            from_block=from_block,
            to_block=to_block,
            response_format=response_format,
            failure_reason=str(e),
            http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        ) from e
    except Exception as e:
        log_reports_forecast_outcome(
            audit_logger,
            action="reports_forecast_view",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=False,
            message="Reports forecast view request failed",
            schedule_date=schedule_date,
            from_date=from_date,
            to_date=to_date,
            from_block=from_block,
            to_block=to_block,
            failure_reason=str(e),
            http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
        audit_logger.log_error_event(
            service="processing",
            error_name="ReportsForecastFetchError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            from_date=from_date,
            to_date=to_date,
            from_block=from_block,
            to_block=to_block,
        )
        logger.exception(
            "Error fetching reports forecast for user %s schedule_date=%s from=%s to=%s blocks=%s-%s",
            user_id,
            schedule_date,
            from_date,
            to_date,
            from_block,
            to_block,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch reports forecast data.",
        ) from e


@router.get(
    "/download",
    status_code=status.HTTP_200_OK,
    summary="Download DAF/IDF forecast report as CSV",
    responses={
        200: {"description": "CSV file"},
        422: {"description": "Validation error"},
        500: {"description": "Failed to generate reports forecast CSV"},
    },
)
async def download_reports_forecast_csv(
    request: Request,
    schedule_date: Optional[str] = Query(
        None,
        description="Single schedule date (YYYY-MM-DD). Use this OR from_date + to_date.",
        example="2025-05-22",
        regex=_DATE_PATTERN,
    ),
    from_date: Optional[str] = Query(
        None,
        description="Start date (YYYY-MM-DD), inclusive.",
        example="2025-05-20",
        regex=_DATE_PATTERN,
    ),
    to_date: Optional[str] = Query(
        None,
        description="End date (YYYY-MM-DD), inclusive.",
        example="2025-05-22",
        regex=_DATE_PATTERN,
    ),
    from_block: int = Query(
        1,
        ge=1,
        le=96,
        description="Starting time block (1–96)",
    ),
    to_block: int = Query(
        96,
        ge=1,
        le=96,
        description="Ending time block (1–96), inclusive",
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Download the forecast report as CSV.

    Equivalent to `GET /reports-forecast/?format=csv` with the same query parameters.

    **Date range limit:** inclusive range cannot exceed **90 days**.

    Columns: Date, Time Block, Auto Forecast, Manual Forecast,
    Day Ahead Forecast(DAF), Market(DAM/RTM), RTM Forecasted Price,
    DAM Forecasted Price, GDAM Forecasted Price, HPDAM Forecasted Price.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    endpoint = "GET /reports-forecast/download"

    log_reports_forecast_access_attempt(
        audit_logger,
        user_id=user_id,
        username=username,
        correlation_id=correlation_id,
        endpoint=endpoint,
        request=request,
        schedule_date=schedule_date,
        from_date=from_date,
        to_date=to_date,
        from_block=from_block,
        to_block=to_block,
        response_format=REPORTS_FORECAST_FORMAT_CSV,
    )

    try:
        _validate_block_range(from_block, to_block)

        csv_content, filename = await reports_forecast_controller.download_csv(
            schedule_date=schedule_date,
            from_date=from_date,
            to_date=to_date,
            from_block=from_block,
            to_block=to_block,
        )

        log_reports_forecast_outcome(
            audit_logger,
            action="reports_forecast_download",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=True,
            message="User downloaded reports forecast CSV",
            schedule_date=schedule_date,
            from_date=from_date or schedule_date,
            to_date=to_date or schedule_date,
            from_block=from_block,
            to_block=to_block,
            response_format=REPORTS_FORECAST_FORMAT_CSV,
            filename=filename,
            csv_row_count=csv_content.count("\n") - 1 if csv_content else 0,
        )

        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    except HTTPException as exc:
        log_reports_forecast_outcome(
            audit_logger,
            action="reports_forecast_download",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=False,
            message="Reports forecast CSV download failed",
            schedule_date=schedule_date,
            from_date=from_date,
            to_date=to_date,
            from_block=from_block,
            to_block=to_block,
            response_format=REPORTS_FORECAST_FORMAT_CSV,
            failure_reason=str(exc.detail),
            http_status=exc.status_code,
        )
        raise
    except ValueError as e:
        log_reports_forecast_outcome(
            audit_logger,
            action="reports_forecast_download",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=False,
            message="Reports forecast CSV download rejected",
            schedule_date=schedule_date,
            from_date=from_date,
            to_date=to_date,
            from_block=from_block,
            to_block=to_block,
            failure_reason=str(e),
            http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        ) from e
    except Exception as e:
        log_reports_forecast_outcome(
            audit_logger,
            action="reports_forecast_download",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=False,
            message="Reports forecast CSV download failed",
            schedule_date=schedule_date,
            from_date=from_date,
            to_date=to_date,
            from_block=from_block,
            to_block=to_block,
            failure_reason=str(e),
            http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
        audit_logger.log_error_event(
            service="processing",
            error_name="ReportsForecastDownloadError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            from_date=from_date,
            to_date=to_date,
            from_block=from_block,
            to_block=to_block,
        )
        logger.exception(
            "Error downloading reports forecast CSV for user %s schedule_date=%s from=%s to=%s",
            user_id,
            schedule_date,
            from_date,
            to_date,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate reports forecast CSV.",
        ) from e
