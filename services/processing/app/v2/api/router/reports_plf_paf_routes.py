"""
Reports PLF/PAF API — per-plant Plant Load Factor and Plant Availability Factor.
"""

import logging
from enum import Enum
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger
from ...controller.reports_plf_paf_controller import reports_plf_paf_controller
from ...dependencies import get_user_context
from ...helpers.reports_plf_paf_audit import (
    log_reports_plf_paf_access_attempt,
    log_reports_plf_paf_outcome,
)
from ...services.reports_plf_paf_service import CSV_HEADERS

from libs.helpers.reports_forecast_format import (
    REPORTS_FORECAST_FORMAT_BOTH,
    REPORTS_FORECAST_FORMAT_CSV,
    REPORTS_FORECAST_FORMAT_JSON,
)

router = APIRouter(
    prefix="/reports-plf-paf",
    tags=["Reports PLF/PAF"],
)

logger = logging.getLogger(__name__)

_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


class ReportsPlfPafFormat(str, Enum):
    """Response payload selection — reduces server work when only one format is needed."""

    JSON = REPORTS_FORECAST_FORMAT_JSON
    CSV = REPORTS_FORECAST_FORMAT_CSV
    BOTH = REPORTS_FORECAST_FORMAT_BOTH


class ReportsPlfPafPlant(BaseModel):
    """One row per plant, averaged across the requested dates and blocks."""

    plant_id: str = Field(..., description="Plant identifier from supply_serve")
    plant_name: str = Field(..., description="Display name (falls back to plant_id)")
    technology: Optional[str] = Field(None, description="Thermal / Nuclear / Solar / …")
    category: Optional[str] = Field(None, description="Plant category from the master")
    dc: Optional[float] = Field(None, description="Average declared capacity (MW)")
    sg: Optional[float] = Field(None, description="Average scheduled generation (MW)")
    plf: Optional[float] = Field(None, description="Plant Load Factor: SG / DC x 100")
    paf: Optional[float] = Field(
        None,
        description=(
            "Plant Availability Factor: DC / contracted capacity x 100. "
            "Null when the plant master carries no capacity for this plant."
        ),
    )
    contracted_capacity: Optional[float] = Field(
        None,
        description="PTC contracted capacity in MW (plant_master.ppa)",
    )
    block_samples: int = Field(
        0,
        description="Number of time-block rows that fed the averages",
    )


class ReportsPlfPafQuery(BaseModel):
    from_date: str
    to_date: str
    from_block: int
    to_block: int
    format: str = Field(
        REPORTS_FORECAST_FORMAT_JSON,
        description="Requested response format: json, csv, or both",
    )


class ReportsPlfPafSummary(BaseModel):
    plant_count: int
    plants_without_capacity: int = Field(
        ...,
        description="Plants reporting a null PAF because the master has no capacity",
    )
    capacity_source: str = Field(
        ...,
        description="Which master field supplied the PAF denominator",
    )


class ReportsPlfPafCsvExport(BaseModel):
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


class ReportsPlfPafResponse(BaseModel):
    response_format: str = Field(..., description="Resolved format: json, csv, or both")
    query: ReportsPlfPafQuery
    summary: ReportsPlfPafSummary
    plants: List[ReportsPlfPafPlant] = Field(default_factory=list)
    csv_export: ReportsPlfPafCsvExport

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "response_format": "json",
                    "query": {
                        "from_date": "2026-08-29",
                        "to_date": "2026-08-31",
                        "from_block": 1,
                        "to_block": 96,
                        "format": "json",
                    },
                    "summary": {
                        "plant_count": 2,
                        "plants_without_capacity": 1,
                        "capacity_source": "plant_master.ppa",
                    },
                    "plants": [
                        {
                            "plant_id": "VALLURNTECL",
                            "plant_name": "NTPC Tamil Nadu Energy Company Ltd Vallur",
                            "technology": "Thermal",
                            "category": "NTPC",
                            "dc": 24.43,
                            "sg": 23.5,
                            "plf": 96.18,
                            "paf": 93.85,
                            "contracted_capacity": 26.03,
                            "block_samples": 288,
                        },
                        {
                            "plant_id": "GEPL_BKN2",
                            "plant_name": "GEPL Bikaner II",
                            "technology": "Solar",
                            "category": "RE",
                            "dc": 7.84,
                            "sg": 7.42,
                            "plf": 94.67,
                            "paf": None,
                            "contracted_capacity": None,
                            "block_samples": 288,
                        },
                    ],
                    "csv_export": {"headers": CSV_HEADERS, "row_count": 2},
                }
            ]
        }
    }


