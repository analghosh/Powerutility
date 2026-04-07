from typing import List, Optional, Literal, Union, Dict
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Query, HTTPException, status
from pydantic import BaseModel, Field

from ....controller.forecastanalysis_controller import demand_controller

router = APIRouter(prefix="/processing/forecast_analysis", tags=["Forecast Analysis"])

# ---------- Time (IST) ----------
IST = timezone(timedelta(hours=5, minutes=30))
def now_ist() -> datetime:
    return datetime.now(IST)

# ---------- Schemas ----------
class DemandMetrics(BaseModel):
    comp_act_demand: Optional[float] = Field(None, example=7012.5)
    comp_udm: Optional[float] = Field(None, example=6903.1)        
    forecast: Optional[float] = Field(..., example=7321.48)
    MAPE: str = Field(..., example="6.06%")

class DemandFlatItem(BaseModel):
    schedule_date: str = Field(..., example="2025-08-27")
    time_block: int = Field(..., ge=1, le=96, example=1)
    data: DemandMetrics

class DemandBlockItem(BaseModel):
    time_block: int = Field(..., ge=1, le=96, example=1)
    data: DemandMetrics

class DemandDateGroup(BaseModel):
    schedule_date: str = Field(..., example="2025-08-27")
    blocks: List[DemandBlockItem]

class DemandDataGrouped(BaseModel):
    result: List[DemandDateGroup]

class DemandDataFlat(BaseModel):
    result: List[DemandFlatItem]

class DemandResponseGrouped(BaseModel):
    timestamp: datetime = Field(..., example="2025-08-27T09:30:00+05:30")
    data: DemandDataGrouped

class DemandResponseFlat(BaseModel):
    timestamp: datetime = Field(..., example="2025-08-27T09:30:00+05:30")
    data: DemandDataFlat



# ---------- Schemas (Last 7 days) ----------
class Last7DaysRow(BaseModel):
    schedule_date: str = Field(..., example="2025-08-27")
    # time_block: int    = Field(..., ge=1, le=96, example=45)
    peak_demand: float = Field(..., example=8123.45)
    average_demand: float = Field(..., example=7050.12)

class Last7DaysResponse(BaseModel):
    timestamp: datetime = Field(..., example="2025-08-27T09:30:00+05:30")
    data: List[Last7DaysRow]



