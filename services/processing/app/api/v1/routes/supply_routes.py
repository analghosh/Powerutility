from typing import Optional, List, Dict, Any, Tuple
from enum import Enum
from fastapi import APIRouter, Request, HTTPException, status, Query
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from datetime import datetime, timezone, timedelta
import logging

from ....controller.supply_controller import supply_controller

router = APIRouter(
    prefix="/processing/supply",
    tags=["Supply"]
)

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
def now_ist() -> str:
    return datetime.now(IST).isoformat()

# for Swagger docs
class SupplyResponse(BaseModel):
    """
    Standard wrapper for successful responses.
    """
    timestamp: str = Field(..., description="ISO formatted timestamp (UTC)")
    data: Dict[str, Any] = Field(..., description="Payload data (service-specific shape)")

    class Config:
        json_schema_extra = {
            "example": {
                "timestamp": "2025-08-27T08:15:30.123456",
                "data": {
                    "result": [
                        {
                            "time_block": 42,
                            "technology_breakdown": [
                                {"technology": "Coal", "central": 500, "state": 300, "total": 800}
                            ],
                            "comp_act_demand": 760,
                            "comp_udm": 745
                           
                        }
                    ]
                }
            }
        }


class ErrorResponse(BaseModel):
    """
    Error payload shown for non-2xx responses.
    """
    detail: Any = Field(..., description="Human-readable error details")

    class Config:
        json_schema_extra = {
            "example": {"detail": "Failed to fetch supply data."}
        }


# =========================
#          Routes
# =========================

@router.get(
    "/",
    response_model=SupplyResponse,
    responses={
        200: {
            "description": "OK",
            "model": SupplyResponse,
            "content": {
                "application/json": {
                    "example": {
                        "timestamp": "2025-08-27T08:15:30.123456",
                        "data": {
                            "result": [
                                {
                                    "block": 1,
                                    "byCategory": [
                                        {"c_s": "central", "category": [{"category": "Coal", "volume": 500}]},
                                        {"c_s": "state", "category": [{"category": "Hydro", "volume": 120}]}
                                    ],
                                    "demand": {
                                        "comp_act_demand": 620,
                                        "comp_udm": 610
                                       
                                    }
                                }
                            ]
                        }
                    }
                }
            },
        },
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    },
    status_code=status.HTTP_200_OK,
    summary="Get supply by category",
    description=(
        "Returns per-block supply grouped by **category**, optionally filtered by `schedule_date`.\n\n"
        "**Defaults**: If `schedule_date` is omitted, the service layer uses **today (IST)**.\n"
        "Demand for each block is attached where available."
    ),
    response_description="Supply-by-category with optional demand for each block",
    operation_id="getSupplyByCategory",
    response_model_exclude_none=True,
)
async def supply_category(
    schedule_date: Optional[str] = Query(
        None,
        description="Filter by schedule date (YYYY-MM-DD, IST). Defaults to today's date.",
        examples={
            "today": {"summary": "Today (default in service)", "value": None},
            "custom": {"summary": "Specific date", "value": "2025-08-26"},
        },
    )
):
    """
    **Supply by Category**

    - Groups supply by `central/state` → `category` per **time block**.
    - Attaches demand (comp_act_demand, comp_udm, rostering) where available.
    """
    try:
        result = supply_controller.supply_by_category(schedule_date)
        return SupplyResponse(
            timestamp=datetime.now().isoformat(),
            data=result
        )
    except Exception:
        logger.exception("Error fetching supply data (category)")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch supply data."
        )