class ReportsPlfPafBlock(BaseModel):
    """One plant's figures for one 15-minute block of one date."""

    date: str = Field(..., description="Schedule date, DD-MM-YYYY")
    time_block: int = Field(..., description="Time block number (1–96)")
    time_label: str = Field(..., description="Block time span, e.g. 11:45-12:00")
    dc: Optional[float] = Field(None, description="Declared capacity (MW); null when no DC row")
    sg: Optional[float] = Field(None, description="Scheduled generation (MW)")
    plf: Optional[float] = Field(None, description="SG / DC x 100 for this block")
    paf: Optional[float] = Field(None, description="DC / contracted capacity x 100 for this block")


class ReportsPlfPafBlocksPlant(BaseModel):
    plant_id: str
    plant_name: str
    technology: Optional[str] = None
    contracted_capacity: Optional[float] = None


class ReportsPlfPafBlocksQuery(BaseModel):
    plant_id: str
    from_date: str
    to_date: str
    from_block: int
    to_block: int


class ReportsPlfPafBlocksResponse(BaseModel):
    query: ReportsPlfPafBlocksQuery
    plant: ReportsPlfPafBlocksPlant
    blocks: List[ReportsPlfPafBlock] = Field(default_factory=list)


def _validate_block_range(from_block: int, to_block: int) -> None:
    if from_block > to_block:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="from_block must be less than or equal to to_block",
        )


