# app/api/v1/routes/processing/management_snapshot_routes.py
from typing import Optional, List
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Query, HTTPException, status
from pydantic import BaseModel, Field
from ....controller.mnagement_snapshot_controller import management_snapshot_controller

router = APIRouter(prefix="/processing/management_snapshot", tags=["Management Snapshot"])

# ---------- Time (IST) ----------
IST = timezone(timedelta(hours=5, minutes=30))
def now_ist() -> datetime:
    return datetime.now(IST)

# ---------- Schemas ----------
class SDTBlockRow(BaseModel):
    time_block: int = Field(..., ge=1, le=96, example=1)
    comp_act_demand: float = Field(0, example=3450.25)
    comp_udm: float = Field(0, example=120.0)
    total_supply: float = Field(0, example=3600.0)
    GAP: float = Field(0, example=149.75)
    rtm_trade: float = Field(0, example=69.25)
    dam_trade: float = Field(0, example=72.00)

class SDTPayload(BaseModel):
    schedule_date: str = Field(..., example="2025-08-29")
    data: List[SDTBlockRow]

class SDTResponse(BaseModel):
    timestamp: datetime = Field(..., example="2025-08-29T09:30:00+05:30")
    data: SDTPayload

class SnapshotBlockRow(BaseModel):
    time_block: int = Field(..., ge=1, le=96, example=1)
    dam_mcv: Optional[float] = Field(None, example=3463.0)
    rtm_mcv: Optional[float] = Field(None, example=3521.0)
    dam_mcp: Optional[float] = Field(None, example=2889.49)
    rtm_mcp: Optional[float] = Field(None, example=2892.10)

class ManagementSnapshotPayload(BaseModel):
    schedule_date: str = Field(..., example="2025-08-29")
    data: List[SnapshotBlockRow]

class ManagementSnapshotResponse(BaseModel):
    timestamp: datetime = Field(..., example="2025-08-29T09:30:00+05:30")
    data: ManagementSnapshotPayload

class MarketTotals(BaseModel):
    buy: Optional[float] = Field(None, example=6157.40)
    sell: Optional[float] = Field(None, example=12558.00)
    mcp: Optional[float] = Field(None, example=2889.49)
    mcv: Optional[float] = Field(None, example=3463.00)

class MarketOurs(BaseModel):
    buy: Optional[float] = Field(None, example=69.25)    # if traded > 0
    sell: Optional[float] = Field(None, example=0.0)     # abs(traded) if traded < 0
    mcp: Optional[float] = Field(None, example=2889.49)  # copied from Total.mcp
    mcv: Optional[float] = Field(None, example=69.25)    # signed traded

class MarketPair(BaseModel):
    Total: Optional[MarketTotals] = None
    Ours: Optional[MarketOurs] = None

class MOBBlockRow(BaseModel):
    time_block: int = Field(..., ge=1, le=96, example=1)
    DAM: MarketPair
    RTM: MarketPair

class MOBPayload(BaseModel):
    schedule_date: str = Field(..., example="2025-08-31")
    data: List[MOBBlockRow]

class MOBResponse(BaseModel):
    timestamp: datetime = Field(..., example="2025-08-31T09:30:00+05:30")
    data: MOBPayload