@router.get(
    "/category_block",
    response_model=SupplyResponse,
    responses={
        200: {
            "description": "OK",
            "model": SupplyResponse,
            "content": {
                "application/json": {
                    "example": {
                        "timestamp": "2025-08-27T08:15:30.123456",
                        "data": {
                            "result": [
                                {
                                    "time_block": 42,
                                    "category_breakdown": [
                                        {"category": "Coal",  "central": 500, "state": 300, "total": 800},
                                        {"category": "Hydro", "central":  50, "state":  60, "total": 110}
                                    ],
                                    "comp_act_demand": 900,
                                    "comp_udm": 880
                                }
                            ]
                        }
                    }
                }
            },
        },
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    },
    status_code=status.HTTP_200_OK,
    summary="Supply by category (single block, central/state split)",
    description=(
        "Returns **single-block** supply grouped by **category** with central/state split and totals.\n\n"
        "**Filters**:\n"
        "- `schedule_date` (YYYY-MM-DD, IST) — defaults to **today** if omitted.\n"
        "- `time_block` (1..96) — defaults to **current IST block** if omitted.\n\n"
        "Demand for the **same date & block** is attached where available."
    ),
    response_description="Category breakdown for the chosen date & block",
    operation_id="getSupplyByCategoryBlock",
    response_model_exclude_none=True,
)
async def supply_category_block(
    schedule_date: Optional[str] = Query(
        None,
        description="YYYY-MM-DD (IST). Defaults to today's date.",
        examples={
            "today": {"summary": "Use today's date", "value": None},
            "custom": {"summary": "Specific date", "value": "2025-08-26"},
        },
    ),
    time_block: Optional[int] = Query(
        None,
        ge=1,
        le=96,
        description="1..96. Defaults to current IST block.",
        examples={
            "current": {"summary": "Use current block", "value": None},
            "block18": {"summary": "Specific block", "value": 18},
        },
    ),
):
    """
    **Supply by Category (single block)**

    Uses SG-priority at plant×block×category (treats SG/SH as SG, else DC).
    """
    try:
        result = supply_controller.supply_by_category_block(schedule_date, time_block)
        return SupplyResponse(timestamp=datetime.now().isoformat(), data=result)
    except Exception:
        logger.exception("Error fetching supply by category (single block)")
        raise HTTPException(status_code=500, detail="Failed to fetch supply-by-category (single block).")

@router.get(
    "/tech",
    response_model=SupplyResponse,
    responses={
        200: {
            "description": "OK",
            "model": SupplyResponse,
            "content": {
                "application/json": {
                    "example": {
                        "timestamp": "2025-08-27T08:15:30.123456",
                        "data": {
                            "result": [
                                {
                                    "time_block": 42,
                                    "technology_breakdown": [
                                        {"technology": "Coal", "central": 500, "state": 300, "total": 800},
                                        {"technology": "Hydro", "central": 50, "state": 60, "total": 110}
                                    ],
                                    "comp_act_demand": 900,
                                    "comp_udm": 880
                                    
                                }
                            ]
                        }
                    }
                }
            },
        },
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    },
    status_code=status.HTTP_200_OK,
    summary="Get supply by technology (single block by default)",
    description=(
        "Returns **single-block** supply grouped by **technology** with central/state split and totals.\n\n"
        "**Filters**:\n"
        "- `schedule_date` (YYYY-MM-DD, IST) — defaults to **today** if omitted.\n"
        "- `time_block` (1..96) — defaults to **current IST block** if omitted.\n\n"
        "Demand for the **same date & block** is attached where available."
    ),
    response_description="Technology breakdown for the chosen date & block",
    operation_id="getSupplyByTechnology",
    response_model_exclude_none=True,
)
async def supply_technology(
    schedule_date: Optional[str] = Query(
        None,
        description="YYYY-MM-DD (IST). Defaults to today's date.",
        examples={
            "today": {"summary": "Use today's date", "value": None},
            "custom": {"summary": "Specific date", "value": "2025-08-26"},
        },
    ),
    time_block: Optional[int] = Query(
        None,
        ge=1,
        le=96,
        description="1..96. Defaults to current IST block.",
        examples={
            "current": {"summary": "Use current block", "value": None},
            "block5": {"summary": "Specific block", "value": 5},
        },
    ),
):
    """
    **Supply by Technology (single block)**

    Uses SG-priority at plant×block (use SG/SH if present, else DC).
    """
    try:
        result = supply_controller.supply_by_technology(schedule_date, time_block)
        return SupplyResponse(timestamp=datetime.now().isoformat(), data=result)
    except Exception:
        logger.exception("Error fetching supply by technology")
        raise HTTPException(status_code=500, detail="Failed to fetch supply data.")


