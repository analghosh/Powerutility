from typing import Any, Dict, Optional, List, Union
from fastapi import APIRouter, HTTPException, Query, Request, Body, Depends
from ...controller.rtm_bidding_controller import rtm_bidding_controller
from ...controller.dam_block_bid_controller import dam_block_bid_controller
from pydantic import BaseModel, Field
from ...dependencies import get_user_context
from ....core.dependencies import get_audit_logger

from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger

router = APIRouter(prefix="/block-bid", tags=["Block Bid V2"])

# Day-ahead markets share one block-bid flow: IEX exposes an identical block-bid
# API for each under its own path prefix (DAM 2.2 §9.15, GDAM 2.1 §6 item 13).
# RTM has a different contract and keeps its own controller.
DAY_AHEAD_BLOCK_MARKETS = {"DAM", "GDAM", "HPDAM"}

# ── Legacy models (kept for backward compatibility) ──────────────────────────
class PlantModel(BaseModel):
    bid_plant_name: str
    bid_plant_key: str
    bid_plant_category: str
    bid_default_volume: int
    bid_volume: int
    bid_price: float
    bid_default_price: float
    rtm_forecast_Price: float
    buy_sell_bid_type: str

class BlockData(BaseModel):
    bid_date: str
    time_block: str
    start_time: str
    end_time: str
    block_no: int
    revision_no: int
    session_block: int
    current_user: str
    Ex_split_string: str
    buy_sell_bid: str
    buy_sell_bid_type: str
    selected_action_type: str
    bid_plant_name: List[PlantModel]

class RequestModel(BaseModel):
    current_user: str
    data: List[BlockData]

class ReferenceData(BaseModel):
    reference_number: str
    bid_status: str
    Remarks: str
    action_by: str
    block_no: str

# ── Request models for Swagger / frontend ────────────────────────────────────

class SubmitForApprovalRequest(BaseModel):
    model_config = {"populate_by_name": True}

    market_type: List[str]        # "RTM", "DAM" or "GDAM"
    type: str = "Sell"              # "Sell" or "Buy"
    dates: str                      # "DD-MM-YYYY"
    trade_session: Optional[List[Union[str, int]]] = None   # RTM only; omit for DAM
    from_: Optional[List[str]] = Field(None, alias="from")
    to: Optional[List[str]] = None
    quantum: List[Union[str, int, float]] = []
    bid_price: List[Union[str, int, float]] = []
    iex_price: List[Optional[float]] = []
    pxil_price: List[Optional[float]] = []
    number_of_bids: List[Union[str, int]] = []
    exchange: List[str]
    selected_for: List[str]
    ocf: Optional[List[Optional[Union[str, int, float]]]] = None  # DAM only
    ocf_config: Optional[List[Optional[Union[str, int, float]]]] = None  # DAM only

class WithdrawRequest(BaseModel):
    bid_id: str
    remark: str = ""
    market_type: str = "RTM"        # "RTM", "DAM" or "GDAM"

class CancelRequest(BaseModel):
    schedule_date: str              # "YYYY-MM-DD"
    order_ids: List[int]
    market_type: str = "RTM"        # "RTM", "DAM" or "GDAM"

class SubmitToExchangeRequest(BaseModel):
    schedule_date: str              # "YYYY-MM-DD"
    market_type: str = "RTM"        # "RTM", "DAM" or "GDAM"

class UpdateStatusRequest(BaseModel):
    id: List[str]
    status: List[Optional[str]]
    reason: List[Optional[str]] = []
    rej_reason: List[Optional[str]] = []
    other_remark: List[Optional[str]] = []
    other_remark_rej: List[Optional[str]] = []



