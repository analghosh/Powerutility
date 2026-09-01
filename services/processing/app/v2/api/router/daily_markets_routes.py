"""
Daily Markets Report API — per-block trade detail, money summary and limit values.

Ports mplive's "Daily Markets Report", minus its TAM and GTAM annexures: EPMS
has no Term-Ahead pipeline, and mplive only ever had those numbers because an
operator typed them into limit_values by hand.
"""

import logging
from enum import Enum
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from libs.audit_queue.simple_logger import AuditLogger

from ....core.dependencies import get_audit_logger
from ...controller.daily_markets_controller import daily_markets_controller
from ...dependencies import get_user_context
from ...helpers.daily_markets_audit import (
    log_daily_markets_access_attempt,
    log_daily_markets_outcome,
)
from ...services.daily_markets_service import (
    CSV_HEADERS,
    FORMAT_BOTH,
    FORMAT_CSV,
    FORMAT_JSON,
)

router = APIRouter(
    prefix="/reports-daily-markets",
    tags=["Reports Daily Markets"],
)

logger = logging.getLogger(__name__)

_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


class DailyMarketsFormat(str, Enum):
    """Response payload selection — skips CSV assembly when only JSON is needed."""

    JSON = FORMAT_JSON
    CSV = FORMAT_CSV
    BOTH = FORMAT_BOTH


class DailyMarketsMarket(str, Enum):
    RTM = "RTM"
    DAM = "DAM"
    GDAM = "GDAM"
    HPDAM = "HPDAM"


class DailyMarketsQuery(BaseModel):
    report_date: str = Field(..., description="Delivery date (YYYY-MM-DD)")
    market: str
    exchange: str
    format: str


class DailyMarketsChargeRates(BaseModel):
    """The rates the money rows are derived from — not measured data."""

    fee_rate_per_mwh: float
    gst_multiplier: float
    sldc_flat_charge: float
    fcr_rate_per_mu: float
    source: str


class DailyMarketsCoverage(BaseModel):
    """Per-source block counts, so an empty day is distinguishable from a zero day."""

    trade_blocks: int
    price_blocks: int
    priced_blocks_non_zero: int
    limit_blocks: int
    expected_blocks: int
    has_trade_data: bool
    has_price_data: bool


class DailyMarketsSummary(BaseModel):
    funds_pay_in_out: float
    buy_outgo: float
    sell_revenue: float
    fees_incl_gst: float
    sldc_scheduling_operating_charges: float
    buy_cost: float
    sell_proceeds: float
    buy_bid_mu: float
    buy_cleared_mu: float
    sell_bid_mu: float
    sell_cleared_mu: float
    average_acp: float
    weighted_average_sell_price: float
    weighted_average_buy_price: float
    total_fcr: float
    total_amount: float


class DailyMarketsTradeRow(BaseModel):
    """Annexure I — one row per time block."""

    delivery_date: str = Field(..., description="Delivery date (DD-MM-YYYY)")
    block: int = Field(..., ge=1, le=96)
    trade_session: int = Field(..., description="30-minute trade session (1-48)")
    from_: str = Field(..., alias="from")
    to: str
    bid_qty: float = Field(..., description="Bid quantity in MW (negative = sell)")
    cleared_qty: float = Field(..., description="Cleared quantity in MW (negative = sell)")
    acp: float = Field(..., description="Area clearing price (Rs./MWh)")
    amount: float = Field(..., description="-cleared_qty * acp / 4, in INR")

    model_config = {"populate_by_name": True}


class DailyMarketsOtherRow(BaseModel):
    """Annexure IV — ceiling price, buffer price and (N-1) quantum per block."""

    date: str
    block: int = Field(..., ge=1, le=96)
    from_: str = Field(..., alias="from")
    to: str
    ceiling_price: Optional[float] = None
    buffer_price: Optional[float] = None
    n1_quantum: Optional[float] = None

    model_config = {"populate_by_name": True}


class DailyMarketsCsvExport(BaseModel):
    headers: List[str]
    row_count: int
    content: Optional[str] = Field(
        None, description="CSV file body (populated when format=csv or both)"
    )
    filename: Optional[str] = Field(
        None, description="Suggested download filename (populated when format=csv or both)"
    )