# ===== Technology GRAPH API =====
@router.get(
    "/tech_graph",
    response_model=SupplyResponse,
    responses={
        200: {"description": "OK", "model": SupplyResponse},
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    },
    status_code=status.HTTP_200_OK,
    summary="Graph data: supply by technology (per block)",
    description=(
        "Returns 1–96 **time blocks** with `technology_breakdown: [{technology, volume}]`.\n"
        "Volume uses SG-priority and is central+state **combined**."
    ),
    response_description="Per-block totals by technology (graph ready)",
    operation_id="getSupplyByTechnologyGraph",
    response_model_exclude_none=True,
)
async def supply_technology_graph(
    schedule_date: Optional[str] = Query(
        None,
        description="YYYY-MM-DD (IST). If omitted, service may default to today.",
        examples={"custom": {"summary": "Specific date", "value": "2025-08-26"}},
    )
):
    """
    **Graph-ready** totals by technology across all blocks for the date.
    """
    try:
        result = supply_controller.supply_by_technology_graph(schedule_date)
        return SupplyResponse(
            timestamp=datetime.now().isoformat(),
            data=result,
        )
    except Exception:
        logger.exception("Error fetching supply-by-technology graph data")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch supply-by-technology graph data.",
        )


# ===== Price Group (tabular breakdown) API =====
@router.get(
    "/price_group",
    response_model=SupplyResponse,
    responses={
        200: {
            "description": "OK",
            "model": SupplyResponse,
            "content": {
                "application/json": {
                    "example": {
                        "timestamp": "2025-08-27T08:15:30.123456",
                        "data": {
                            "price_group": {
                                "result": [
                                    {
                                        "time_block": 42,
                                        "price_group_breakdown": [
                                            {"price_group": "A", "central": 100, "state": 50, "total": 150},
                                            {"price_group": "B", "central": 300, "state": 400, "total": 700},
                                        ],
                                        "comp_act_demand": 860,
                                        "comp_udm": 845
                                       
                                    }
                                ]
                            },
                            "price_range": {"min": 0.0, "max": 12.5}
                        }
                    }
                }
            },
        },
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    },
    status_code=status.HTTP_200_OK,
    summary="Supply by price group (single block by default, central/state split)",
    description=(
        "Returns **single-block** supply grouped by **price_group** with central/state split and totals.\n\n"
        "**Filters**:\n"
        "- `schedule_date` (YYYY-MM-DD, IST) — defaults to **today** if omitted.\n"
        "- `time_block` (1..96) — defaults to **current IST block** if omitted.\n\n"
        "Demand for the **same date & block** is attached where available."
    ),
    response_description="Price group breakdown for the chosen date & block",
    operation_id="getSupplyByPriceGroup",
    response_model_exclude_none=True,
)
async def supply_price_group(
    schedule_date: Optional[str] = Query(
        None,
        description="YYYY-MM-DD (IST). Defaults to today's date.",
        examples={
            "today": {"summary": "Use today's date", "value": None},
            "custom": {"summary": "Specific date", "value": "2025-08-26"},
        },
    ),
    time_block: Optional[int] = Query(
        None,
        ge=1,
        le=96,
        description="1..96. Defaults to current IST block.",
        examples={
            "current": {"summary": "Use current block", "value": None},
            "block25": {"summary": "Specific block", "value": 25},
        },
    ),
):
    """
    **Supply by Price Group (single block)**

    Uses SG-priority at plant×block (treats SG/SH as SG).
    """
    try:
        result = supply_controller.supply_by_price_group(schedule_date, time_block)
        return SupplyResponse(timestamp=datetime.now().isoformat(), data=result)
    except Exception:
        logger.exception("Error fetching supply by price group")
        raise HTTPException(status_code=500, detail="Failed to fetch supply-by-price-group data.")