# ---------- Endpoint ----------
@router.get(
    "/demand_supply",
    response_model=SDTResponse,
    status_code=status.HTTP_200_OK,
    summary="Supply vs Demand + DAM/RTM trades by time_block",
    response_description=(
        "For a given date and block window, returns comp_act_demand, comp_udm, "
        "total_supply, GAP, and traded volumes split by DAM/RTM. Missing blocks are zero-filled."
    ),
    responses={
        200: {"description": "OK"},
        422: {"description": "Validation error"},
        500: {"description": "Failed to fetch supply-demand-trade snapshot."},
    },
)
async def get_supply_demand_trade(
    schedule_date: Optional[str] = Query(
        default=None,
        description="YYYY-MM-DD. Defaults to today's date in Asia/Kolkata.",
        example="2025-08-29",
    ),
    from_block: Optional[int] = Query(
        default=1, ge=1, le=96,
        description="Time block start (1..96). Default 1.",
        example=1,
    ),
    to_block: Optional[int] = Query(
        default=96, ge=1, le=96,
        description="Time block end (1..96). Default 96.",
        example=96,
    ),
):
    """
    Combines:
    - **serve_supply_table** → `total_supply` (sum of `volume`)
    - **serve_demand_table** → `comp_act_demand`, `comp_udm` (sum if duplicates)
    - **serve_px_bid_trade** → `dam_trade`, `rtm_trade` (sum of `traded` split by `market`)

    Notes:
    - Accepts `schedule_date` stored as a **string** or **BSON Date**.
    - Markets considered in trades: **DAM** and **RTM** (case-insensitive).
    - Fills every requested block with zeros if data is missing.
    """
    try:
        payload = await management_snapshot_controller.demand_supply_snapshot(
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
        )
        return {
            "timestamp": now_ist().isoformat(),
            "data": payload,
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch supply-demand-trade snapshot.")

@router.get(
    "/iex_dam_rtm",
    response_model=ManagementSnapshotResponse,
    status_code=status.HTTP_200_OK,
    summary="DAM/RTM per-block snapshot (default: today IST, all 96 blocks)",
    response_description=(
        "Returns an object with schedule_date and a list of blocks, each with "
        "dam_mcv, rtm_mcv, dam_mcp, rtm_mcp. Missing blocks are gap-filled as nulls."
    ),
    responses={
        200: {"description": "OK"},
        422: {"description": "Validation error"},
        500: {"description": "Failed to fetch management snapshot."},
    },
)
async def iex_dam_rtm_snapshot(
    schedule_date: Optional[str] = Query(
        default=None,
        description="YYYY-MM-DD. Defaults to today's date in Asia/Kolkata.",
        example="2025-08-29",
    ),
    from_block: Optional[int] = Query(
        default=1, ge=1, le=96,
        description="Time block start (1..96). Default 1.",
        example=1,
    ),
    to_block: Optional[int] = Query(
        default=96, ge=1, le=96,
        description="Time block end (1..96). Default 96.",
        example=96,
    ),
):
    """
    Filters **serve_exchange_table** by:
    - schedule_date (string or Date in DB handled gracefully)
    - markets ∈ {DAM, RTM}
    - time_block range (inclusive)

    Always returns each block in the requested range (gap-filled with nulls if not present).
    """
    try:
        payload = await management_snapshot_controller.iex_dam_rtm_snapshot(
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
        )
        return {
            "timestamp": now_ist().isoformat(),
            "data": payload,
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch iex dam rtm snapshot.")



@router.get(
    "/variable_cost",
    response_model=MOBResponse,
    status_code=status.HTTP_200_OK,
    summary="DAM/RTM → {Total,Ours} by time_block",
    response_description=(
        "For each block, returns DAM/RTM with Total (exchange buy/sell/mcp/mcv) "
        "and Ours (derived from serve_px_bid_trade: traded→buy/sell, mcp=Total.mcp, mcv=traded)."
    ),
    responses={
        200: {"description": "OK"},
        422: {"description": "Validation error"},
        500: {"description": "Failed to fetch market book snapshot."},
    },
)
async def get_variable_cost_snapshot(
    schedule_date: Optional[str] = Query(
        default=None,
        description="YYYY-MM-DD. Defaults to today's date in Asia/Kolkata.",
        example="2025-08-31",
    ),
    from_block: Optional[int] = Query(
        default=1, ge=1, le=96,
        description="Time block start (1..96).",
        example=1,
    ),
    to_block: Optional[int] = Query(
        default=96, ge=1, le=96,
        description="Time block end (1..96).",
        example=96,
    ),
    exchange: Optional[str] = Query(
        default=None,
        description='Optional exchange filter (e.g., "IEX"). If omitted, all exchanges are included.',
        example="IEX",
    ),
):
    """
    - **Total** comes from `serve_exchange_table` (buy/sell/mcp/mcv).
    - **Ours** is built from `serve_px_bid_trade`:
      - `buy = traded` if traded > 0, else 0  
      - `sell = abs(traded)` if traded < 0, else 0  
      - `mcp = Total.mcp`  
      - `mcv = traded` (signed)
    - If either market is missing for a block, it’s present with `{"Total": null, "Ours": null}`.
    """
    try:
        payload = await management_snapshot_controller.variable_cost_snapshot(
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
            exchange=exchange,
        )
        return {
            "timestamp": now_ist().isoformat(),
            "data": payload,
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch market book snapshot.")

class MOBRangeResponse(BaseModel):
    timestamp: datetime
    data: List[MOBPayload]  # <- each item is the SAME shape as /variable_cost

@router.get(
    "/variable_cost/range",
    response_model=MOBRangeResponse,
    status_code=status.HTTP_200_OK,
    summary="Variable cost for a date range (per-day payloads unchanged)",
)
async def get_variable_cost_snapshot_range(
    date_from: str = Query(..., example="2025-08-29"),
    date_to: str   = Query(..., example="2025-08-31"),
    from_block: Optional[int] = Query(1, ge=1, le=96),
    to_block: Optional[int]   = Query(96, ge=1, le=96),
    exchange: Optional[str]   = Query(None, example="IEX"),
):
    try:
        days = await management_snapshot_controller.variable_cost_snapshot_range(
            date_from=date_from,
            date_to=date_to,
            from_block=from_block,
            to_block=to_block,
            exchange=exchange,
        )
        return {"timestamp": now_ist().isoformat(), "data": days}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch market book snapshot.")