@router.get(
    "/",
    response_model=None,
    status_code=status.HTTP_200_OK,
    summary="Get per-plant PLF and PAF for a date range",
    responses={
        200: {
            "description": "JSON report, CSV file, or JSON with embedded CSV (format param)",
            "content": {"application/json": {}, "text/csv": {}},
        },
        422: {"description": "Validation error"},
        500: {"description": "Failed to fetch PLF/PAF report data"},
    },
)
async def get_reports_plf_paf(
    request: Request,
    schedule_date: Optional[str] = Query(
        None,
        description="Single schedule date (YYYY-MM-DD). Use this OR from_date + to_date.",
        example="2026-08-31",
        regex=_DATE_PATTERN,
    ),
    from_date: Optional[str] = Query(
        None,
        description="Start date (YYYY-MM-DD), inclusive.",
        example="2026-08-29",
        regex=_DATE_PATTERN,
    ),
    to_date: Optional[str] = Query(
        None,
        description="End date (YYYY-MM-DD), inclusive.",
        example="2026-08-31",
        regex=_DATE_PATTERN,
    ),
    from_block: int = Query(1, ge=1, le=96, description="Starting time block (1–96)"),
    to_block: int = Query(96, ge=1, le=96, description="Ending time block (1–96), inclusive"),
    format: ReportsPlfPafFormat = Query(
        ReportsPlfPafFormat.JSON,
        description=(
            "Response format: `json` (UI table), `csv` (file download), "
            "or `both` (JSON with embedded CSV in csv_export.content)"
        ),
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Average DC and SG per plant over the requested dates and blocks, then derive:

    - **PLF** = average SG / average DC x 100 — 0 when the plant declared no capacity
      (RLDC publishes SG but no DC for nuclear and other must-run plants).
    - **PAF** = average DC / contracted capacity x 100 — `null` when the plant master
      carries no capacity for that plant (renewables and open-access blocks).

    **Date range limit:** inclusive range cannot exceed **90 days**.

    The PAF denominator is `plant_master.ppa`, PTC's contracted MW. The legacy
    mplive report divided by the plant's tie-line capacity, which EPMS does not hold.

    `format=csv` returns the block-wise file: each plant's average row, then one
    row per date and block. `GET /reports-plf-paf/blocks` serves one plant's
    blocks to the screen.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    endpoint = "GET /reports-plf-paf/"
    response_format = format.value

    log_reports_plf_paf_access_attempt(
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

        result = await reports_plf_paf_controller.get_report(
            schedule_date=schedule_date,
            from_date=from_date,
            to_date=to_date,
            from_block=from_block,
            to_block=to_block,
            response_format=response_format,
        )

        is_csv = response_format == REPORTS_FORECAST_FORMAT_CSV
        log_reports_plf_paf_outcome(
            audit_logger,
            action="reports_plf_paf_download" if is_csv else "reports_plf_paf_view",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=True,
            message=(
                "User downloaded PLF/PAF report CSV"
                if is_csv
                else "User fetched PLF/PAF report data"
            ),
            schedule_date=schedule_date,
            from_date=result.get("query", {}).get("from_date"),
            to_date=result.get("query", {}).get("to_date"),
            from_block=from_block,
            to_block=to_block,
            response_format=response_format,
            record_count=result.get("summary", {}).get("plant_count", 0),
        )

        if is_csv:
            csv_content = result["csv_export"]["content"]
            filename = result["csv_export"]["filename"]
            return Response(
                content=csv_content,
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )

        return ReportsPlfPafResponse(**result)

    except HTTPException as exc:
        log_reports_plf_paf_outcome(
            audit_logger,
            action="reports_plf_paf_view",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=False,
            message="PLF/PAF report request failed",
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
        log_reports_plf_paf_outcome(
            audit_logger,
            action="reports_plf_paf_view",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=False,
            message="PLF/PAF report request rejected",
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
        log_reports_plf_paf_outcome(
            audit_logger,
            action="reports_plf_paf_view",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=False,
            message="PLF/PAF report request failed",
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
            error_name="ReportsPlfPafFetchError",
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
            "Error fetching PLF/PAF report for user %s schedule_date=%s from=%s to=%s blocks=%s-%s",
            user_id,
            schedule_date,
            from_date,
            to_date,
            from_block,
            to_block,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch PLF/PAF report data.",
        ) from e


@router.get(
    "/download",
    status_code=status.HTTP_200_OK,
    summary="Download the PLF/PAF report as CSV",
    responses={
        200: {"description": "CSV file"},
        422: {"description": "Validation error"},
        500: {"description": "Failed to generate PLF/PAF report CSV"},
    },
)
async def download_reports_plf_paf_csv(
    request: Request,
    schedule_date: Optional[str] = Query(
        None,
        description="Single schedule date (YYYY-MM-DD). Use this OR from_date + to_date.",
        example="2026-08-31",
        regex=_DATE_PATTERN,
    ),
    from_date: Optional[str] = Query(
        None,
        description="Start date (YYYY-MM-DD), inclusive.",
        example="2026-08-29",
        regex=_DATE_PATTERN,
    ),
    to_date: Optional[str] = Query(
        None,
        description="End date (YYYY-MM-DD), inclusive.",
        example="2026-08-31",
        regex=_DATE_PATTERN,
    ),
    from_block: int = Query(1, ge=1, le=96, description="Starting time block (1–96)"),
    to_block: int = Query(96, ge=1, le=96, description="Ending time block (1–96), inclusive"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Download the PLF/PAF report as CSV.

    Equivalent to `GET /reports-plf-paf/?format=csv` with the same query parameters.

    Columns: Plant, Technology, Contracted Capacity (MW), Date, Block, Time,
    DC (MW), SG (MW), PLF (%), PAF (%). Each plant's first row is its average
    (Date and Block = All, Time = Average); one row per date and block follows.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    endpoint = "GET /reports-plf-paf/download"

    log_reports_plf_paf_access_attempt(
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

        csv_content, filename = await reports_plf_paf_controller.download_csv(
            schedule_date=schedule_date,
            from_date=from_date,
            to_date=to_date,
            from_block=from_block,
            to_block=to_block,
        )

        log_reports_plf_paf_outcome(
            audit_logger,
            action="reports_plf_paf_download",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=True,
            message="User downloaded PLF/PAF report CSV",
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
        log_reports_plf_paf_outcome(
            audit_logger,
            action="reports_plf_paf_download",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=False,
            message="PLF/PAF report CSV download failed",
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
        log_reports_plf_paf_outcome(
            audit_logger,
            action="reports_plf_paf_download",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=False,
            message="PLF/PAF report CSV download rejected",
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
        log_reports_plf_paf_outcome(
            audit_logger,
            action="reports_plf_paf_download",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=False,
            message="PLF/PAF report CSV download failed",
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
            error_name="ReportsPlfPafDownloadError",
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
            "Error downloading PLF/PAF report CSV for user %s schedule_date=%s from=%s to=%s",
            user_id,
            schedule_date,
            from_date,
            to_date,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate PLF/PAF report CSV.",
        ) from e


@router.get(
    "/blocks",
    response_model=ReportsPlfPafBlocksResponse,
    status_code=status.HTTP_200_OK,
    summary="Get one plant's DC, SG, PLF and PAF for every block in a date range",
    responses={
        422: {"description": "Validation error"},
        500: {"description": "Failed to fetch PLF/PAF block data"},
    },
)
async def get_reports_plf_paf_blocks(
    request: Request,
    plant_id: str = Query(..., min_length=1, description="Plant identifier from the report"),
    schedule_date: Optional[str] = Query(
        None,
        description="Single schedule date (YYYY-MM-DD). Use this OR from_date + to_date.",
        example="2026-08-31",
        regex=_DATE_PATTERN,
    ),
    from_date: Optional[str] = Query(
        None,
        description="Start date (YYYY-MM-DD), inclusive.",
        example="2026-08-29",
        regex=_DATE_PATTERN,
    ),
    to_date: Optional[str] = Query(
        None,
        description="End date (YYYY-MM-DD), inclusive.",
        example="2026-08-31",
        regex=_DATE_PATTERN,
    ),
    from_block: int = Query(1, ge=1, le=96, description="Starting time block (1–96)"),
    to_block: int = Query(96, ge=1, le=96, description="Ending time block (1–96), inclusive"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    One row per date and block for the plant, by the same rules as the plant row:

    - **PLF** = SG / DC x 100 — 0 when DC is 0, `null` when the block has no DC row.
    - **PAF** = DC / contracted capacity x 100 — `null` without a contracted capacity.

    The plant row in `GET /reports-plf-paf/` is the average of these blocks.
    **Date range limit:** 90 days.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    endpoint = "GET /reports-plf-paf/blocks"
    audit = {
        "user_id": user_id,
        "username": username,
        "correlation_id": correlation_id,
        "endpoint": endpoint,
        "request": request,
        "schedule_date": schedule_date,
        "from_date": from_date,
        "to_date": to_date,
        "from_block": from_block,
        "to_block": to_block,
    }

    log_reports_plf_paf_access_attempt(audit_logger, **audit)

    try:
        _validate_block_range(from_block, to_block)
        result = await reports_plf_paf_controller.get_blocks(
            plant_id=plant_id,
            schedule_date=schedule_date,
            from_date=from_date,
            to_date=to_date,
            from_block=from_block,
            to_block=to_block,
        )
        log_reports_plf_paf_outcome(
            audit_logger,
            action="reports_plf_paf_blocks_view",
            success=True,
            message="User fetched PLF/PAF block data",
            plant_id=plant_id,
            record_count=len(result["blocks"]),
            **audit,
        )
        return ReportsPlfPafBlocksResponse(**result)

    except HTTPException as exc:
        log_reports_plf_paf_outcome(
            audit_logger,
            action="reports_plf_paf_blocks_view",
            success=False,
            message="PLF/PAF block request failed",
            plant_id=plant_id,
            failure_reason=str(exc.detail),
            http_status=exc.status_code,
            **audit,
        )
        raise
    except ValueError as e:
        log_reports_plf_paf_outcome(
            audit_logger,
            action="reports_plf_paf_blocks_view",
            success=False,
            message="PLF/PAF block request rejected",
            plant_id=plant_id,
            failure_reason=str(e),
            http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            **audit,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        ) from e
    except Exception as e:
        log_reports_plf_paf_outcome(
            audit_logger,
            action="reports_plf_paf_blocks_view",
            success=False,
            message="PLF/PAF block request failed",
            plant_id=plant_id,
            failure_reason=str(e),
            http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            **audit,
        )
        audit_logger.log_error_event(
            service="processing",
            error_name="ReportsPlfPafBlocksFetchError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            plant_id=plant_id,
            from_date=from_date,
            to_date=to_date,
        )
        logger.exception(
            "Error fetching PLF/PAF blocks for user %s plant=%s from=%s to=%s",
            user_id,
            plant_id,
            from_date,
            to_date,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch PLF/PAF block data.",
        ) from e