class DailyMarketsResponse(BaseModel):
    query: DailyMarketsQuery
    charge_rates: DailyMarketsChargeRates
    data_coverage: DailyMarketsCoverage
    summary: DailyMarketsSummary
    trade_details: List[DailyMarketsTradeRow]
    other_details: List[DailyMarketsOtherRow]
    csv_export: DailyMarketsCsvExport


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
    summary="Get Daily Markets Report",
    response_description="Trade summary, per-block trade detail and limit values",
)
async def get_daily_markets_report(
    request: Request,
    report_date: Optional[str] = Query(
        None,
        description="Delivery date (YYYY-MM-DD). Defaults to today.",
        example="2026-08-31",
        regex=_DATE_PATTERN,
    ),
    market: DailyMarketsMarket = Query(
        DailyMarketsMarket.RTM,
        description="Market segment to report on",
    ),
    exchange: str = Query(
        "IEX",
        description="Power exchange (IEX, HPX, PXIL)",
        min_length=2,
        max_length=16,
    ),
    format: DailyMarketsFormat = Query(
        DailyMarketsFormat.JSON,
        description=(
            "Response format: `json` (UI sections), `csv` (Annexure I as a file), "
            "or `both` (JSON with embedded CSV in csv_export.content)"
        ),
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Compose the daily markets report for one delivery date.

    Sections:

    - **summary** — the money rows. Every figure below the volume rows is
      derived from cleared volume using the rates in `charge_rates`; EPMS holds
      no fee, GST, SLDC or transmission dataset.
    - **trade_details** — Annexure I, 96 blocks of bid/cleared volume, ACP and amount.
    - **other_details** — Annexure IV, ceiling price, buffer price and (N-1) quantum.
    - **data_coverage** — how many blocks each source returned, so a day with no
      data reads differently from a day of genuine zeros.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    endpoint = "GET /reports-daily-markets/"
    response_format = format.value
    market_value = market.value

    log_daily_markets_access_attempt(
        audit_logger,
        user_id=user_id,
        username=username,
        correlation_id=correlation_id,
        endpoint=endpoint,
        request=request,
        report_date=report_date,
        market=market_value,
        exchange=exchange,
        response_format=response_format,
    )

    try:
        result = await daily_markets_controller.get_report(
            report_date=report_date,
            market=market_value,
            exchange=exchange,
            response_format=response_format,
        )

        audit_action = (
            "reports_daily_markets_download"
            if response_format == FORMAT_CSV
            else "reports_daily_markets_view"
        )
        audit_message = (
            "User downloaded the daily markets report CSV"
            if response_format == FORMAT_CSV
            else "User fetched the daily markets report"
        )

        log_daily_markets_outcome(
            audit_logger,
            action=audit_action,
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=True,
            message=audit_message,
            report_date=result.get("query", {}).get("report_date"),
            market=market_value,
            exchange=exchange,
            response_format=response_format,
            record_count=len(result.get("trade_details", [])),
            has_trade_data=result.get("data_coverage", {}).get("has_trade_data"),
        )

        if response_format == FORMAT_CSV:
            csv_export = result.get("csv_export", {})
            return _csv_file_response(
                csv_export.get("content", ""),
                csv_export.get("filename", "daily_markets_report.csv"),
            )

        return DailyMarketsResponse(**result)

    except HTTPException as exc:
        log_daily_markets_outcome(
            audit_logger,
            action="reports_daily_markets_view",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=False,
            message="Daily markets report request failed",
            report_date=report_date,
            market=market_value,
            exchange=exchange,
            response_format=response_format,
            failure_reason=str(exc.detail),
            http_status=exc.status_code,
        )
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
            message="Daily markets report request was rejected",
            report_date=report_date,
            market=market_value,
            exchange=exchange,
            response_format=response_format,
            failure_reason=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    except Exception as e:
        logger.exception(
            "Error building the daily markets report for user %s, date %s",
            user_id,
            report_date,
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
            message="Daily markets report request errored",
            report_date=report_date,
            market=market_value,
            exchange=exchange,
            response_format=response_format,
            failure_reason=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to build the daily markets report.",
        )


@router.get(
    "/download",
    response_model=None,
    status_code=status.HTTP_200_OK,
    summary="Download Daily Markets Report CSV",
    response_description="Annexure I as a CSV file",
)
async def download_daily_markets_csv(
    request: Request,
    report_date: Optional[str] = Query(
        None,
        description="Delivery date (YYYY-MM-DD). Defaults to today.",
        example="2026-08-31",
        regex=_DATE_PATTERN,
    ),
    market: DailyMarketsMarket = Query(
        DailyMarketsMarket.RTM,
        description="Market segment to report on",
    ),
    exchange: str = Query(
        "IEX",
        description="Power exchange (IEX, HPX, PXIL)",
        min_length=2,
        max_length=16,
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Download Annexure I (per-block trade detail) as CSV."""
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    endpoint = "GET /reports-daily-markets/download"
    market_value = market.value

    log_daily_markets_access_attempt(
        audit_logger,
        user_id=user_id,
        username=username,
        correlation_id=correlation_id,
        endpoint=endpoint,
        request=request,
        report_date=report_date,
        market=market_value,
        exchange=exchange,
        response_format=FORMAT_CSV,
    )

    try:
        csv_content, filename = await daily_markets_controller.download_csv(
            report_date=report_date,
            market=market_value,
            exchange=exchange,
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
            message="User downloaded the daily markets report CSV",
            report_date=report_date,
            market=market_value,
            exchange=exchange,
            response_format=FORMAT_CSV,
            filename=filename,
        )

        return _csv_file_response(csv_content, filename)

    except ValueError as e:
        log_daily_markets_outcome(
            audit_logger,
            action="reports_daily_markets_download",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=False,
            message="Daily markets report download was rejected",
            report_date=report_date,
            market=market_value,
            exchange=exchange,
            response_format=FORMAT_CSV,
            failure_reason=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    except Exception as e:
        logger.exception(
            "Error downloading the daily markets report for user %s, date %s",
            user_id,
            report_date,
        )
        log_daily_markets_outcome(
            audit_logger,
            action="reports_daily_markets_download",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            endpoint=endpoint,
            request=request,
            success=False,
            message="Daily markets report download errored",
            report_date=report_date,
            market=market_value,
            exchange=exchange,
            response_format=FORMAT_CSV,
            failure_reason=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to download the daily markets report.",
        )
