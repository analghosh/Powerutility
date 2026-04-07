from typing import Optional
from fastapi import APIRouter, Query, UploadFile, File
from ....controller.limit_value_controller import limit_value_controller

router = APIRouter(prefix="/processing", tags=["Limit-Value"])

@router.post("/limit-value/upload")
async def get_market_by_exchange_garph(
    file: UploadFile = File(...)
):
    """
    Returns market data by exchange for a given schedule_date.
    If no data exists, responds with:
      {"detail": "No data found for the requested schedule_date."}
    """
    return await limit_value_controller.import_limit_value(file)

@router.get("/limit-value")
async def list_limit_values(
    schedule_date: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata"),
   ):
    
    return await limit_value_controller.get_limit_values( schedule_date) 