# ===== Price Group GRAPH API =====
@router.get(
    "/price_group_graph",
    response_model=SupplyResponse,
    responses={
        200: {"description": "OK", "model": SupplyResponse},
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    },
    status_code=status.HTTP_200_OK,
    summary="Graph data: supply by price group (per block)",
    description=(
        "Returns 1–96 **time blocks** with `price_group_breakdown: [{price_group, volume}]`.\n"
        "Volume uses SG-priority and is central+state **combined**."
    ),
    response_description="Per-block totals by price group (graph ready)",
    operation_id="getSupplyByPriceGroupGraph",
    response_model_exclude_none=True,
)
async def supply_price_group_graph(
    schedule_date: Optional[str] = Query(
        None,
        description="YYYY-MM-DD (IST). If omitted, service may default to today.",
        examples={"custom": {"summary": "Specific date", "value": "2025-08-26"}},
    )
):
    """
    **Graph-ready** totals by price group across all blocks for the date.
    """
    try:
        result = supply_controller.supply_by_price_group_graph(schedule_date)
        return SupplyResponse(
            timestamp=datetime.now().isoformat(),
            data=result,
        )
    except Exception:
        logger.exception("Error fetching supply-by-price-group graph data")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch supply-by-price-group graph data.",
        )


@router.get(
    "/plant_list_block",
    response_model=SupplyResponse,
    responses={
        200: {
            "description": "OK",
            "model": SupplyResponse,
            "content": {
                "application/json": {
                    "example": {
                        "timestamp": "2025-08-27T08:15:30.123456",
                        "data": {
                            "result": [
                                {
                                    "time_block": 42,
                                    "plant_breakdown": [
                                        {
                                            "plant_name": "Example TPS",
                                            "c_s": "central",
                                            "technology": "Coal",
                                            "vc": 3.45,
                                            "volume": 24,
                                            "cumulative_volume": 24
                                        }
                                    ],
                                    "comp_act_demand": 900,
                                    "comp_udm": 880
                                    
                                }
                            ]
                        }
                    }
                }
            },
        },
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    },
    summary="Single-block plants list (VC-ordered, within-block cumulative)",
    description=(
        "Returns a **single time block** list of plants ordered by **VC asc**, then **volume**, then **name**.\n"
        "Includes **within-block cumulative** volumes and attached demand for the same date & block.\n\n"
        "**Filters**:\n"
        "- `schedule_date` (YYYY-MM-DD, IST) — defaults to **today** if omitted.\n"
        "- `time_block` (1..96) — defaults to **current IST block** if omitted."
    ),
    response_description="Plant list for the chosen date & block",
    operation_id="getPlantListBlock",
    response_model_exclude_none=True,
)
def plant_list_block(
    schedule_date: Optional[str] = Query(
        None,
        description="YYYY-MM-DD (IST). Defaults to today's date.",
        examples={
            "today": {"summary": "Use today's date", "value": None},
            "custom": {"summary": "Specific date", "value": "2025-08-26"},
        },
    ),
    time_block: Optional[int] = Query(
        None,
        ge=1,
        le=96,
        description="1..96. Defaults to current IST block.",
        examples={
            "current": {"summary": "Use current block", "value": None},
            "block10": {"summary": "Specific block", "value": 10},
        },
    )
):
    """
    **Plant list for one block** (SG-priority selection per plant×block).
    """
    data = supply_controller.plants_list(schedule_date, time_block)
    return {
        "timestamp": datetime.now().isoformat(),
        "data": data
    }




# ====================================================================================================================================================================================
#               Single Api for Graph (by category, By Technology, By Price_group)|  Single APi for All table and Plant List graph (category, technology, price_group and Plant List)
# ====================================================================================================================================================================================

# GRAPH API ALL IN ONE


