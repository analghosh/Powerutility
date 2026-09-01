"""
Weekly Markets Report API — one ISO week of the daily report, day columns plus a total.

The Total column is recomputed from every block in the week rather than summed
from the seven day columns, because those are rounded for display and the
weighted average prices divide by them.
"""

import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from libs.audit_queue.simple_logger import AuditLogger

from ....core.dependencies import get_audit_logger
from ...controller.weekly_markets_controller import weekly_markets_controller
from ...dependencies import get_user_context
from ...helpers.daily_markets_audit import (
    log_daily_markets_access_attempt,
    log_daily_markets_outcome,
)
from ...services.daily_markets_service import FORMAT_BOTH, FORMAT_CSV, FORMAT_JSON

router = APIRouter(
    prefix="/reports-weekly-markets",
    tags=["Reports Weekly Markets"],
)

logger = logging.getLogger(__name__)


class WeeklyMarketsFormat(str, Enum):
    JSON = FORMAT_JSON
    CSV = FORMAT_CSV
    BOTH = FORMAT_BOTH


class WeeklyMarketsMarket(str, Enum):
    RTM = "RTM"
    DAM = "DAM"
    GDAM = "GDAM"
    HPDAM = "HPDAM"


class WeeklyMarketsQuery(BaseModel):
    year: int
    week: int
    market: str
    exchange: str
    format: str
    from_date: str
    to_date: str


class WeeklyMarketsChargeRates(BaseModel):
    fee_rate_per_mwh: float
    gst_percent: float
    gst_multiplier: float
    sldc_flat_charge: float
    fcr_rate_per_mu: float
    sources: Dict[str, str]
    fee_rate_basis: str


class WeeklyMarketsDay(BaseModel):
    day: int = Field(..., ge=1, le=7)
    date: str
    has_trade_data: bool
    price_blocks: int


class WeeklyMarketsCoverage(BaseModel):
    days_with_trade_data: int
    expected_days: int
    expected_blocks: int


class WeeklyMarketsRow(BaseModel):
    """
    One report line. Money and price rows carry floats; the Bid || Cleared rows
    carry a formatted string, matching how the daily report presents the pair.
    """

    particulars: str
    uom: str
    day1: Union[float, str]
    day2: Union[float, str]
    day3: Union[float, str]
    day4: Union[float, str]
    day5: Union[float, str]
    day6: Union[float, str]
    day7: Union[float, str]
    total: Union[float, str]


class WeeklyMarketsCsvExport(BaseModel):
    headers: List[str]
    row_count: int
    content: Optional[str] = None
    filename: Optional[str] = None


class WeeklyMarketsResponse(BaseModel):
    query: WeeklyMarketsQuery
    charge_rates: WeeklyMarketsChargeRates
    days: List[WeeklyMarketsDay]
    data_coverage: WeeklyMarketsCoverage
    rows: List[WeeklyMarketsRow]
    summary: Dict[str, float]
    csv_export: WeeklyMarketsCsvExport


def _csv_file_response(csv_content: str, filename: str) -> Response:
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get(
    "/",
    response_model=None,
    status_code=status.HTTP_200_OK,
    summary="Get Weekly Markets Report",
    response_description="Seven day columns plus a total, for one ISO week",
)
async def get_weekly_markets_report(
    request: Request,
    year: Optional[int] = Query(None, ge=2000, le=2100, description="ISO year. Defaults to the current week."),
    week: Optional[int] = Query(None, ge=1, le=53, description="ISO week number (1-53)"),
    market: WeeklyMarketsMarket = Query(WeeklyMarketsMarket.RTM),
    exchange: str = Query("IEX", min_length=2, max_length=16),
    format: WeeklyMarketsFormat = Query(WeeklyMarketsFormat.JSON),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    One ISO week of the daily markets report.

    Buy Cost, Sell Proceeds and Transmission Charge are computed the way the
    daily report computes them. mplive read them from hand-entered limit values
    that EPMS does not hold, which would have left those three rows empty.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    endpoint = "GET /reports-weekly-markets/"
    market_value = market.value
    response_format = format.value

    log_daily_markets_access_attempt(
        audit_logger,
        user_id=user_id,
        username=username,
        correlation_id=correlation_id,
        endpoint=endpoint,
        request=request,
        report_date=f"{year}-W{week}" if year and week else None,
        market=market_value,
        exchange=exchange,
        response_format=response_format,
    )

    try:
        result = await weekly_markets_controller.get_report(
            year=year,
            week=week,
            market=market_value,
            exchange=exchange,
            response_format=response_format,
        )

        log_daily_markets_outcome(
            audit_logger,
            action=(
                "reports_daily_markets_download"
                if response_format == FORMAT_CSV
                else "reports_daily_markets_view"
            ),
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=True,
            message="User fetched the weekly markets report",
            report_date=f"{result['query']['year']}-W{result['query']['week']}",
            market=market_value,
            exchange=exchange,
            response_format=response_format,
            record_count=len(result.get("rows", [])),
            days_with_trade_data=result["data_coverage"]["days_with_trade_data"],
        )

        if response_format == FORMAT_CSV:
            export = result.get("csv_export", {})
            return _csv_file_response(
                export.get("content", ""),
                export.get("filename", "weekly_markets_report.csv"),
            )

        return WeeklyMarketsResponse(**result)

    except HTTPException:
        raise
    except ValueError as e:
        log_daily_markets_outcome(
            audit_logger,
            action="reports_daily_markets_view",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=False,
            message="Weekly markets report request was rejected",
            market=market_value,
            exchange=exchange,
            response_format=response_format,
            failure_reason=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        )
    except Exception as e:
        logger.exception(
            "Error building the weekly markets report for user %s, %s-W%s",
            user_id, year, week,
        )
        log_daily_markets_outcome(
            audit_logger,
            action="reports_daily_markets_view",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=False,
            message="Weekly markets report request errored",
            market=market_value,
            exchange=exchange,
            response_format=response_format,
            failure_reason=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to build the weekly markets report.",
        )


@router.get(
    "/download",
    response_model=None,
    status_code=status.HTTP_200_OK,
    summary="Download Weekly Markets Report CSV",
    response_description="The weekly summary as a CSV file",
)
async def download_weekly_markets_csv(
    request: Request,
    year: Optional[int] = Query(None, ge=2000, le=2100),
    week: Optional[int] = Query(None, ge=1, le=53),
    market: WeeklyMarketsMarket = Query(WeeklyMarketsMarket.RTM),
    exchange: str = Query("IEX", min_length=2, max_length=16),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Download the weekly summary as CSV."""
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    endpoint = "GET /reports-weekly-markets/download"

    try:
        content, filename = await weekly_markets_controller.download_csv(
            year=year, week=week, market=market.value, exchange=exchange
        )
        log_daily_markets_outcome(
            audit_logger,
            action="reports_daily_markets_download",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=True,
            message="User downloaded the weekly markets report CSV",
            market=market.value,
            exchange=exchange,
            response_format=FORMAT_CSV,
            filename=filename,
        )
        return _csv_file_response(content, filename)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        )
    except Exception:
        logger.exception("Error downloading the weekly markets report")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to download the weekly markets report.",
        )
