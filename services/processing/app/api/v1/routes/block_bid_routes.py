from typing import Any, Dict, Optional, List
from fastapi import APIRouter, HTTPException, Query, Request, Body
from services.processing.app.controller.realtimemarket_controller import realtimemarket_controller
from pydantic import BaseModel

router = APIRouter(prefix="/processing", tags=["Block Bid"])

# Define request schema
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



@router.post("/submit_for_approval_block_bid", summary="Submit block bid approval data",
    response_description="Returns success status after processing bid data")
async def submit_for_approval_block_bid(post_data: dict = Body(..., description="Submit bid approval data")):
    """
    Submit bid data for approval for multiple blocks and plants.
    ### Request Body Example
    ```json
            {
        "market_type": "RTM",
        "type": "Sell",
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
    return await realtimemarket_controller.submit_for_approval_block_bid(post_data)


@router.get("/block_bid_approval_request_list")
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
    return await realtimemarket_controller.block_bid_approval_request_list(schedule_date)

@router.post("/update_status_block_bid")
async def update_status_block_bid(
    post_data: Dict[str, Any] = Body(
        ..., 
        description="Update block bid approval/rejection status"
    )
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
      "other_remark_rej": ["", "Invalid data"],
      "user_id": "670b5f2f3d9b8f32a62a4211"
    }
    ```
    """
    try:
        result = await realtimemarket_controller.update_status_block_bid(post_data)
        return {"success": True, "message": "Block bid status updated successfully", "data": result}
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