# ---------- Endpoint ----------
@router.get(
    "/demand_graph",
    response_model=Union[DemandResponseGrouped, DemandResponseFlat],
    status_code=status.HTTP_200_OK,
    summary="Demand blocks (per-day block window, grouped by date or flat)",
    response_description=(
        "Returns per-block demand metrics for one or more dates. "
        "Filter by market(s), zone(s), date range, and per-day block window (1–96). "
        "Set group_by_date=true (default) for grouped per-date result; "
        "or group_by_date=false for a flat list with schedule_date on each row."
    ),
    responses={
        200: {"description": "OK"},
        422: {"description": "Validation error"},
        500: {"description": "Failed to fetch demand blocks."},
    },
)
async def demand_blocks(
    market: Optional[List[Literal["IDF", "DAF"]]] = Query(
        default=None,
        description='Market filter. Defaults to ["IDF"] if omitted. '
                    'If your documents use only "type", we fallback to that field.',
        
    ),
    zone: Optional[List[str]] = Query(
        default=None,
        description='Zone filter (case-insensitive). Defaults to ["PUDUCHERRY"] if omitted. '
                    'Example: ?zone=North&zone=South',
        
    ),
    date_from: Optional[str] = Query(
        default=None,
        description="Start date (YYYY-MM-DD). Defaults to today (IST) if omitted.",
        
    ),
    date_to: Optional[str] = Query(
        default=None,
        description="End date (YYYY-MM-DD). Defaults to same as date_from.",
        
    ),
    from_block: Optional[int] = Query(
        default=1,
        ge=1,
        le=96,
        description="Per-day block window start (1..96). Applied to each date. Default 1.",
        example=1,
    ),
    to_block: Optional[int] = Query(
        default=96,
        ge=1,
        le=96,
        description="Per-day block window end (1..96). Applied to each date. Default 96.",
        example=96,
    ),
    group_by_date: bool = Query(
        default=True,
        description="If true, returns one object per date with its blocks; "
                    "if false, returns a flat list with schedule_date per row.",
        example=True,
    ),
):
    """
    Defaults when omitted:
    - market -> ["IDF"]
    - zone   -> ["PUDUCHERRY"]
    - date_from -> today (IST)
    - date_to   -> date_from (same day)
    - from_block/to_block -> 1..96
    Sorting: schedule_date ASC, time_block ASC.
    """
    try:
        payload = demand_controller.demand_blocks(
            markets=market,
            date_from=date_from,
            date_to=date_to,
            from_block=from_block,
            to_block=to_block,
            group_by_date=group_by_date,
            zones=zone,   # pass through
        )
        return {
            "timestamp": now_ist().isoformat(),
            "data": payload,
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch demand blocks.")







# ---------- Schemas (Curve API) ----------
class DemandCurveRow(BaseModel):
    count: int = Field(..., example=1, description="Rank position when rows are sorted by comp_act_demand ascending")
    comp_act_demand: float = Field(..., example=7000.0, description="Block-level comp_act_demand after collapsing duplicates")
    percentage: float = Field(..., example=0.52, description="count / denominator × 100 (rounded to 2 decimals)")

class DemandCurveMeta(BaseModel):
    markets: List[str] = Field(..., example=["IDF"])
    zones: List[str] = Field(..., example=["PUDUCHERRY"])
    date_from: str = Field(..., example="2025-08-26")
    date_to: str = Field(..., example="2025-08-27")
    from_block: int = Field(..., example=1)
    to_block: int = Field(..., example=96)
    denominator: Literal["matched", "expected"] = Field(..., example="expected")
    expected_total: Optional[int] = Field(
        None,
        example=192,
        description="days_in_range × slots_per_day (present only when denominator=expected)",
    )

class DemandCurvePayload(BaseModel):
    result: List[DemandCurveRow]
    meta: DemandCurveMeta

class DemandCurveResponse(BaseModel):
    timestamp: datetime = Field(..., example="2025-08-27T09:30:00+05:30")
    data: DemandCurvePayload


# ---------- Endpoint: Demand curve (rank by comp_act_demand) ----------
@router.get(
    "/curve/demand",
    response_model=DemandCurveResponse,
    status_code=status.HTTP_200_OK,
    summary="Demand curve (rank by comp_act_demand across selected dates/blocks)",
    response_description=(
        "Returns rows sorted by comp_act_demand ascending with running `count` and `percentage`.\n"
        "percentage is computed as:\n"
        " - denominator=matched  -> count / totalMatchedRows × 100\n"
        " - denominator=expected -> count / (days × slotsPerDay) × 100"
    ),
    responses={
        200: {"description": "OK"},
        422: {"description": "Validation error"},
        500: {"description": "Failed to compute demand curve."},
    },
)
async def demand_curve(
    market: Optional[List[Literal["IDF", "DAF"]]] = Query(
        default=None,
        description='Market filter. Defaults to ["IDF"] if omitted. '
                    'If your documents use only "type", the service falls back to that field.',
        example=["IDF"],
    ),
    zone: Optional[List[str]] = Query(
        default=None,
        description='Zone filter (case-insensitive). Defaults to ["PUDUCHERRY"] if omitted.',
        example=["PUDUCHERRY"],
    ),
    date_from: Optional[str] = Query(
        default=None,
        description="Start date (YYYY-MM-DD). Defaults to today (IST) if omitted.",
        example="2025-08-26",
    ),
    date_to: Optional[str] = Query(
        default=None,
        description="End date (YYYY-MM-DD). Defaults to same as date_from.",
        example="2025-08-27",
    ),
    from_block: Optional[int] = Query(
        default=1,
        ge=1,
        le=96,
        description="Per-day block window start (1..96). Applied to each date. Default 1.",
        example=1,
    ),
    to_block: Optional[int] = Query(
        default=96,
        ge=1,
        le=96,
        description="Per-day block window end (1..96). Applied to each date. Default 96.",
        example=96,
    ),
    denominator: Literal["matched", "expected"] = Query(
        default="matched",
        description=(
            "How to compute percentage:\n"
            "• matched  = count / totalMatchedRows × 100\n"
            "• expected = count / (days × slotsPerDay) × 100"
        ),
        example="expected",
    ),
):
    """
    ### What this API does
    - Filters **serve_demand_table** by market(s), zone(s), date range, and per-day time_block window.
    - Collapses duplicates per (schedule_date, time_block), summing **comp_act_demand**.
    - Sorts rows by **comp_act_demand ASC**.
    - Computes:
      - `count` as the 1-based rank (running count).
      - `percentage`:
        - **matched**  → `count / totalMatchedRows × 100`
        - **expected** → `count / (days × slotsPerDay) × 100`, where `slotsPerDay = to_block - from_block + 1`.

    ### Typical use
    Build a demand curve and show progress across cumulative blocks.
    """
    try:
        payload = demand_controller.demand_curve(
            markets=market,
            date_from=date_from,
            date_to=date_to,
            from_block=from_block,
            to_block=to_block,
            denominator=denominator,
            zones=zone,
        )
        return {
            "timestamp": now_ist().isoformat(),
            "data": payload,
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to compute demand curve.")
    



# ---------- Schemas (MAPE Curve API) ----------
class MapeCurveRow(BaseModel):
    count: int = Field(..., example=1, description="Rank position when rows are sorted by MAPE ascending")
    MAPE: str = Field(..., example="6.12%", description="Average MAPE for the block (after collapsing duplicates)")
    percentage: float = Field(..., example=0.52, description="count / denominator × 100 (rounded to 2 decimals)")

class MapeCurveMeta(BaseModel):
    markets: List[str] = Field(..., example=["IDF"])
    zones: List[str] = Field(..., example=["PUDUCHERRY"])
    date_from: str = Field(..., example="2025-08-26")
    date_to: str = Field(..., example="2025-08-27")
    from_block: int = Field(..., example=1)
    to_block: int = Field(..., example=96)
    denominator: Literal["matched", "expected"] = Field(..., example="expected")
    expected_total: Optional[int] = Field(
        None,
        example=192,
        description="days_in_range × slots_per_day (present only when denominator=expected)",
    )

class MapeCurvePayload(BaseModel):
    result: List[MapeCurveRow]
    meta: MapeCurveMeta

class MapeCurveResponse(BaseModel):
    timestamp: datetime = Field(..., example="2025-08-27T09:30:00+05:30")
    data: MapeCurvePayload


# ---------- Endpoint: MAPE curve (rank by MAPE) ----------
@router.get(
    "/curve/mape",
    response_model=MapeCurveResponse,
    status_code=status.HTTP_200_OK,
    summary="MAPE curve (rank by average MAPE across selected dates/blocks)",
    response_description=(
        "Returns rows sorted by MAPE ascending with running `count` and `percentage`.\n"
        "percentage is computed as:\n"
        " - denominator=matched  -> count / totalMatchedRows × 100\n"
        " - denominator=expected -> count / (days × slotsPerDay) × 100"
    ),
    responses={
        200: {"description": "OK"},
        422: {"description": "Validation error"},
        500: {"description": "Failed to compute MAPE curve."},
    },
)
async def mape_curve(
    market: Optional[List[Literal["IDF", "DAF"]]] = Query(
        default=None,
        description='Market filter. Defaults to ["IDF"] if omitted. '
                    'If your documents use only "type", the service falls back to that field.',
        example=["IDF"],
    ),
    zone: Optional[List[str]] = Query(
        default=None,
        description='Zone filter (case-insensitive). Defaults to ["PUDUCHERRY"] if omitted.',
        example=["PUDUCHERRY"],
    ),
    date_from: Optional[str] = Query(
        default=None,
        description="Start date (YYYY-MM-DD). Defaults to today (IST) if omitted.",
        example="2025-08-26",
    ),
    date_to: Optional[str] = Query(
        default=None,
        description="End date (YYYY-MM-DD). Defaults to same as date_from.",
        example="2025-08-27",
    ),
    from_block: Optional[int] = Query(
        default=1,
        ge=1,
        le=96,
        description="Per-day block window start (1..96). Applied to each date. Default 1.",
        example=1,
    ),
    to_block: Optional[int] = Query(
        default=96,
        ge=1,
        le=96,
        description="Per-day block window end (1..96). Applied to each date. Default 96.",
        example=96,
    ),
    denominator: Literal["matched", "expected"] = Query(
        default="matched",
        description=(
            "How to compute percentage:\n"
            "• matched  = count / totalMatchedRows × 100\n"
            "• expected = count / (days × slotsPerDay) × 100"
        ),
        example="expected",
    ),
):
    """
    ### What this API does
    - Filters **serve_demand_table** by market(s), zone(s), date range, and per-day time_block window.
    - Parses **MAPE** strings (handles \"\", \"null\", and \"%\" suffix).
    - Collapses duplicates per (schedule_date, time_block), averaging MAPE within each block.
    - Sorts rows by **MAPE ASC**.
    - Computes:
      - `count` using `$documentNumber` (1-based rank in that order).
      - `percentage`:
        - **matched**  → `count / totalMatchedRows × 100`
        - **expected** → `count / (days × slotsPerDay) × 100`, where `slotsPerDay = to_block - from_block + 1`.
    """
    try:
        payload = demand_controller.mape_curve(
            markets=market,
            date_from=date_from,
            date_to=date_to,
            from_block=from_block,
            to_block=to_block,
            denominator=denominator,
            zones=zone,
        )
        return {
            "timestamp": now_ist().isoformat(),
            "data": payload,
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to compute MAPE curve.")
    


class UnifiedMeta(BaseModel):
    markets: List[str]
    zones: List[str]
    date_from: str
    date_to: str
    from_block: int
    to_block: int
    group_by_date: bool
    denominator: Literal["matched", "expected"]
    expected_total: Optional[int] = Field(
        None, description="days × slotsPerDay (present only when denominator=expected)"
    )

# class AllThreeUnifiedPayload(BaseModel):
#     blocks: Union[DemandDataGrouped, DemandDataFlat]
#     demand_curve: List[DemandCurveRow]
#     mape_curve: List[MapeCurveRow]
#     last_7_days: List[Last7DaysRow]
#     meta: UnifiedMeta




# ---------- Schemas forecast demand status (Last 7 days) ----------


class Stat(BaseModel):
    min: Optional[float]
    max: Optional[float]
    avg: Optional[float]
    std: Optional[float]
    median: Optional[float]
    q25: Optional[float]
    q75: Optional[float]
    # sum: Optional[float]

class BlocksPayloadGrouped(BaseModel):
    result: List[DemandDateGroup]          # direct type, no introspection
    forecast_demand_status: Dict[str, Stat]                 # keys: comp_act_demand, comp_udm, forecast, MAPE
    # date_wise_status: Dict[str, Dict[str, Stat]] = Field(default_factory=dict)

class BlocksPayloadFlat(BaseModel):
    result: List[DemandFlatItem]           # direct type, no introspection
    forecast_demand_status: Dict[str, Stat]
    # date_wise_status: Dict[str, Dict[str, Stat]] = Field(default_factory=dict)

class AllThreeUnifiedPayload(BaseModel):
    blocks: Union[BlocksPayloadGrouped, BlocksPayloadFlat]
    demand_curve: List[DemandCurveRow]
    mape_curve: List[MapeCurveRow]
    last_7_days: List[Last7DaysRow]
    meta: UnifiedMeta


class AllThreeUnifiedResponse(BaseModel):
    timestamp: datetime
    data: AllThreeUnifiedPayload



@router.get(
    "/all_graph",
    response_model=AllThreeUnifiedResponse,
    status_code=status.HTTP_200_OK,
    summary="All-in-one: blocks + demand curve + MAPE curve (shared filters, single meta)",
    responses={
        200: {"description": "OK"},
        422: {"description": "Validation error"},
        500: {"description": "Failed to compute combined analysis."},
    },
)
async def forecast_analysis_all_unified(
    market: Optional[List[Literal["IDF", "DAF"]]] = Query(
        default=None,
        description='Market filter. Defaults to ["IDF"] if omitted.',
        example=["IDF"],
    ),
    zone: Optional[List[str]] = Query(
        default=None,
        description='Zone filter (case-insensitive). Defaults to ["PUDUCHERRY"] if omitted.',
        example=["PUDUCHERRY"],
    ),
    date_from: Optional[str] = Query(
        default=None,
        description="Start date (YYYY-MM-DD). Defaults to today (IST).",
        example="2025-08-26",
    ),
    date_to: Optional[str] = Query(
        default=None,
        description="End date (YYYY-MM-DD). Defaults to same as date_from.",
        example="2025-08-27",
    ),
    from_block: Optional[int] = Query(
        default=1, ge=1, le=96,
        description="Per-day block window start (1..96). Default 1.",
        example=1,
    ),
    to_block: Optional[int] = Query(
        default=96, ge=1, le=96,
        description="Per-day block window end (1..96). Default 96.",
        example=96,
    ),
    group_by_date: bool = Query(
        default=True,
        description="If true, blocks are grouped per date; if false, a flat list is returned.",
        example=True,
    ),
    denominator: Literal["matched", "expected"] = Query(
        default="matched",
        description="Shared denominator for BOTH curves.",
        example="expected",
    ),
):
    """
    Runs **blocks**, **demand curve (comp_act_demand)**, and **MAPE curve** with the SAME filters & SAME denominator.
    Returns a single unified `meta`.
    """
    try:
        payload = demand_controller.all_three_unified(
            markets=market,
            zones=zone,
            date_from=date_from,
            date_to=date_to,
            from_block=from_block,
            to_block=to_block,
            group_by_date=group_by_date,
            denominator=denominator,
        )
        return {"timestamp": now_ist().isoformat(), "data": payload}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to compute combined analysis.")
    




# ---------- Endpoint: last 7 days (IST) ----------
@router.get(
    "/demand_forecast_last_7_days",
    response_model=Last7DaysResponse,
    status_code=status.HTTP_200_OK,
    summary="Last 7 days peak & average demand (from precomputed table)",
    response_description=(
        "Returns rows for the last 7 IST dates (today back 6 days), "
        "each with schedule_date, time_block (peak block), peak_demand, and average_demand."
    ),
    responses={
        200: {"description": "OK"},
        500: {"description": "Failed to fetch last 7 days demand summary."},
    },
)
async def demand_forecast_last_7_days():
    """
    Reads from **peak_demand_31_days** (precomputed) and returns a simple list for the last 7 IST days.
    Sorted by `schedule_date` ascending.
    """
    try:
        payload = demand_controller.demand_forecast_last_7_days()
        # payload = {"result": [...]}
        return {
            "timestamp": now_ist().isoformat(),
            "data": payload["result"],
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch last 7 days demand summary.")


# -----------------------------------------------------------------------------
# Schemas (Blocks + Stats)  — matches service: demand_for_selected_days + stats
# -----------------------------------------------------------------------------

# Each block returns both fields
class CompBoth(BaseModel):
    comp_act_demand: float = Field(..., example=7123.45)
    comp_udm: float = Field(..., example=7100.10)

class SelectedDaysBlockItem(BaseModel):
    time_block: int = Field(..., ge=1, le=96, example=1)
    data: CompBoth

class SelectedDaysDateGroup(BaseModel):
    schedule_date: str = Field(..., example="2025-09-01")
    blocks: List[SelectedDaysBlockItem]

class SelectedDaysFlatItem(BaseModel):
    schedule_date: str = Field(..., example="2025-09-01")
    time_block: int = Field(..., ge=1, le=96, example=1)
    data: CompBoth

class SelectedDaysGrouped(BaseModel):
    result: List[SelectedDaysDateGroup]

class SelectedDaysFlat(BaseModel):
    result: List[SelectedDaysFlatItem]


# Stats per field
class FieldStats(BaseModel):
    min: Optional[float] = Field(None, example=325.5)
    max: Optional[float] = Field(None, example=476.0)
    avg: Optional[float] = Field(None, example=402.695833)
    sum: float = Field(..., example=38658.8)
    count: int = Field(..., example=96)

class StatsByField(BaseModel):
    comp_act_demand: FieldStats
    comp_udm: FieldStats

class SelectedDayStatsRow(BaseModel):
    schedule_date: str = Field(..., example="2025-09-15")
    stats: StatsByField


# Combined response
class SelectedDaysCombinedData(BaseModel):
    blocks: Union[SelectedDaysGrouped, SelectedDaysFlat]
    stats: List[SelectedDayStatsRow]

class SelectedDaysCombinedResponse(BaseModel):
    timestamp: datetime = Field(..., example="2025-09-01T09:30:00+05:30")
    data: SelectedDaysCombinedData

# -----------------------------------------------------------------------------
# Endpoint: Single API returning chart blocks + stats table
# -----------------------------------------------------------------------------
@router.get(
    "/selected_days",
    response_model=SelectedDaysCombinedResponse,
    status_code=status.HTTP_200_OK,
    summary="Selected days: blocks (comp_act_demand & comp_udm) + per-date stats table",
    response_description=(
        "Returns comp_act_demand and comp_udm by block for up to 10 dates (grouped or flat) "
        "and a per-date stats table (min, max, avg, sum, count) for both fields."
    ),
    responses={
        200: {"description": "OK"},
        422: {"description": "Validation error"},
        500: {"description": "Failed to fetch selected days data."},
    },
)
async def selected_days(
    date: List[str] = Query(
        ...,
        min_items=1,
        max_items=10,
        description="One or more dates (YYYY-MM-DD). Max 10.",
        example=["2025-09-01", "2025-09-03"],
    ),
    market: Optional[List[Literal["IDF", "DAF"]]] = Query(
        default=None,
        description='Market filter. Defaults to ["IDF"] if omitted.',
        example=["IDF"],
    ),
    zone: Optional[List[str]] = Query(
        default=None,
        description='Zone filter (case-insensitive). Defaults to ["PUDUCHERRY"] if omitted.',
        example=["PUDUCHERRY"],
    ),
    from_block: Optional[int] = Query(
        default=1,
        ge=1,
        le=96,
        description="Per-day block window start (1..96). Default 1.",
        example=1,
    ),
    to_block: Optional[int] = Query(
        default=96,
        ge=1,
        le=96,
        description="Per-day block window end (1..96). Default 96.",
        example=96,
    ),
    group_by_date: bool = Query(
        default=True,
        description="If true, groups by date; if false, returns a flat list with schedule_date on each row.",
        example=True,
    ),
):
    """
    Single-shot API that returns:
    - **blocks**: comp_act_demand & comp_udm per (date, time_block) for the provided dates (max 10),
                  shaped for your chart (grouped-by-date or flat).
    - **stats**:  per-date min, max, avg, sum, count across the selected `from_block..to_block`
                  for both comp_act_demand and comp_udm.

    Sorting: schedule_date ASC, time_block ASC.
    """
    try:
        payload = demand_controller.selected_days_blocks_and_stats(
            dates=date,
            markets=market,
            zones=zone,
            from_block=from_block,
            to_block=to_block,
            group_by_date=group_by_date,
        )
        # payload = { "blocks": {result: [...]}, "stats": [ ... ] }
        return {"timestamp": now_ist().isoformat(), "data": payload}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch selected days data.")