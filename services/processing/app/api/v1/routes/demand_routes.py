
from typing import Optional, List, Dict, Any, Tuple
from fastapi import APIRouter, Query, HTTPException, status
from fastapi.responses import ORJSONResponse   # <-- fixed: ORJSONResponse
from pydantic import BaseModel, Field
from datetime import datetime, timezone, timedelta
import logging

from ....controller.demand_controller import demand_controller


router = APIRouter(
    prefix="/processing/demand",
    tags=["demand"]
)

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
def now_ist() -> str:
    return datetime.now(IST).isoformat()

class DemandResponse(BaseModel):
    timestamp: str = Field(..., description="iso formatted timestamp (utc)")
    data: Dict[str, Any] = Field(..., description="payload data (service-specific shape)")
    class Config:
        json_schema_extra = {
            "example": {
                "timestamp": "2025-09-25T08:15:30.123456",
                "data": {
                    "result": [
                        {
                            "time_block": 1,
                            "schedule_date": "2025-09-23",
                            "zone": "puducherry",
                            "comp_act_demand": 415.55,
                            "comp_udm": 420.10,
                            "mape": 3.4773,
                            "updated_at": "2025-09-22T00:00:00.120Z"
                        }
                    ],
                    "meta": {"skip": 0, "limit": 1000, "returned": 1}
                }
            }
        }

class ErrorResponse(BaseModel):
    detail: Any = Field(..., description="human-readable error details")

