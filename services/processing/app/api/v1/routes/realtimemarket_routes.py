from typing import Optional, List
from fastapi import APIRouter, Query, Request, Body
from services.processing.app.controller.realtimemarket_controller import realtimemarket_controller
from pydantic import BaseModel

router = APIRouter(prefix="/processing", tags=["Real-Time-Market"])

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
    block_no: int



@router.get("/rtm-single-bid-garph")
async def get_rtm_single_bid_garph(
    schedule_date: Optional[str] = Query(
        None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata"
    ),
    from_block: int = Query(1, description="Starting time block (default=1)"),
    to_block: int = Query(96, description="Ending time block (default=96)")
):
    """
    Returns market data by exchange for a given schedule_date.
    If no data exists, responds with:
      {"detail": "No data found for the requested schedule_date."}
    """
    return await realtimemarket_controller.rtm_single_bid_graph(schedule_date, from_block, to_block)


@router.get("/rtm-single-bid-table")
async def get_rtm_single_bid_table(
    schedule_date: Optional[str] = Query(
        None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata"
    ),
    from_block: int = Query(1, description="Starting time block (default=1)"),
    to_block: int = Query(96, description="Ending time block (default=96)")
):
    """
    Returns market data by exchange for a given schedule_date.
    If no data exists, responds with:
      {"detail": "No data found for the requested schedule_date."}
    """
    return await realtimemarket_controller.rtm_single_bid_table(schedule_date, from_block, to_block)

@router.get("/approval_request_list")
async def get_approval_request_list(
    schedule_date: Optional[str] = Query(
        None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata"
    ),
    from_block: int = Query(1, description="Starting time block (default=1)"),
    to_block: int = Query(96, description="Ending time block (default=96)")
):
    """
    Returns market data by exchange for a given schedule_date.
    If no data exists, responds with:
      {"detail": "No data found for the requested schedule_date."}
    """
    return await realtimemarket_controller.approval_request_list(schedule_date, from_block, to_block)

@router.post("/bid_creation_post", summary="Create bid data for RTM",
    response_description="Processed bid block data")
async def bid_creation_post(
    post_data: dict = Body(..., description="Bid data for multiple blocks")
):
    """
    Submit bid data for sell/buy types for given time blocks.

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
    # return {"received": post_data}
    return await realtimemarket_controller.BidCreationpost(post_data)

@router.post("/submit_for_approval_post_data", summary="Submit bid approval data",
    response_description="Returns success status after processing bid data")
async def submit_for_approval_post_data(post_data: dict = Body(..., description="Submit bid approval data")):
    """
    Submit bid data for approval for multiple blocks and plants.

    ### Request Body Example
    ```json
    {
      "current_user": "5f158b66cf650f572054ddc2",
      "data": [
        {
          "bid_date": "2025-10-15",
          "time_block": "04:00-04:15_17",
          "start_time": "04:00",
          "end_time": "04:15",
          "block_no": 17,
          "revision_no": 1,
          "session_block": 1,
          "current_user": "5f158b66cf650f572054ddc2",
          "Ex_split_string": "IEX-100_PXIL-0",
          "buy_sell_bid": "sell",
          "buy_sell_bid_type": "sell",
          "selected_action_type": "approved",
          "bid_plant_name": [
            {
              "bid_plant_name": "Plant A",
              "bid_plant_key": "PLANT001",
              "bid_plant_category": "Must-Run",
              "bid_default_volume": 100,
              "bid_volume": 120,
              "bid_price": 2500.0,
              "bid_default_price": 2450.0,
              "rtm_forecast_Price": 2480.0,
              "buy_sell_bid_type": "sell"
            }
          ]
        }
      ]
    }
    ```

    - `current_user`: ID of the user submitting the bids  
    - `data`: List of blocks containing bid data for multiple plants  
    - `bid_plant_name`: Each plant’s bid details including volume, price, and type
    """
    return await realtimemarket_controller.submit_for_approval_post_data(post_data)

@router.post("/submit_for_pre_exchange_data")
async def submit_for_pre_exchange_data(data: List[ReferenceData]):
    """
    Accepts a list of bid data for pre-exchange processing.

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
    
    return await realtimemarket_controller.submitForPreExchangeData(data) # type: ignore

@router.get("/rtm-scheduling_table_calculation")
async def get_rtm_scheduling_table_calculation(
    schedule_date: Optional[str] = Query(
        None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata"
    ),
   
):
    """
    Returns market data by exchange for a given schedule_date.
    If no data exists, responds with:
      {"detail": "No data found for the requested schedule_date."}
    """
    
    return await realtimemarket_controller.rtm_scheduling_cal_table(schedule_date)

