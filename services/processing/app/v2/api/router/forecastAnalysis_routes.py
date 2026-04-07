
from typing import List, Optional, Literal, Union, Dict, Any
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Query, HTTPException, status, Depends
from pydantic import BaseModel, Field
import logging

from ...controller.forecastanalysis_controller import ForecastAnalysisController
from ...dependencies import get_user_context
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger

router = APIRouter(prefix="/forecast_analysis", tags=["Forecast Analysis V2"])
controller = ForecastAnalysisController()
logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
def now_ist() -> datetime:
    return datetime.now(IST)

# ---------- Schemas ----------

class DemandMetrics(BaseModel): comp_act_demand: Optional[float]; comp_udm: Optional[float]; forecast: Optional[float]; MAPE: Optional[str]
class DemandBlockItem(BaseModel): time_block: int; data: DemandMetrics
class DemandDateGroup(BaseModel): schedule_date: str; blocks: List[DemandBlockItem]
class DemandDataGrouped(BaseModel): result: List[DemandDateGroup]
class DemandCurveRow(BaseModel): count: int; comp_act_demand: float; percentage: float
class MapeCurveRow(BaseModel): count: int; MAPE: str; percentage: float
class Last7DaysRow(BaseModel): schedule_date: str; peak_demand: float; average_demand: float # MODIFIED

class UnifiedMeta(BaseModel):
    markets: List[str]; zones: List[str]; date_from: str; date_to: str; from_block: int; to_block: int; group_by_date: bool; denominator: Literal["matched", "expected"]; expected_total: Optional[int]

# ADDED: Schema for the missing forecast_demand_status
class DemandStatusStats(BaseModel):
    min: Optional[float]
    max: Optional[float]
    avg: Optional[float]
    std: Optional[float]
    median: Optional[float]
    q25: Optional[float]
    q75: Optional[float]

class ForecastDemandStatus(BaseModel):
    comp_act_demand: DemandStatusStats
    comp_udm: DemandStatusStats
    forecast: DemandStatusStats
    MAPE: DemandStatusStats

class AllThreeUnifiedPayload(BaseModel):
    blocks: DemandDataGrouped
    forecast_demand_status: ForecastDemandStatus  # ADDED
    demand_curve: List[DemandCurveRow]
    mape_curve: List[MapeCurveRow]
    last_7_days: List[Last7DaysRow]
    meta: UnifiedMeta
    last_updated_at: Optional[datetime]  # ADDED

class AllThreeUnifiedResponse(BaseModel):
    timestamp: datetime
    data: AllThreeUnifiedPayload
    correlation_id: Optional[str]

class CompBoth(BaseModel): comp_act_demand: float; comp_udm: float
class SelectedDaysBlockItem(BaseModel): time_block: int; data: CompBoth
class SelectedDaysDateGroup(BaseModel): schedule_date: str; blocks: List[SelectedDaysBlockItem]
class SelectedDaysFlatItem(BaseModel): schedule_date: str; time_block: int; data: CompBoth
class SelectedDaysGrouped(BaseModel): result: List[SelectedDaysDateGroup]
class SelectedDaysFlat(BaseModel): result: List[SelectedDaysFlatItem]
class FieldStats(BaseModel): min: Optional[float]; max: Optional[float]; avg: Optional[float]; sum: float; count: int
class StatsByField(BaseModel): comp_act_demand: FieldStats; comp_udm: FieldStats
class SelectedDayStatsRow(BaseModel): schedule_date: str; stats: StatsByField
class SelectedDaysCombinedData(BaseModel): blocks: Union[SelectedDaysGrouped, SelectedDaysFlat]; stats: List[SelectedDayStatsRow]

class SelectedDaysCombinedResponse(BaseModel):
    timestamp: datetime
    data: SelectedDaysCombinedData
    correlation_id: Optional[str]

class Last7DaysResponseOnlyPeak(BaseModel):
    timestamp: datetime
    data: List[Last7DaysRow]
    correlation_id: Optional[str]

# ---------- Endpoints ----------