@router.get(
    "/raw",
    response_model=DemandResponse,
    responses={
        200: {"description": "ok", "model": DemandResponse},
        422: {"description": "validation error", "model": ErrorResponse},
        500: {"description": "server error", "model": ErrorResponse},
    },
    status_code=status.HTTP_200_OK,
    summary="raw: serve_demand_table (filters, projection, ranges, pagination)",
    description=(
        "generic reader for **serve_demand_table**. "
        "by default returns **all rows & fields**.\n\n"
        "supports date/time_block ranges, field filters, projection (include/exclude), sorting, pagination."
    ),
    operation_id="getDemandRaw",
    response_model_exclude_none=True,
    response_class=ORJSONResponse,            # <-- use orjson for this endpoint
)
async def demand_raw(
    # ---- ranges (all optional) ----
    date_from: Optional[str] = Query(None, description="yyyy-mm-dd (inclusive)"),
    date_to:   Optional[str] = Query(None, description="yyyy-mm-dd (inclusive)"),
    from_block: Optional[int] = Query(None, ge=1, le=96, description="lower bound time_block (inclusive)"),
    to_block:   Optional[int] = Query(None, ge=1, le=96, description="upper bound time_block (inclusive)"),

    # ---- common field filters (multi-value allowed) ----
    type: Optional[List[str]] = Query(None, description="e.g., idf"),
    zone: Optional[List[str]] = Query(None),

    # numeric ranges (all lowercase)
    actual_min: Optional[float] = Query(None, description="actual min"),
    actual_max: Optional[float] = Query(None, description="actual max"),
    drawal_min: Optional[float] = Query(None, description="drawal min"),
    drawal_max: Optional[float] = Query(None, description="drawal max"),
    mape_min: Optional[float] = Query(None, description="mape min"),
    mape_max: Optional[float] = Query(None, description="mape max"),
    mpe_min: Optional[float] = Query(None, description="mpe min"),
    mpe_max: Optional[float] = Query(None, description="mpe max"),
    raw_freq_min: Optional[float] = Query(None, description="raw_frequency min"),
    raw_freq_max: Optional[float] = Query(None, description="raw_frequency max"),
    schedule_min: Optional[float] = Query(None, description="schedule min"),
    schedule_max: Optional[float] = Query(None, description="schedule max"),
    comp_act_min: Optional[float] = Query(None, description="comp_act_demand min"),
    comp_act_max: Optional[float] = Query(None, description="comp_act_demand max"),
    comp_udm_min: Optional[float] = Query(None, description="comp_udm min"),
    comp_udm_max: Optional[float] = Query(None, description="comp_udm max"),
    dsm_rate_min: Optional[float] = Query(None, description="dsm_rate min"),
    dsm_rate_max: Optional[float] = Query(None, description="dsm_rate max"),
    forecast_min: Optional[float] = Query(None, description="forecast min"),
    forecast_max: Optional[float] = Query(None, description="forecast max"),
    forecast_freq_min: Optional[float] = Query(None, description="forecasted_frequency min"),
    forecast_freq_max: Optional[float] = Query(None, description="forecasted_frequency max"),
    net_dsm_min: Optional[float] = Query(None, description="net_dsm_amount min"),
    net_dsm_max: Optional[float] = Query(None, description="net_dsm_amount max"),
    rostering_min: Optional[float] = Query(None, description="rostering min"),
    rostering_max: Optional[float] = Query(None, description="rostering max"),

    updated_from: Optional[str] = Query(None, description="iso datetime, inclusive"),
    updated_to:   Optional[str] = Query(None, description="iso datetime, inclusive"),

    # ---- projection ----
    include: Optional[str] = Query(None, description="comma-separated list to include (lowercase)"),
    exclude: Optional[str] = Query(None, description="comma-separated list to exclude (lowercase)"),

    # ---- sorting + pagination ----
    sort: Optional[str] = Query(None, description="comma-separated 'field:dir'. e.g. 'schedule_date:asc,time_block:asc'"),
    skip: int = Query(0, ge=0),
    limit: Optional[int] = Query(
        None,
        ge=1, le=1_000_000,
        description="max rows to return. omit for no limit (use with care)."
    ),
):
    """
    examples:
    - all data (default): `/processing/demand/raw`
    - date & block window: `/processing/demand/raw?date_from=2025-09-22&date_to=2025-09-23&from_block=1&to_block=96`
    - filter by zone & type: `/processing/demand/raw?zone=puducherry&type=idf`
    - projection include: `/processing/demand/raw?include=schedule_date,time_block,zone,comp_act_demand`
    - sort & paginate: `/processing/demand/raw?sort=schedule_date:asc,time_block:asc&skip=0&limit=5000`
    """
    try:
        # build filters dict for the service (all lowercase keys)
        filters: Dict[str, Any] = {}

        def _maybe_in(name: str, values: Optional[List[str]]):
            if values:
                filters[name] = values  # service maps lowercase -> db via FIELD_MAP

        _maybe_in("type", type)
        _maybe_in("zone", zone)

        # numeric ranges helper (lowercase keys only)
        def _range(field: str, lo: Optional[float], hi: Optional[float]):
            rng: Dict[str, Any] = {}
            if lo is not None: rng["$gte"] = lo
            if hi is not None: rng["$lte"] = hi
            if rng: filters[field] = rng

        _range("actual", actual_min, actual_max)
        _range("drawal", drawal_min, drawal_max)
        _range("mape", mape_min, mape_max)
        _range("mpe", mpe_min, mpe_max)
        _range("raw_frequency", raw_freq_min, raw_freq_max)
        _range("schedule", schedule_min, schedule_max)
        _range("comp_act_demand", comp_act_min, comp_act_max)
        _range("comp_udm", comp_udm_min, comp_udm_max)
        _range("dsm_rate", dsm_rate_min, dsm_rate_max)
        _range("forecast", forecast_min, forecast_max)
        _range("forecasted_frequency", forecast_freq_min, forecast_freq_max)
        _range("net_dsm_amount", net_dsm_min, net_dsm_max)
        _range("rostering", rostering_min, rostering_max)

        # updated_at range
        if updated_from or updated_to:
            rng: Dict[str, Any] = {}
            if updated_from: rng["$gte"] = {"$date": updated_from} if "T" in updated_from else updated_from
            if updated_to:   rng["$lte"] = {"$date": updated_to}   if "T" in updated_to   else updated_to
            filters["updated_at"] = rng

        # projection parsing
        include_fields = [s.strip().lower() for s in include.split(",")] if include else None
        exclude_fields = [s.strip().lower() for s in exclude.split(",")] if exclude else None
        if include_fields and exclude_fields:
            raise HTTPException(status_code=422, detail="use either 'include' or 'exclude', not both.")

        # sort parsing (lowercase field names; service maps to db fields)
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
                sort_list.append((f.strip().lower(), direction))

        data = demand_controller.demand_raw(
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

        # keep returning a dict to preserve pydantic validation;
        # ORJSONResponse is applied via the decorator’s response_class
        return DemandResponse(timestamp=datetime.now().isoformat(), data=data)

    except HTTPException:
        raise
    except Exception:
        logger.exception("error fetching raw serve_demand_table")
        raise HTTPException(status_code=500, detail="failed to fetch raw serve_demand_table.")