@router.post("/withdraw", summary="Mark block bid as withdrawn (local only)")
async def mark_block_bid_withdrawal(
    post_data: WithdrawRequest,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Step 1 of cancellation: mark the bid as Cancelled in block_bid_current_data.
    No exchange API call — just updates local DB with withdrawal_remark and withdrawal_time.

    ### Request Body
    ```json
    { "bid_id": "671b3a13d9e2a1f57dbe9134", "remark": "Price not favourable" }
    ```
    """
    user_id        = user_context.get("user_id", "anonymous")
    username       = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    post_data = post_data.model_dump(by_alias=True)
    post_data["username"] = username
    market_type = post_data.get("market_type", "RTM").upper()
    audit_logger.log_user_action(
        service="processing", user_id=user_id, action="block_bid_withdraw",
        message=f"User {username} marking bid {post_data.get('bid_id')} for withdrawal [{market_type}]",
        username=username, correlation_id=correlation_id,
    )
    if market_type in DAY_AHEAD_BLOCK_MARKETS:
        return await dam_block_bid_controller.mark_withdrawal(post_data)
    return await rtm_bidding_controller.mark_block_bid_withdrawal(post_data)


@router.post("/cancel", summary="Cancel block bid on IEX exchange")
async def cancel_block_bid(
    post_data: CancelRequest,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Step 2 of cancellation: submit cancellation to IEX exchange for the given order_ids.

    For each order_id:
    - Retrieves `last_updated_time` from `dicision_rtm_block_bid_iex_response`
    - POSTs to `POST /rtm/blockbids/cancel/{user_code},{order_id}`
    - On success (error==0): updates `dicision_rtm_block_bid_iex_response` status → Cancelled

    ### Request Body
    ```json
    { "schedule_date": "2025-10-25", "order_ids": [12345, 67890] }
    ```
    """
    user_id        = user_context.get("user_id", "anonymous")
    username       = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    post_data = post_data.model_dump()
    post_data["username"] = username
    market_type = post_data.get("market_type", "RTM").upper()
    audit_logger.log_user_action(
        service="processing", user_id=user_id, action="block_bid_cancel",
        message=f"User {username} cancelling {market_type} block bids {post_data.get('order_ids')} for {post_data.get('schedule_date')}",
        username=username, correlation_id=correlation_id,
    )
    if market_type in DAY_AHEAD_BLOCK_MARKETS:
        result = await dam_block_bid_controller.cancel_on_exchange(post_data, correlation_id, market=market_type)
    else:
        result = await rtm_bidding_controller.cancel_block_bid(post_data, correlation_id)
    audit_logger.log_user_action(
        service="processing", user_id=user_id, action="block_bid_cancel",
        message=f"{market_type} block bid cancellation result: {result.get('message')}",
        username=username, correlation_id=correlation_id,
    )
    return result


@router.post("/submit-for-approval", summary="Submit block bid approval data",
    response_description="Returns success status after processing bid data")
async def submit_for_approval_block_bid(post_data: SubmitForApprovalRequest,
                                        user_context:Dict[str, Any] = Depends(get_user_context),
                                        audit_logger: AuditLogger = Depends(get_audit_logger)):
    """
    Submit bid data for approval for multiple blocks and plants.
    ### Request Body Example
    ```json
            {
        "market_type": "RTM",
        "type": "Sell",
        "exchange": "IEX",
        "selected_for": "Bid",
        "dates": "25-10-2025",
        "trade_session": [
            "48"
        ],
        "from": [
            "23:30"
        ],
        "to": [
            "23:45"
        ],
        "quantum": [
            "12"
        ],
        "bid_price": [
            "12"
        ],
        "iex_price": [
            null
        ],
        "pxil_price": [
            null
        ],
        "number_of_bids": [
            "12"
        ],
        "submit": "Submit"
        }
    ```

    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        # Log user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="approval",
            message="User submitted block bid for approval",
            username=username,
            correlation_id=correlation_id,
        )
        # Add user information to post_data for service to use
        post_data = post_data.model_dump(by_alias=True)
        post_data["user_id"] = user_id
        post_data["current_user"] = username
        post_data["username"] = username
        data =  await rtm_bidding_controller.submit_for_approval_block_bid(post_data)
        action_msg = ""
        if(post_data.get("market_type") == "RTM"):
            action_msg = "Approver will approve RTM Block Bid"
        else:
            action_msg = "Approver will approve DAM Block Bid"
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="approval",
            message=action_msg,
            username=username,
            correlation_id=correlation_id,
            record_count=len(data.get("submitted_bids", [])) if isinstance(data, dict) else 0,
        )
        return {"success": True, "message": "Block bid submitted for approval successfully", "data": data}
    except HTTPException as e:
        raise e
    except Exception as e:
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="approval",
            message=f"Error submitting block bid for approval: {e}",
            username=username,
            correlation_id=correlation_id,
        )
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/approval-list")
async def get_block_bid_approval_request_list(
    schedule_date: Optional[str] = Query(
        None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata"
    ),
    
):
    """
    Returns market data by exchange for a given schedule_date.
    If no data exists, responds with:
      {"detail": "No data found for the requested schedule_date."}
    """
    return await rtm_bidding_controller.block_bid_approval_request_list(schedule_date)

@router.get("/download-excel/iex", summary="Download approved block bids as IEX Excel")
async def download_iex_excel(
    schedule_date: str = Query(..., description="Bidding date in YYYY-MM-DD format"),
    market_type: str = Query("RTM", description="Market type: RTM, DAM or GDAM"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Download approved block bids as an IEX-formatted Excel file.
    Each bid is expanded into number_of_bids rows.
    Quantity = (quantum × -1 × iex_split%) / 100
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    audit_logger.log_user_action(
        service="processing",
        user_id=user_id,
        action="download_iex_excel",
        message=f"User {username} downloading IEX Excel for {schedule_date} [{market_type}]",
        username=username,
        correlation_id=correlation_id,
    )
    return await rtm_bidding_controller.download_iex_excel(schedule_date, market_type)


@router.post("/submit-to-exchange", summary="Submit approved block bids to IEX exchange")
async def submit_block_bid_to_exchange(
    post_data: SubmitToExchangeRequest,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Submit all Approved block bids for schedule_date to IEX exchange.
    - Validates total number_of_bids <= 60
    - Builds bid_details[], posts to exchange API
    - Stores exchange response in dicision_rtm_block_bid_iex_response / dicision_dam_block_bid_iex_response
    - Updates block_bid_current_data status to Submitted
    """
    user_id        = user_context.get("user_id", "anonymous")
    username       = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    biding_date    = post_data.schedule_date
    market_type    = post_data.market_type.upper()
    audit_logger.log_user_action(
        service="processing",
        user_id=user_id,
        action="submit_block_bid_to_exchange",
        message=f"User {username} submitting {market_type} block bids to exchange for {biding_date}",
        username=username,
        correlation_id=correlation_id,
    )

    if market_type in DAY_AHEAD_BLOCK_MARKETS:
        result = await dam_block_bid_controller.submit_to_exchange(biding_date, username, correlation_id, market=market_type)
    else:
        result = await rtm_bidding_controller.submit_block_bid_to_exchange(biding_date, username, correlation_id)

    audit_logger.log_user_action(
        service="processing",
        user_id=user_id,
        action="submit_block_bid_to_exchange",
        message=f"{market_type} block bid exchange submission complete for {biding_date}: {result.get('message')}",
        username=username,
        correlation_id=correlation_id,
    )
    return result


@router.get("/bid-submission-detail", summary="Get block bid submission details")
async def get_block_bid_submission_details(
    schedule_date: str = Query(..., description="Bidding date in YYYY-MM-DD format"),
):
    """
    Get block bid submission details grouped by correlation_id for a given date.
    Reads from iex_integration_apilog (bid_type=block-bid, method=submitBlockBid).
    """
    return await rtm_bidding_controller.get_block_bid_submission_details(schedule_date)


@router.get("/bid-book", summary="Get block bid book (exchange responses)")
async def get_block_bid_book(
    schedule_date: str = Query(..., description="Bidding date in YYYY-MM-DD format"),
):
    """
    Get block bid book — all exchange responses stored in dicision_rtm_block_bid_iex_response
    for the given schedule_date.
    """
    return await rtm_bidding_controller.get_block_bid_book(schedule_date)


@router.get("/trade-book", summary="Get block bid trade book (executed/provisional)")
async def get_block_bid_trade_book(
    schedule_date: str = Query(..., description="Bidding date in YYYY-MM-DD format"),
):
    """
    Get executed and provisional block bids for a schedule date.
    Source: rtm_block_bid_book_status (kept live by scheduler every 15 min).
    """
    return await rtm_bidding_controller.get_block_bid_trade_book(schedule_date=schedule_date)


@router.post("/update-status")
async def update_status_block_bid(
    post_data: UpdateStatusRequest,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    ✅ Update block bid approval/rejection status
    
    ### Example Request
    ```json
    {
      "id": ["671b3a13d9e2a1f57dbe9134", "671b3a13d9e2a1f57dbe9135"],
      "status": ["Approved", "Rejected"],
      "reason": ["valid data", "other"],
      "rej_reason": ["", "other"],
      "other_remark": ["", "Manual approval"],
      "other_remark_rej": ["", "Invalid data"]
    }
    ```
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    current_user = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Add user information to post_data for service to use
        post_data = post_data.model_dump()
        post_data["user_id"] = user_id
        post_data["current_user"] = current_user
        post_data["username"] = username
        
        # Log user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="approval",
            message="User updating block bid status",
            username=username,
            correlation_id=correlation_id,
        )
        
        result = await rtm_bidding_controller.update_status_block_bid(post_data)
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="approval",
            message="Block bid status updated successfully",
            username=username,
            correlation_id=correlation_id,
        )
        
        return {"success": True, "message": "Block bid status updated successfully", "data": result}
    except HTTPException as e:
        raise e
    except Exception as e:
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="approval",
            message=f"Error updating block bid status: {e}",
            username=username,
            correlation_id=correlation_id,
        )
        raise HTTPException(status_code=500, detail=str(e))