@router.get(
    "/graph_bundle",
    response_model=SupplyResponse,
    responses={
        200: {"description": "OK", "model": SupplyResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    },
    status_code=status.HTTP_200_OK,
    summary="Bundle: graph data (category, technology, price group) for a date",
    description=(
        "Returns a single payload with:\n"
        "- **category_graph**: per-block supply by category (your existing `/processing/supply` shape)\n"
        "- **technology_graph**: per-block totals by technology (your existing `/tech_graph` shape)\n"
        "- **price_group_graph**: per-block totals by price group (your existing `/price_group_graph` shape)\n\n"
        "All three are filtered by the same `schedule_date` (defaults to **today IST** if omitted)."
    ),
    operation_id="getSupplyGraphBundle",
    response_model_exclude_none=True,
)
async def supply_graph_bundle(
    schedule_date: Optional[str] = Query(
        None,
        description="YYYY-MM-DD (IST). Defaults to today's date.",
        examples={"custom": {"summary": "Specific date", "value": "2025-08-26"}},
    )
):
    try:
        category_graph = supply_controller.supply_by_category(schedule_date)
        technology_graph = supply_controller.supply_by_technology_graph(schedule_date)
        price_group_graph = supply_controller.supply_by_price_group_graph(schedule_date)

        data = {
            "category_graph": category_graph,            # same shape as GET /processing/supply
            "technology_graph": technology_graph,        # same shape as GET /tech_graph
            "price_group_graph": price_group_graph,      # same shape as GET /price_group_graph
        }
        return SupplyResponse(timestamp=datetime.now().isoformat(), data=data)
    except Exception:
        logger.exception("Error fetching graph bundle")
        raise HTTPException(status_code=500, detail="Failed to fetch graph bundle.")




# TABLE API ALL IN ONE

@router.get(
    "/block_bundle",
    response_model=SupplyResponse,
    responses={
        200: {"description": "OK", "model": SupplyResponse},
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    },
    status_code=status.HTTP_200_OK,
    summary="Bundle: all single-block datasets (category, technology, price group, plants)",
    description=(
        "Returns **all four** single-block datasets in one response:\n"
        "- `category` → category-by-block (central/state split + totals)\n"
        "- `technology` → supply by technology (central/state split + totals)\n"
        "- `price_group` → supply by price group (central/state split + totals)\n"
        "- `plants` → plant list (VC-ordered, within-block cumulative)\n\n"
        "**Defaults**: If `schedule_date` is omitted, uses **today (IST)**. "
        "If `time_block` is omitted, uses **current IST block**."
    ),
    operation_id="getSupplyBlockAll",
    response_model_exclude_none=True,
)
async def supply_block_all(
    schedule_date: Optional[str] = Query(
        None,
        description="YYYY-MM-DD (IST). Defaults to today's date."
    ),
    time_block: Optional[int] = Query(
        None, ge=1, le=96,
        description="1..96. Defaults to current IST block."
    ),
):
    """
    Returns all four single-block datasets, sharing the same date/block inputs.
    """
    try:
        # Call existing controller methods (each applies defaults if args are None)
        category_payload = supply_controller.supply_by_category_block(schedule_date, time_block)
        technology_payload = supply_controller.supply_by_technology(schedule_date, time_block)
        price_group_payload = supply_controller.supply_by_price_group(schedule_date, time_block)
        plants_payload = supply_controller.plants_list(schedule_date, time_block)

        # Try to surface the resolved block the services actually used (for UI clarity)
        def _resolved_block(p: Dict[str, Any]) -> Optional[int]:
            try:
                res = p.get("result") or []
                if isinstance(res, list) and res:
                    # Most single-block services return {"time_block": <int>, ...}
                    blk = res[0].get("time_block")
                    if isinstance(blk, int):
                        return blk
            except Exception:
                pass
            return None

        resolved_block = (
            _resolved_block(technology_payload)
            or _resolved_block(price_group_payload)
            or _resolved_block(plants_payload)
            or _resolved_block(category_payload)
            or time_block  # fallback to requested value
        )

        data = {
            # Echo inputs and resolved block to help the client/UI
            "schedule_date": schedule_date,      # may be None -> means "today (IST)" was used
            "time_block": resolved_block,        # concrete block number used by services
            # Native payloads (unchanged shapes from their respective endpoints)
            "category": category_payload,
            "technology": technology_payload,
            "price_group": price_group_payload,
            "plants": plants_payload,
        }

        return SupplyResponse(timestamp=datetime.now().isoformat(), data=data)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error fetching block_all bundle")
        raise HTTPException(status_code=500, detail="Failed to fetch block_all bundle.")



# ---------- Response models (lightweight) ----------
class PlantListItem(BaseModel):
    schedule_date: str
    time_block: int = Field(..., ge=1, le=96)
    plant_breakdown: List[Dict[str, Any]]

class PriceGroupItem(BaseModel):
    schedule_date: str
    time_block: int = Field(..., ge=1, le=96)
    price_group_breakdown: List[Dict[str, Any]]

class TechnologyItem(BaseModel):
    schedule_date: str
    time_block: int = Field(..., ge=1, le=96)
    technology_breakdown: List[Dict[str, Any]]

class RangeResponse(BaseModel):
    timestamp: str
    data: List[Dict[str, Any]]

# ---------- Endpoints ----------
@router.get(
    "/plant_list_block/range",
    response_model=RangeResponse,
    status_code=status.HTTP_200_OK,
    summary="Plant list (VC-ordered) for a date & block range",
)
async def plant_list_block_range(
    date_from: str = Query(..., description="YYYY-MM-DD (inclusive)"),
    date_to: str   = Query(..., description="YYYY-MM-DD (inclusive)"),
    from_block: Optional[int] = Query(1, ge=1, le=96),
    to_block: Optional[int]   = Query(96, ge=1, le=96),
):
    try:
        items = supply_controller.plants_list_range(
            date_from=date_from,
            date_to=date_to,
            from_block=from_block,
            to_block=to_block,
        )
        return {"timestamp": now_ist(), "data": items}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch plant list (range).")

@router.get(
    "/price_group/range",
    response_model=RangeResponse,
    status_code=status.HTTP_200_OK,
    summary="Supply by price group for a date & block range",
)
async def price_group_range(
    date_from: str = Query(..., description="YYYY-MM-DD (inclusive)"),
    date_to: str   = Query(..., description="YYYY-MM-DD (inclusive)"),
    from_block: Optional[int] = Query(1, ge=1, le=96),
    to_block: Optional[int]   = Query(96, ge=1, le=96),
):
    try:
        items = supply_controller.price_group_range(
            date_from=date_from,
            date_to=date_to,
            from_block=from_block,
            to_block=to_block,
        )
        return {"timestamp": now_ist(), "data": items}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch price group (range).")

@router.get(
    "/tech/range",
    response_model=RangeResponse,
    status_code=status.HTTP_200_OK,
    summary="Supply by technology for a date & block range",
)
async def technology_range(
    date_from: str = Query(..., description="YYYY-MM-DD (inclusive)"),
    date_to: str   = Query(..., description="YYYY-MM-DD (inclusive)"),
    from_block: Optional[int] = Query(1, ge=1, le=96),
    to_block: Optional[int]   = Query(96, ge=1, le=96),
):
    try:
        items = supply_controller.technology_range(
            date_from=date_from,
            date_to=date_to,
            from_block=from_block,
            to_block=to_block,
        )
        return {"timestamp": now_ist(), "data": items}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch technology (range).")


@router.get(
    "/category_block/range",
    response_model=RangeResponse,
    status_code=status.HTTP_200_OK,
    summary="Supply by category (single block) for a date & block range",
)
async def category_block_range(
    date_from: str = Query(..., description="YYYY-MM-DD (inclusive)"),
    date_to: str   = Query(..., description="YYYY-MM-DD (inclusive)"),
    from_block: Optional[int] = Query(1, ge=1, le=96),
    to_block: Optional[int]   = Query(96, ge=1, le=96),
):
    try:
        items = supply_controller.category_block_range(
            date_from=date_from,
            date_to=date_to,
            from_block=from_block,
            to_block=to_block,
        )
        return {"timestamp": now_ist(), "data": items}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch category (block) range.")

# ---------- Category (whole day) range with block filtering ----------
@router.get(
    "/category/range",
    response_model=RangeResponse,
    status_code=status.HTTP_200_OK,
    summary="Supply by category (whole day) with date range and block filtering",
    response_description=(
        "For each date in the range, returns rows per block within the requested time_block window. "
        "Each row contains byCategory, comp_act_demand, comp_udm."
    ),
)
async def category_range(
    date_from: str = Query(..., description="YYYY-MM-DD (inclusive)"),
    date_to: str   = Query(..., description="YYYY-MM-DD (inclusive)"),
    from_block: Optional[int] = Query(1, ge=1, le=96),
    to_block: Optional[int]   = Query(96, ge=1, le=96),
):
    try:
        items = supply_controller.category_range(
            date_from=date_from,
            date_to=date_to,
            from_block=from_block,
            to_block=to_block,
        )
        return {"timestamp": now_ist(), "data": items}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch category (day) range.")



# ===== Raw supply table API (filters, projection, ranges, pagination) =====


@router.get(
    "/raw",
    response_model=SupplyResponse,
    responses={
        200: {"description": "OK", "model": SupplyResponse},
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    },
    status_code=status.HTTP_200_OK,
    summary="Raw: serve_supply_table (filters, projection, ranges, pagination)",
    description=(
        "Generic reader for **serve_supply_table**. "
        "By default returns **all rows & fields**.\n\n"
        "Supports date/time_block ranges, field filters, projection (include/exclude), sorting, pagination."
    ),
    operation_id="getSupplyRaw",
    response_model_exclude_none=True,
)
async def supply_raw(
    # ---- ranges (all optional) ----
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD (inclusive)"),
    date_to:   Optional[str] = Query(None, description="YYYY-MM-DD (inclusive)"),
    from_block: Optional[int] = Query(None, ge=1, le=96, description="Lower bound time_block (inclusive)"),
    to_block:   Optional[int] = Query(None, ge=1, le=96, description="Upper bound time_block (inclusive)"),

    # ---- common field filters (multi-value allowed) ----
    plant_id: Optional[List[str]] = Query(None),
    plant_name: Optional[List[str]] = Query(None),
    c_s: Optional[List[str]] = Query(None, description="central | state"),
    category: Optional[List[str]] = Query(None),
    technology: Optional[List[str]] = Query(None),
    price_group: Optional[List[str]] = Query(None),
    dc_sg: Optional[List[str]] = Query(None, description="DC | SG | SH"),

    # numeric ranges (optional)
    ramping_min: Optional[float] = Query(None),
    ramping_max: Optional[float] = Query(None),
    TM_min: Optional[float] = Query(None),
    TM_max: Optional[float] = Query(None),
    FC_min: Optional[float] = Query(None, description="FC_price min"),
    FC_max: Optional[float] = Query(None, description="FC_price max"),
    VC_min: Optional[float] = Query(None, description="VC_price min"),
    VC_max: Optional[float] = Query(None, description="VC_price max"),
    px_min: Optional[float] = Query(None, description="px_price min"),
    px_max: Optional[float] = Query(None, description="px_price max"),
    mult_min: Optional[float] = Query(None, description="multiplier min"),
    mult_max: Optional[float] = Query(None, description="multiplier max"),
    vol_min: Optional[float] = Query(None, description="volume min"),
    vol_max: Optional[float] = Query(None, description="volume max"),

    updated_from: Optional[str] = Query(None, description="ISO datetime, inclusive"),
    updated_to:   Optional[str] = Query(None, description="ISO datetime, inclusive"),

    # ---- projection ----
    include: Optional[str] = Query(None, description="Comma-separated list to include"),
    exclude: Optional[str] = Query(None, description="Comma-separated list to exclude"),

    # ---- sorting + pagination ----
    sort: Optional[str] = Query(None, description="Comma-separated 'field:dir'. e.g. 'schedule_date:asc,time_block:asc'"),
    skip: int = Query(0, ge=0),
    limit: Optional[int] = Query(
        None,                  # <-- None => service adds no $limit
        ge=1, le=1_000_000,
        description="Max rows to return. Omit for no limit (use with care)."
    ),
):
    """
    Examples:

    - **All data (default)**: `/processing/supply/raw`
    - Date & block window: `/processing/supply/raw?date_from=2025-09-22&date_to=2025-09-23&from_block=1&to_block=96`
    - Filter tech + price_group: `/processing/supply/raw?technology=Thermal&price_group=D&price_group=C`
    - Projection include: `/processing/supply/raw?include=schedule_date,time_block,plant_id,volume`
    - Projection exclude: `/processing/supply/raw?exclude=updated_at,px_price`
    - Sort & paginate: `/processing/supply/raw?sort=schedule_date:asc,time_block:asc&skip=0&limit=5000`
    """
    try:
        # Build filters dict for the service
        filters: Dict[str, Any] = {}

        def _maybe_in(name: str, values: Optional[List[str]]):
            if values:
                filters[name] = values  # service will convert list -> {$in: [...]}

        _maybe_in("plant_id", plant_id)
        _maybe_in("plant_name", plant_name)
        _maybe_in("c_s", c_s)
        _maybe_in("category", category)
        _maybe_in("technology", technology)
        _maybe_in("price_group", price_group)
        _maybe_in("dc_sg", dc_sg)

        # Numeric range helper
        def _range(field: str, lo: Optional[float], hi: Optional[float]):
            rng: Dict[str, Any] = {}
            if lo is not None: rng["$gte"] = lo
            if hi is not None: rng["$lte"] = hi
            if rng: filters[field] = rng

        _range("ramping", ramping_min, ramping_max)
        _range("TM", TM_min, TM_max)
        _range("FC_price", FC_min, FC_max)
        _range("VC_price", VC_min, VC_max)
        _range("px_price", px_min, px_max)
        _range("multiplier", mult_min, mult_max)
        _range("volume", vol_min, vol_max)

        # updated_at range (stored as Date)
        if updated_from or updated_to:
            rng: Dict[str, Any] = {}
            if updated_from: rng["$gte"] = {"$date": updated_from} if "T" in updated_from else updated_from
            if updated_to:   rng["$lte"] = {"$date": updated_to} if "T" in updated_to else updated_to
            # Note: if your repo expects native Python datetimes, convert here instead.
            filters["updated_at"] = rng

        # Projection parsing
        include_fields = [s.strip() for s in include.split(",")] if include else None
        exclude_fields = [s.strip() for s in exclude.split(",")] if exclude else None
        if include_fields and exclude_fields:
            raise HTTPException(status_code=422, detail="Use either 'include' or 'exclude', not both.")

        # Sort parsing (e.g. "schedule_date:asc,time_block:desc")
        sort_list: Optional[List[Tuple[str, int]]] = None
        if sort:
            sort_list = []
            parts = [p.strip() for p in sort.split(",") if p.strip()]
            for p in parts:
                if ":" in p:
                    f, d = p.split(":", 1)
                    direction = 1 if d.lower() in ("1", "asc", "ascending", "+") else -1
                else:
                    f, direction = p, 1
                sort_list.append((f.strip(), direction))

        data = supply_controller.supply_raw(
            date_from=date_from,
            date_to=date_to,
            from_block=from_block,
            to_block=to_block,
            filters=filters,
            include_fields=include_fields,
            exclude_fields=exclude_fields,
            sort=sort_list,
            skip=skip,
            limit=limit,
        )

        return SupplyResponse(timestamp=datetime.now().isoformat(), data=data)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error fetching raw serve_supply_table")
        raise HTTPException(status_code=500, detail="Failed to fetch raw serve_supply_table.")
