from typing import Optional
from fastapi import APIRouter, Query
from ....controller.marketintgrator_controller import marketintgrator_controller

router = APIRouter(prefix="/processing", tags=["Market-Integrator"])

@router.get("/market-by-exchange-garph")
async def get_market_by_exchange_garph(
    schedule_date: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata")
):
    """
    Returns market data by exchange for a given schedule_date.
    If no data exists, responds with:
      {"detail": "No data found for the requested schedule_date."}
    """
    return await marketintgrator_controller.exchange_garph(schedule_date)

@router.get("/market-overview-garph")
async def get_market_overview_garph(
    schedule_date: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata")
):
    """
    Returns market data by exchange for a given schedule_date.
    If no data exists, responds with:
      {"detail": "No data found for the requested schedule_date."}
    """
    return await marketintgrator_controller.market_overview_garph(schedule_date)


@router.get("/market-by-exchange-table")
async def get_market_by_exchange_table(
    schedule_date: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata"),
    time_block: Optional[int] = Query(None, description="Time block (1–96); optional")
):
    """
    Returns market data by exchange for a given schedule_date.
    If no data exists, responds with:
      {"detail": "No data found for the requested schedule_date."}
    """
    return await marketintgrator_controller.exchange_table(schedule_date, time_block)

@router.get("/market-exchange-bidding-selected-summary-table")
async def get_market_exchange_bidding_selected_summary_table(
    schedule_date: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to today's date in Asia/Kolkata"),
    time_block: Optional[int] = Query(None, description="Time block (1–96); optional")
):
    """
    Returns market data by exchange for a given schedule_date.
    If no data exists, responds with:
      {"detail": "No data found for the requested schedule_date."}
    """
    return await marketintgrator_controller.exchange_bidding_selected_summary_table(schedule_date, time_block)
