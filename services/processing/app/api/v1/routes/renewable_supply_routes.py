from typing import Optional
from fastapi import APIRouter, Query
from ....controller.renewable_supply_controller import renewable_supply_controller

router = APIRouter(prefix="/processing", tags=["Renewable-Supply"])

@router.get("/renewable-supply")
async def get_renewable_availability_day(
    date: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata")
):
    """
    Returns a single day's (96 blocks) renewable availability.
    If no data exists for the day, responds with:
      {"detail": "No data in current 4-day window <D-2>..<D+1>."}
    """
    return await renewable_supply_controller.renewable_availability_by_day(date)


@router.get("/renewable-schedule")
async def get_renewable_schedule_day(
    date: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata")
):
    """
    Returns grouped SCHEDULE for a day (both technologies: solar & wind; both types: IDF & DAF).
    Response shape:
      {
        "data": [
          { "schedule_date": "...", "technology": "solar|wind", "type": "IDF|DAF",
            "data": [ { "time_block": 1, "scheduled_volume": <float> }, ... x96 ] },
          ... up to 4 documents ...
        ]
      }
    If no data exists for the day, responds with:
      {"detail": "No schedule data in current 4-day window <D-2>..<D+1>."}
    """
    return await renewable_supply_controller.renewable_schedule_by_day(date)