@router.get("/all_graph", response_model=AllThreeUnifiedResponse, summary="All-in-one forecast analysis")
async def forecast_analysis_all_unified(user_context: Dict[str, Any] = Depends(get_user_context), market: Optional[List[str]] = Query(None), zone: Optional[List[str]] = Query(None), date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None), from_block: int = 1, to_block: int = 96, group_by_date: bool = True, denominator: Literal["matched", "expected"] = "matched", audit_logger: AuditLogger = Depends(get_audit_logger)):
    user_id = user_context.get("user_id", "anonymous"); username = user_context.get("username", "anonymous"); correlation_id = user_context.get("correlation_id", "unknown")
    filters = {"markets": market, "zones": zone, "date_from": date_from, "date_to": date_to, "from_block": from_block, "to_block": to_block, "group_by_date": group_by_date, "denominator": denominator}
    try:
        audit_logger.log_system_operation(service="processing", operation="all_graph_fetch", message="Starting all_graph data fetch", correlation_id=correlation_id, user_id=user_id, **filters)
        payload = await controller.get_all_graph_data(**filters)
        audit_logger.log_user_action(service="processing", user_id=user_id, action="all_graph_view", message="User successfully fetched all_graph data", username=username, correlation_id=correlation_id, success=True, **filters)
        return {"timestamp": now_ist().isoformat(), "data": payload, "correlation_id": correlation_id}
    except Exception as e:
        audit_logger.log_error_event(service="processing", error_name="AllGraphFetchError", error_message=str(e), user_id=user_id, correlation_id=correlation_id, error_type=type(e).__name__, **filters)
        logger.exception(f"Error fetching all_graph data for user {user_id}")
        raise HTTPException(status_code=500, detail="Failed to compute combined analysis.")

@router.get("/demand_forecast_last_7_days", response_model=Last7DaysResponseOnlyPeak, summary="Last 7 days peak demand")
async def demand_forecast_last_7_days(user_context: Dict[str, Any] = Depends(get_user_context), audit_logger: AuditLogger = Depends(get_audit_logger)):
    user_id = user_context.get("user_id", "anonymous"); username = user_context.get("username", "anonymous"); correlation_id = user_context.get("correlation_id", "unknown")
    try:
        audit_logger.log_system_operation(service="processing", operation="last_7_days_fetch", message="Starting last 7 days fetch", correlation_id=correlation_id, user_id=user_id)
        payload = await controller.get_demand_forecast_last_7_days()
        audit_logger.log_user_action(service="processing", user_id=user_id, action="last_7_days_view", message="User successfully fetched last 7 days data", username=username, correlation_id=correlation_id, success=True)
        return {"timestamp": now_ist().isoformat(), "data": payload["result"], "correlation_id": correlation_id}
    except Exception as e:
        audit_logger.log_error_event(service="processing", error_name="Last7DaysFetchError", error_message=str(e), user_id=user_id, correlation_id=correlation_id, error_type=type(e).__name__)
        logger.exception(f"Error fetching last 7 days data for user {user_id}")
        raise HTTPException(status_code=500, detail="Failed to fetch last 7 days demand summary.")

@router.get("/selected_days", response_model=SelectedDaysCombinedResponse, summary="Data and stats for selected days")
async def selected_days(
    user_context: Dict[str, Any] = Depends(get_user_context),
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
    from_block: int = Query(
        default=1,
        ge=1,
        le=96,
        description="Per-day block window start (1..96). Default 1.",
        example=1,
    ),
    to_block: int = Query(
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
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous"); username = user_context.get("username", "anonymous"); correlation_id = user_context.get("correlation_id", "unknown")
    filters = {"dates": date, "markets": market, "zones": zone, "from_block": from_block, "to_block": to_block, "group_by_date": group_by_date}
    try:
        audit_logger.log_system_operation(service="processing", operation="selected_days_fetch", message="Starting selected days fetch", correlation_id=correlation_id, user_id=user_id, **filters)
        payload = await controller.get_selected_days_data(**filters)
        audit_logger.log_user_action(service="processing", user_id=user_id, action="selected_days_view", message="User successfully fetched selected days data", username=username, correlation_id=correlation_id, success=True, **filters)
        return {"timestamp": now_ist().isoformat(), "data": payload, "correlation_id": correlation_id}
    except Exception as e:
        audit_logger.log_error_event(service="processing", error_name="SelectedDaysFetchError", error_message=str(e), user_id=user_id, correlation_id=correlation_id, error_type=type(e).__name__, **filters)
        logger.exception(f"Error fetching selected days data for user {user_id}")
        raise HTTPException(status_code=500, detail="Failed to fetch selected days data.")
