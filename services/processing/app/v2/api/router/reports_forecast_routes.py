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


class ReportsForecastRecord(BaseModel):
    """One row per date + time block (same columns as CSV)."""

    date: str = Field(..., description="Schedule date (DD-MM-YYYY)")
    time_block: int = Field(..., ge=1, le=96, description="Time block (1–96)")
    idf_demand: Optional[float] = Field(None, description="IDF auto forecast")
    daf_demand: Optional[float] = Field(None, description="DAF demand forecast")
    idf_price: Optional[float] = Field(None, description="IDF (RTM) forecasted price")
    dam_price: Optional[float] = Field(None, description="DAM forecasted price")
    gdam_price: Optional[float] = Field(None, description="GDAM forecasted price")
    hpdam_price: Optional[float] = Field(None, description="HPDAM forecasted price")


class ReportsForecastBlockBucket(ReportsForecastRecord):
    """Per-block row inside a date bucket (same shape as records)."""


class ReportsForecastDateBucket(BaseModel):
    date: str = Field(..., description="Schedule date (DD-MM-YYYY)")
    block_count: int
    blocks: List[ReportsForecastBlockBucket] = Field(default_factory=list)


class ReportsForecastQuery(BaseModel):
    from_date: str
    to_date: str
    from_block: int
    to_block: int
    format: str = Field(
        REPORTS_FORECAST_FORMAT_JSON,
        description="Requested response format: json, csv, or both",
    )
    include_flat_records: Optional[bool] = Field(
        None,
        description="When format=json, omit flat records if false",
    )


class ReportsForecastSummary(BaseModel):
    total_rows: int


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
    summary: ReportsForecastSummary
    by_date: Optional[List[ReportsForecastDateBucket]] = Field(
        None,
        description="Grouped view (one date → blocks) when format is json or both",
    )
    csv_export: ReportsForecastCsvExport
    records: Optional[List[ReportsForecastRecord]] = Field(
        None,
        description="Flat rows matching CSV columns (omitted when include_flat_records=false)",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "response_format": "json",
                    "query": {
                        "from_date": "2025-05-22",
                        "to_date": "2025-05-22",
                        "from_block": 1,
                        "to_block": 2,
                        "format": "json",
                        "include_flat_records": False,
                    },
                    "summary": {
                        "total_rows": 2,
                    },
                    "by_date": [
                        {
                            "date": "22-05-2025",
                            "block_count": 2,
                            "blocks": [
                                {
                                    "date": "22-05-2025",
                                    "time_block": 1,
                                    "idf_demand": 1200.5,
                                    "daf_demand": 1215.0,
                                    "idf_price": 3200.0,
                                    "dam_price": 3500.0,
                                    "gdam_price": 3400.0,
                                    "hpdam_price": 3300.0,
                                },
                                {
                                    "date": "22-05-2025",
                                    "time_block": 2,
                                    "idf_demand": 1198.0,
                                    "daf_demand": 1210.0,
                                    "idf_price": 3180.0,
                                    "dam_price": 3480.0,
                                    "gdam_price": 3380.0,
                                    "hpdam_price": 3280.0,
                                },
                            ],
                        }
                    ],
                    "csv_export": {
                        "headers": CSV_HEADERS,
                        "row_count": 2,
                    },
                    "records": [
                        {
                            "date": "22-05-2025",
                            "time_block": 1,
                            "idf_demand": 1200.5,
                            "daf_demand": 1215.0,
                            "idf_price": 3200.0,
                            "dam_price": 3500.0,
                            "gdam_price": 3400.0,
                            "hpdam_price": 3300.0,
                        },
                        {
                            "date": "22-05-2025",
                            "time_block": 2,
                            "idf_demand": 1198.0,
                            "daf_demand": 1210.0,
                            "idf_price": 3180.0,
                            "dam_price": 3480.0,
                            "gdam_price": 3380.0,
                            "hpdam_price": 3280.0,
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
            "(use by_date only — lighter payload)"
        ),
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Compose a forecast report for the given date and block range.

    **Date range limit:** inclusive range cannot exceed **90 days**.

    **Format (`format` query param):**

    - **json** (default) — `records` and/or `by_date` with columns:
      Date, Time Block, IDF Demand, DAF Demand, IDF Price, DAM Price, GDAM Price, HPDAM Price
    - **csv** — returns a CSV file (skips JSON section assembly)
    - **both** — JSON plus embedded CSV string in `csv_export.content`

    Use `include_flat_records=false` with `format=json` when the UI only needs `by_date`.
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

    Columns: Date (DD-MM-YYYY), Time Block, IDF Demand, DAF Demand,
    IDF Price (RTM), DAM Price, GDAM Price, HPDAM Price.
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
