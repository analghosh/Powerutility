from typing import Optional
from fastapi import APIRouter, Query
from services.processing.app.controller.rtm_scheduling_controller import rtm_scheduling_controller

router = APIRouter(prefix="/processing", tags=["RTM Scheduling"])

@router.get("/rtm-scheduling-table")
def get_rtm_scheduling_table():
    """
    Returns a single day's (96 blocks) renewable availability.
    If no data exists for the day, responds with:
      {"detail": "No data in current 4-day window <D-2>..<D+1>."}
    """
    return rtm_scheduling_controller.rtm_scheduling_table()