from typing import Optional, List
from fastapi import APIRouter, Query, Request, Body
from services.processing.app.controller.dayaheadmarket_controller import dayaheadmarket_controller
from pydantic import BaseModel

router = APIRouter(prefix="/processing", tags=["Day-Ahead-Market"])

# Define request schema
class PlantModel(BaseModel):
    bid_plant_name: str
    bid_plant_key: str
    bid_plant_category: str
    bid_default_volume: int
    bid_volume: int
    bid_price: float
    bid_default_price: float
    dayahead_forecast_Price: float
    buy_sell_bid_type: str

class BlockData(BaseModel):
    bid_date: str
    time_block: str
    bid_volume: int
    bid_price: float
    buy_sell_bid_type: str
    bid_plant_name: str
    bid_plant_key: str
    bid_plant_category: str
    bid_default_volume: int
    bid_default_price: float
    dayahead_forecast_Price: float

class BidCreationRequest(BaseModel):
    sell_buy_bid_type: List[str]
    time_block_range: List[str]
    bid_volume: List[List[str]]
    bid_price: List[List[str]]
    Ex_split_string: List[str]
    approve_by: List[str]
    reference_number: List[str]
    bid_status: List[str]
    remarks: List[str]
    bidingDate: str

class ApprovalRequest(BaseModel):
    schedule_date: str
    from_block: int
    to_block: int
    plant_data: List[PlantModel]
    block_data: List[BlockData]

class ReferenceData(BaseModel):
    reference_number: str
    bid_status: str
    Remarks: str
    action_by: str
    block_no: int

@router.get("/dayahead-single-bid-garph")
async def get_dayahead_single_bid_garph(
    schedule_date: Optional[str] = Query(
        None, description="YYYY-MM-DD; defaults to tomorrow's date for dayahead market"
    ),
    from_block: int = Query(1, description="Starting time block (default=1)"),
    to_block: int = Query(96, description="Ending time block (default=96)")
):
    """
    Returns dayahead market graph data for a given schedule_date.
    For dayahead market, shows all 96 blocks and uses tomorrow's date by default.
    """
    return await dayaheadmarket_controller.dayahead_single_bid_graph(schedule_date, from_block, to_block)


@router.get("/dayahead-single-bid-table")
async def get_dayahead_single_bid_table(
    schedule_date: Optional[str] = Query(
        None, description="YYYY-MM-DD; defaults to tomorrow's date for dayahead market"
    ),
    from_block: int = Query(1, description="Starting time block (default=1)"),
    to_block: int = Query(96, description="Ending time block (default=96)")
):
    """
    Returns dayahead market data by exchange for a given schedule_date.
    For dayahead market, shows all 96 blocks and uses tomorrow's date by default.
    If no data exists, responds with:
      {"detail": "No data found for the requested schedule_date."}
    """
    return await dayaheadmarket_controller.dayahead_single_bid_table(schedule_date, from_block, to_block)

@router.get("/dayahead-approval_request_list")
async def get_dayahead_approval_request_list(
    schedule_date: Optional[str] = Query(
        None, description="YYYY-MM-DD; defaults to tomorrow's date for dayahead market"
    ),
    from_block: int = Query(1, description="Starting time block (default=1)"),
    to_block: int = Query(96, description="Ending time block (default=96)")
):
    """
    Returns dayahead market approval request list for a given schedule_date.
    For dayahead market, shows all 96 blocks and uses tomorrow's date by default.
    """
    return await dayaheadmarket_controller.approval_request_list(schedule_date, from_block, to_block)

@router.post("/dayahead-bid_creation_post", summary="Create bid data for Day-Ahead Market",
    response_description="Processed bid block data")
async def dayahead_bid_creation_post(
    post_data: dict = Body(..., description="Bid data for multiple blocks")
):
    """
    Submit bid data for sell/buy types for given time blocks in dayahead market.

    ### Request Body Example
    ```json
    {
      "sell_buy_bid_type": ["sell", "sell"],
      "time_block_range": ["04:00-04:15_17_2025-10-15", "04:15-04:30_18_2025-10-15"],
      "bid_volume_17": ["196", "119", "111"],
      "bid_price_17": ["2307", "2548", "2569"],
      "Ex_split_string_17": "IEX-100_PXIL-0",
      "bid_volume_18": ["196", "119", "111"],
      "bid_price_18": ["2307", "2548", "2569"],
      "Ex_split_string_18": "IEX-100_PXIL-0",
      "approve_by": ["5f158b66cf650f572054ddc2", "5f158b66cf650f572054ddc2"],
      "reference_number": ["68ee9bcbd35c84056106b0e3"],
      "bid_status": ["approved", "approved"],
      "remarks": ["This data is correct"],
      "bidingDate": "15-10-2025"
    }
    ```
    """
    return await dayaheadmarket_controller.BidCreationpost(post_data)

@router.post("/dayahead-submit_for_approval_post_data", summary="Submit dayahead bid approval data",
    response_description="Returns success status after processing bid data")
async def dayahead_submit_for_approval_post_data(
    request: dict = Body(..., description="Approval request data")
):
    """
    Submit dayahead market bid data for approval.
    
    ### Request Body Example
    ```json
    {
      "schedule_date": "2025-10-16",
      "from_block": 1,
      "to_block": 96,
      "plant_data": [...],
      "block_data": [...]
    }
    ```
    """
    return await dayaheadmarket_controller.submit_for_approval_post_data(request)

@router.get("/dayahead-scheduling-cal-table")
async def get_dayahead_scheduling_cal_table(
    schedule_date: Optional[str] = Query(
        None, description="YYYY-MM-DD; defaults to tomorrow's date for dayahead market"
    )
):
    """
    Returns dayahead market scheduling calculation table for a given schedule_date.
    For dayahead market, uses tomorrow's date by default.
    """
    return await dayaheadmarket_controller.dayahead_scheduling_cal_table(schedule_date)

@router.post("/dayahead-submitForPreExchangeData", summary="Submit dayahead pre-exchange data",
    response_description="Returns success status after processing pre-exchange data")
async def dayahead_submitForPreExchangeData(
    data: List[ReferenceData]
):
    """
    Accepts a list of bid data for pre-exchange processing in dayahead market.

    ### Example Request
    ```json
    [
      {
        "reference_number": "68ee9bcbd35c84056106b0e3",
        "bid_status": "approved",
        "Remarks": "This bid is correct",
        "action_by": "5f158b66cf650f572054ddc2",
        "block_no": 17
      }
    ]
    ```

    - `reference_number`: Unique bid ID  
    - `bid_status`: approved / pending / rejected  
    - `Remarks`: Additional notes about the bid  
    - `action_by`: User ID performing the action  
    - `block_no`: Time block number
    """
    return await dayaheadmarket_controller.submitForPreExchangeData(data)
