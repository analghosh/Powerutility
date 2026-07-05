from typing import Optional, Dict, Any
import logging
import uuid

from fastapi import APIRouter, Query, Body, Depends, Request, HTTPException, status, Response
from pydantic import BaseModel, Field

from services.processing.app.v2.controller.new_gdam_bidding_controller import gdam_bidding_controller
from services.processing.app.v2.controller.gdam_transaction_controller import gdam_transaction_controller
from libs.core.dependencies import get_current_user
from libs.core.jwt_validator import TokenPayload
from datetime import datetime

logger = logging.getLogger(__name__)

public_router = APIRouter(
    prefix="/gdam-bidding/public",
    tags=["GDAM Bidding V2 (Public)"],
    include_in_schema=True,
)


class SingleBidRequest(BaseModel):
    """Request body for single bid submission"""
    OCFToggle: bool = Field(default=False, description="If true, fetch OCF values from limit_values")
    Data: Dict[str, Any] = Field(..., description="Bid creation data")
    bidAreaId: Optional[str] = Field(default=None, description="Optional bid area ID override")
    SubmitStartTime: Optional[str] = Field(None, description="Submit start time in HH:MM format")


@public_router.post(
    "/submit/single-bid",
    summary="Submit GDAM Single Bid",
    response_description="GDAM Bid Submission Response",
    response_model=Dict[str, Any]
)
async def submit_single_bid(
    request: SingleBidRequest,
    response: Response,
    current_user: TokenPayload = Depends(get_current_user)
):
    """
    Submit GDAM Single Bid to external exchange API
    """
    correlation_id = str(uuid.uuid4())
    response.headers["X-Correlation-ID"] = correlation_id
    submit_start_time = datetime.now().time()

    result = await gdam_transaction_controller.submit_bid(
        ocf_toggle=request.OCFToggle,
        bid_area_id=request.bidAreaId or "",
        current_user=current_user,
        bid_data=request.Data,
        correlation_id=correlation_id,
        start_time=submit_start_time
    )
    return result


@public_router.get(
    "/single-bid/bid-submission-detail",
    summary="Get GDAM bid submission details",
    response_description="GDAM bid submission details response",
)
async def get_bid_submission_detail(
    request: Request,
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
):
    """
    Get GDAM bid submission details for a specific date.
    """
    return await gdam_bidding_controller.get_bid_submission_details(date)


@public_router.get(
    "/bid-status",
    summary="Get GDAM bid status data",
    response_description="Returns comprehensive bid status data",
)
async def get_bid_status(
    request: Request,
    bid_status_date: Optional[str] = Query(
        None,
        description="Bid status date in YYYY-MM-DD format",
        example="2024-01-15"
    ),
):
    """
    Retrieves comprehensive bid status data for GDAM bidding.
    """
    request_dict = {}
    if bid_status_date:
        request_dict["bid_status_date"] = bid_status_date
    return await gdam_bidding_controller.bid_status(request_dict)


@public_router.get(
    "/single-bid/bid-book",
    summary="Get GDAM Single Bid Book",
    response_description="GDAM Single Bid Book Response",
)
async def get_gdam_single_bid_book(
    request: Request,
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
):
    """
    Get GDAM Single Bid Book details for the valid delivery date.
    """
    return await gdam_bidding_controller.get_dam_single_bid_book(date)


@public_router.get(
    "/single-bid/trade-book",
    summary="Get GDAM Single Trade Book",
    response_description="GDAM Single Trade Book Response",
)
async def get_gdam_single_trade_book(
    request: Request,
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
):
    """
    Get GDAM Single Trade Book details for the valid delivery date.
    """
    return await gdam_bidding_controller.get_dam_single_trade_book(date)


__all__ = ["public_router"]
