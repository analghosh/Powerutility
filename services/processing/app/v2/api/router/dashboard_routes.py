"""
Dashboard V2 Router
Enhanced dashboard endpoints with JWT authentication and correlation tracking
"""

from fastapi import APIRouter, Request, HTTPException, status, Depends, Query
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum
import logging

from ...controller.dashboard_controller import dashboard_controller
from ...dependencies import get_user_context
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger
from libs.middleware import get_correlation_id

router = APIRouter(
    prefix="/dashboard",
    tags=["Dashboard V2"]
)

logger = logging.getLogger(__name__)


class PowerUnit(str, Enum):
    MW = "MW"
    KW = "kW"
    GW = "GW"
    KWH = "kWh"


class PriceUnit(str, Enum):
    RS_PER_MWH = "RS_PER_MWH"
    RS_PER_KWH = "RS_PER_KWH"
    PAISE_PER_KWH = "PAISE_PER_KWH"


class CurrencyUnit(str, Enum):
    RS = "Rs"
    PAISE = "Paise"


class CurrencyScale(str, Enum):
    UNITS = "units"
    THOUSANDS = "thousands"
    LAKHS = "lakhs"
    CRORES = "crores"


class FrequencyUnit(str, Enum):
    HZ = "Hz"
    KHZ = "kHz"


class DashboardResponse(BaseModel):
    """
    Response schema for Dashboard V2 APIs.
    """
    data: Dict[str, Any] = Field(..., description="Dashboard data payload")
    timestamp: str = Field(..., description="Response timestamp in ISO format")
    correlation_id: Optional[str] = Field(None, description="Request correlation ID")

class CapacityChartResponse(BaseModel):
    """
    Response schema for Capacity Chart API.
    """
    data: Dict[str, Any] = Field(..., description="Hierarchical data for the power plant capacity sunburst chart")
    timestamp: str = Field(..., description="Response timestamp in ISO format")
    correlation_id: Optional[str] = Field(None, description="Request correlation ID")

class DemandRawV2Data(BaseModel):
    """
    Schema for a single demand/raw V2 data entry.
    """
    timestamp: Optional[datetime] = Field(None, description="Timestamp of the data entry")
    remarks: Optional[str] = Field(None, description="Remarks for the data entry")
    zone: Optional[str] = Field(None, description="Geographical zone")
    mpe: Optional[float] = Field(None, description="Mean Percentage Error")
    comp_act_demand: Optional[float] = Field(None, description="Computed Actual Demand")
    type: Optional[str] = Field(None, description="Type of demand (e.g., DAF, IDF)")
    source_actual: Optional[str] = Field(None, description="Source of actual data")
    drawal: Optional[float] = Field(None, description="Drawal value")
    forecast: Optional[float] = Field(None, description="Forecast value")
    id: Optional[str] = Field(None, alias="_id", description="MongoDB ObjectId as string")
    created_at: Optional[datetime] = Field(None, description="Creation timestamp")
    updated_flag: Optional[str] = Field(None, description="Update flag (e.g., U for updated)")
    schedule: Optional[float] = Field(None, description="Schedule value")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")
    source_forecast: Optional[str] = Field(None, description="Source of forecast data")
    source_rostering: Optional[str] = Field(None, description="Source of rostering data")
    source: Optional[str] = Field(None, description="General source information")
    time_block: Optional[int] = Field(None, description="Time block number")
    frequency: Optional[float] = Field(None, description="Frequency value")
    rostering: Optional[float] = Field(None, description="Rostering value")
    mape: Optional[float] = Field(None, description="Mean Absolute Percentage Error")
    actual: Optional[float] = Field(None, description="Actual value")
    net_dsm_amount: Optional[float] = Field(None, description="Net DSM amount")
    comp_udm: Optional[float] = Field(None, description="Computed UDM")
    dsm_rate: Optional[float] = Field(None, description="DSM rate")
    scheduled_at: Optional[datetime] = Field(None, description="Scheduled timestamp")
    udm: Optional[int] = Field(None, description="UDM value")
    inserted_at: Optional[datetime] = Field(None, description="Insertion timestamp")

class PaginationMeta(BaseModel):
    skip: int = Field(..., description="Number of records skipped")
    limit: Optional[int] = Field(None, description="Maximum number of records requested")
    returned: int = Field(..., description="Number of records returned in this response")

class DemandRawV2DataPayload(BaseModel):
    result: List[DemandRawV2Data] = Field(..., description="List of demand/raw V2 data entries")
    meta: PaginationMeta = Field(..., description="Pagination metadata")

class DemandRawV2Response(BaseModel):
    """
    Response schema for Demand Raw V2 API.
    """
    data: DemandRawV2DataPayload = Field(..., description="Demand raw V2 data payload with metadata")
    timestamp: str = Field(..., description="Response timestamp in ISO format")
    correlation_id: Optional[str] = Field(None, description="Request correlation ID")

class DemandTempRawV2Data(BaseModel):
    """
    Schema for a single Temp demand/raw V2 data entry.
    """
    
    drawal: Optional[float] = Field(None, description="Drawal value")
    forecast: Optional[float] = Field(None, description="Forecast value")
    id: Optional[str] = Field(None, alias="_id", description="MongoDB ObjectId as string")
    created_at: Optional[datetime] = Field(None, description="Creation timestamp")
    updated_flag: Optional[str] = Field(None, description="Update flag (e.g., U for updated)")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")
    time_block: Optional[int] = Field(None, description="Time block number")
    frequency: Optional[float] = Field(None, description="Frequency value")
    rostering: Optional[float] = Field(None, description="Rostering value")
    inserted_at: Optional[datetime] = Field(None, description="Insertion timestamp")
    scheduled_at: Optional[datetime] = Field(None, description="Scheduled timestamp")
    
class DemandTempRawV2DataPayload(BaseModel):
    result: List[DemandTempRawV2Data] = Field(..., description="List of demand/raw V2 data entries")
    meta: PaginationMeta = Field(..., description="Pagination metadata")
    
class DemandRawTempV2Response(BaseModel):
    """
    Response schema for Demand Raw V2 API.
    """
    data: DemandTempRawV2DataPayload = Field(..., description="Demand raw V2 data payload with metadata")
    timestamp: str = Field(..., description="Response timestamp in ISO format")
    correlation_id: Optional[str] = Field(None, description="Request correlation ID") 


    
@router.get(
    "/home",
    response_model=DashboardResponse,
    status_code=status.HTTP_200_OK,
    summary="Dashboard Home Data (V2)",
    description="""
    Fetches **dashboard insights** for the Power Grid system including:

    - 🔹 Demand (Actual & Forecast) upto current_block show Actual and after current_block shows Forecast
    - 🔹 Frequency trends (last 24 blocks)
    - 🔹 OD-UD (Over/Under Drawal)
    - 🔹 MCP (Market Clearing Price) for RTM & DAM
    - 🔹 Scheduled Drawal (InterState & IntraState)
    - 🔹 Peak Demand (last 31 days)

    ### Notes:
    - `schedule_date` is required in `YYYY-MM-DD` format.
    - Current block is auto-detected from IST.
    - Enhanced with JWT authentication and correlation tracking.
    """,
    response_description="Returns structured dashboard data for visualization.",
    responses={
        200: {"description": "Dashboard data retrieved successfully"},
        401: {"description": "Unauthorized - Invalid or missing JWT token"},
        404: {"description": "Data not found for given schedule_date or block"},
        422: {"description": "Validation error - Invalid date format"},
        500: {"description": "Internal Server Error - Failed to fetch dashboard data"},
    },
)
async def get_dashboard_home(
        request: Request,
        schedule_date: Optional[str] = Query(
            None,
            description="YYYY-MM-DD (IST). Defaults to today's date.",
            example="2025-01-15"
        ),
        demand_unit: PowerUnit = Query(
            PowerUnit.MW,
            description="Unit for demand graph values.",
            example=PowerUnit.KWH.value
        ),
        schedule_drawal_unit: PowerUnit = Query(
            PowerUnit.MW,
            description="Unit for schedule drawal (Inter/Intra state).",
            example=PowerUnit.KW.value
        ),
        od_ud_unit: PowerUnit = Query(
            PowerUnit.MW,
            description="Unit for OD-UD series.",
            example=PowerUnit.KWH.value
        ),
        peak_demand_unit: PowerUnit = Query(
            PowerUnit.MW,
            description="Unit for 31-day peak demand data.",
            example=PowerUnit.GW.value
        ),
        frequency_unit: FrequencyUnit = Query(
            FrequencyUnit.HZ,
            description="Unit for frequency series.",
            example=FrequencyUnit.KHZ.value
        ),
        mcp_unit: PriceUnit = Query(
            PriceUnit.RS_PER_MWH,
            description="Unit for DAM/RTM MCP values.",
            example=PriceUnit.RS_PER_KWH.value
        ),
        acp_unit: PriceUnit = Query(
            PriceUnit.RS_PER_MWH,
            description="Unit for ACP values.",
            example=PriceUnit.PAISE_PER_KWH.value
        ),
        dsm_rate_unit: PriceUnit = Query(
            PriceUnit.RS_PER_MWH,
            description="Unit for DSM rate series.",
            example=PriceUnit.RS_PER_KWH.value
        ),
        net_dsm_amount_unit: CurrencyUnit = Query(
            CurrencyUnit.RS,
            description="Unit for Net DSM amount.",
            example=CurrencyUnit.PAISE.value
        ),
        total_net_dsm_amount_unit: CurrencyUnit = Query(
            CurrencyUnit.RS,
            description="Unit for cumulative Net DSM amount.",
            example=CurrencyUnit.PAISE.value
        ),
        net_dsm_amount_scale: CurrencyScale = Query(
            CurrencyScale.UNITS,
            description="Scale Net DSM amount (units, thousands, lakhs, crores).",
            example=CurrencyScale.LAKHS.value
        ),
        total_net_dsm_amount_scale: CurrencyScale = Query(
            CurrencyScale.UNITS,
            description="Scale total Net DSM amount.",
            example=CurrencyScale.CRORES.value
        ),
        user_context: Dict[str, Any] = Depends(get_user_context),
        audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Fetch dashboard home data with enhanced authentication and logging.
    
    Args:
        request: FastAPI request object
        schedule_date: Optional date filter in YYYY-MM-DD format
        user_context: Current authenticated user context
        
    Returns:
        DashboardResponse: Dashboard data with metadata
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        unit_overrides = {
            "demand": demand_unit.value,
            "schedule_drawal": schedule_drawal_unit.value,
            "od_ud": od_ud_unit.value,
            "peak_demand": peak_demand_unit.value,
            "frequency": frequency_unit.value,
            "mcp": mcp_unit.value,
            "acp": acp_unit.value,
            "dsm_rate": dsm_rate_unit.value,
            "net_dsm_amount": net_dsm_amount_unit.value,
            "total_net_dsm_amount": total_net_dsm_amount_unit.value,
            "net_dsm_amount_scale": net_dsm_amount_scale.value,
            "total_net_dsm_amount_scale": total_net_dsm_amount_scale.value,
        }
        # Log system operation start
        audit_logger.log_system_operation(
            service="processing",
            operation="dashboard_home_fetch",
            message="Starting dashboard home data fetch operation",
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            user_id=user_id,
            units=unit_overrides
        )

        result = await dashboard_controller.get_dashboard_home(
            schedule_date=schedule_date,
            unit_overrides=unit_overrides
        )

        # Log successful user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="dashboard_home_view",
            message="User successfully fetched dashboard home data",
            username=username,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            record_count=1,
            success=True,
            units=unit_overrides
        )

        logger.info(f"Dashboard home data fetched successfully for user {user_id}, schedule_date: {schedule_date}")

        return DashboardResponse(
            data=result,
            timestamp=datetime.now().isoformat(),
            correlation_id=correlation_id
        )

    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="DashboardHomeFetchError",
            error_message=f"Failed to fetch dashboard home data: {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            schedule_date=schedule_date,
            error_type=type(e).__name__
        )

        logger.exception(f"Error fetching dashboard home data for user {user_id}, schedule_date: {schedule_date}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch dashboard home data."
        )


@router.get(
    "/capacity-chart",
    response_model=CapacityChartResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Power Plant Capacity Sunburst Chart Data",
    description="""
    Retrieves hierarchical data representing the capacity of power plants,
    suitable for rendering a sunburst chart. The data is structured to show
    capacity breakdown by various categories (e.g., fuel type, ownership).
    """,
    response_description="Returns hierarchical data for the capacity sunburst chart.",
    responses={
        200: {"description": "Capacity chart data retrieved successfully"},
        401: {"description": "Unauthorized - Invalid or missing JWT token"},
        500: {"description": "Internal Server Error - Failed to fetch capacity chart data"},
    },
)
async def get_capacity_chart(
        user_context: Dict[str, Any] = Depends(get_user_context),
        audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    API endpoint to retrieve the hierarchical data for the power plant capacity sunburst chart.
    """

    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        audit_logger.log_system_operation(
            service="processing",
            operation="capacity_chart",
            message="Starting dashboard capacity chart fetch operation",
            correlation_id=correlation_id,
            schedule_date=datetime.now().strftime("%Y-%m-%d"),
            user_id=user_id
        )

        result = await dashboard_controller.dashboard_capacity_chart()

        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="capacity_chart",
            message="User successfully fetched capacity chart data",
            username=username,
            correlation_id=correlation_id,
            schedule_date=datetime.now().strftime("%Y-%m-%d"),
            record_count=1,
            success=True
        )
        return CapacityChartResponse(
            data=result,
            timestamp=datetime.now().isoformat(),
            correlation_id=correlation_id
        )

    except Exception as e:
        # Log error event
        audit_logger.log_error_event(
            service="processing",
            error_name="DashboardCapacityChartFetchError", # Changed error name
            error_message=f"Failed to fetch dashboard capacity chart data: {str(e)}", # Changed error message
            user_id=user_id,
            correlation_id=correlation_id,
            schedule_date=datetime.now().strftime("%Y-%m-%d"),
            error_type=type(e).__name__
        )

        logger.exception(f"Error fetching dashboard capacity chart data for user {user_id}") # Changed log message
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch dashboard capacity chart data." # Changed detail message
        )


@router.get(
    "/demand/raw",
    response_model=DemandRawV2Response,
    status_code=status.HTTP_200_OK,
    summary="Demand Raw Data (V2)",
    description="""
    Fetches **raw demand data** from the V2 collection with extensive filtering and pagination options.

    ### Query Parameters:
    - `date_from`, `date_to`: Date range (YYYY-MM-DD, inclusive).
    - `from_block`, `to_block`: Time block range (1-96, inclusive).
    - `type`: Type of demand (e.g., 'DAF', 'IDF').
    - `zone`: Geographical zone (e.g., 'Puducherry').
    - `actual_min`, `actual_max`: Min/max filter for 'actual' field.
    - `drawal_min`, `drawal_max`: Min/max filter for 'drawal' field.
    - `mape_min`, `mape_max`: Min/max filter for 'MAPE' field.
    - `mpe_min`, `mpe_max`: Min/max filter for 'MPE' field.
    - `raw_freq_min`, `raw_freq_max`: Min/max filter for 'Raw_Frequency' field.
    - `schedule_min`, `schedule_max`: Min/max filter for 'Schedule' field.
    - `comp_act_min`, `comp_act_max`: Min/max filter for 'comp_act_demand' field.
    - `comp_udm_min`, `comp_udm_max`: Min/max filter for 'comp_udm' field.
    - `dsm_rate_min`, `dsm_rate_max`: Min/max filter for 'dsm_rate' field.
    - `forecast_min`, `forecast_max`: Min/max filter for 'forecast' field.
    - `forecast_freq_min`, `forecast_freq_max`: Min/max filter for 'forecasted_frequency' field.
    - `net_dsm_min`, `net_dsm_max`: Min/max filter for 'net_dsm_amount' field.
    - `rostering_min`, `rostering_max`: Min/max filter for 'rostering' field.
    - `updated_from`, `updated_to`: ISO datetime range for 'updated_at' field (inclusive).
    - `include`, `exclude`: Comma-separated list of fields to include/exclude in the response.
    - `sort`: Comma-separated 'field:dir' for sorting (e.g., 'schedule_date:asc,time_block:desc').
    - `skip`: Number of records to skip (for pagination, default 0).
    - `limit`: Maximum number of records to return (for pagination, omit for no limit).

    ### Notes:
    - All date/time parameters are interpreted in IST.
    - Enhanced with JWT authentication and correlation tracking.
    """,
    response_description="Returns a list of raw demand data entries matching the criteria.",
    responses={
        200: {"description": "Raw demand data retrieved successfully"},
        401: {"description": "Unauthorized - Invalid or missing JWT token"},
        404: {"description": "Data not found for the given filters"},
        422: {"description": "Validation error - Invalid date format or query parameters"},
        500: {"description": "Internal Server Error - Failed to fetch raw demand data"},
    },
)
async def get_demand_raw_v2(
        request: Request,
        date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD, inclusive)", example="2025-10-09"),
        date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD, inclusive)", example="2025-10-10"),
        from_block: Optional[int] = Query(None, description="Lower bound time_block (inclusive)", ge=1, le=96, example=1),
        to_block: Optional[int] = Query(None, description="Upper bound time_block (inclusive)", ge=1, le=96, example=96),
        demand_type: Optional[str] = Query(None, alias="type", description="Type of demand (e.g., DAF, IDF)", example="DAF"),
        zone: Optional[str] = Query(None, description="Geographical zone (e.g., Puducherry)", example="Puducherry"),
        actual_min: Optional[float] = Query(None, description="Actual minimum value"),
        actual_max: Optional[float] = Query(None, description="Actual maximum value"),
        drawal_min: Optional[float] = Query(None, description="Drawal minimum value"),
        drawal_max: Optional[float] = Query(None, description="Drawal maximum value"),
        mape_min: Optional[float] = Query(None, description="MAPE minimum value"),
        mape_max: Optional[float] = Query(None, description="MAPE maximum value"),
        mpe_min: Optional[float] = Query(None, description="MPE minimum value"),
        mpe_max: Optional[float] = Query(None, description="MPE maximum value"),
        raw_freq_min: Optional[float] = Query(None, description="Raw_Frequency minimum value"),
        raw_freq_max: Optional[float] = Query(None, description="Raw_Frequency maximum value"),
        schedule_min: Optional[float] = Query(None, description="Schedule minimum value"),
        schedule_max: Optional[float] = Query(None, description="Schedule maximum value"),
        comp_act_min: Optional[float] = Query(None, description="Comp_Act_Demand minimum value"),
        comp_act_max: Optional[float] = Query(None, description="Comp_Act_Demand maximum value"),
        comp_udm_min: Optional[float] = Query(None, description="Comp_UDM minimum value"),
        comp_udm_max: Optional[float] = Query(None, description="Comp_UDM maximum value"),
        dsm_rate_min: Optional[float] = Query(None, description="DSM_Rate minimum value"),
        dsm_rate_max: Optional[float] = Query(None, description="DSM_Rate maximum value"),
        forecast_min: Optional[float] = Query(None, description="Forecast minimum value"),
        forecast_max: Optional[float] = Query(None, description="Forecast maximum value"),
        forecast_freq_min: Optional[float] = Query(None, description="Forecasted_Frequency minimum value"),
        forecast_freq_max: Optional[float] = Query(None, description="Forecasted_Frequency maximum value"),
        net_dsm_min: Optional[float] = Query(None, description="Net_DSM_Amount minimum value"),
        net_dsm_max: Optional[float] = Query(None, description="Net_DSM_Amount maximum value"),
        rostering_min: Optional[float] = Query(None, description="Rostering minimum value"),
        rostering_max: Optional[float] = Query(None, description="Rostering maximum value"),
        updated_from: Optional[datetime] = Query(None, description="Updated from (ISO datetime, inclusive)", example="2025-10-08T00:00:00Z"),
        updated_to: Optional[datetime] = Query(None, description="Updated to (ISO datetime, inclusive)", example="2025-10-08T23:59:59Z"),
        include: Optional[str] = Query(None, description="Comma-separated list of fields to include (lowercase)", example="zone,time_block"),
        exclude: Optional[str] = Query(None, description="Comma-separated list of fields to exclude (lowercase)", example="_id,created_at"),
        sort: Optional[str] = Query(None, description="Comma-separated 'field:dir' for sorting (e.g., 'scheduled_at:asc,time_block:desc')", example="scheduled_at:asc,time_block:asc"),
        skip: int = Query(0, description="Number of records to skip (for pagination)", ge=0),
        limit: Optional[int] = Query(None, description="Maximum number of records to return (omit for no limit)", ge=1),
        user_context: Dict[str, Any] = Depends(get_user_context),
        audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Fetch raw demand data from the V2 collection with enhanced authentication and logging.
    
    Args:
        request: FastAPI request object
        date_from: Start date filter in YYYY-MM-DD format
        date_to: End date filter in YYYY-MM-DD format
        from_block: Start time block number (1-96)
        to_block: End time block number (1-96)
        demand_type: Type of demand (e.g., DAF, IDF)
        zone: Geographical zone
        actual_min: Minimum value for 'actual'
        actual_max: Maximum value for 'actual'
        drawal_min: Minimum value for 'drawal'
        drawal_max: Maximum value for 'drawal'
        mape_min: Minimum value for 'mape'
        mape_max: Maximum value for 'mape'
        mpe_min: Minimum value for 'mpe'
        mpe_max: Maximum value for 'mpe'
        raw_freq_min: Minimum value for 'raw_frequency'
        raw_freq_max: Maximum value for 'raw_frequency'
        schedule_min: Minimum value for 'schedule'
        schedule_max: Maximum value for 'schedule'
        comp_act_min: Minimum value for 'comp_act_demand'
        comp_act_max: Maximum value for 'comp_act_demand'
        comp_udm_min: Minimum value for 'comp_udm'
        comp_udm_max: Maximum value for 'comp_udm'
        dsm_rate_min: Minimum value for 'dsm_rate'
        dsm_rate_max: Maximum value for 'dsm_rate'
        forecast_min: Minimum value for 'forecast'
        forecast_max: Maximum value for 'forecast'
        forecast_freq_min: Minimum value for 'forecasted_frequency'
        forecast_freq_max: Maximum value for 'forecasted_frequency'
        net_dsm_min: Minimum value for 'net_dsm_amount'
        net_dsm_max: Maximum value for 'net_dsm_amount'
        rostering_min: Minimum value for 'rostering'
        rostering_max: Maximum value for 'rostering'
        updated_from: Start datetime for 'updated_at'
        updated_to: End datetime for 'updated_at'
        include: Comma-separated list of fields to include
        exclude: Comma-separated list of fields to exclude
        sort: Sorting order (e.g., 'field:asc')
        skip: Number of records to skip
        limit: Maximum number of records to return
        user_context: Current authenticated user context
        
    Returns:
        DemandRawV2Response: List of raw demand data entries with metadata
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        audit_logger.log_system_operation(
            service="processing",
            operation="demand_raw_fetch_v2",
            message="Starting demand raw data fetch operation (V2)",
            correlation_id=correlation_id,
            date_from=date_from,
            date_to=date_to,
            from_block=from_block,
            to_block=to_block,
            demand_type=demand_type,
            zone=zone,
            user_id=user_id
        )

        # Prepare filters for the controller
        filters = {
            "date_from": date_from,
            "date_to": date_to,
            "from_block": from_block,
            "to_block": to_block,
            "type": demand_type,
            "zone": zone,
            "actual_min": actual_min,
            "actual_max": actual_max,
            "drawal_min": drawal_min,
            "drawal_max": drawal_max,
            "mape_min": mape_min,
            "mape_max": mape_max,
            "mpe_min": mpe_min,
            "mpe_max": mpe_max,
            "raw_frequency_min": raw_freq_min,
            "raw_frequency_max": raw_freq_max,
            "schedule_min": schedule_min,
            "schedule_max": schedule_max,
            "comp_act_demand_min": comp_act_min,
            "comp_act_demand_max": comp_act_max,
            "comp_udm_min": comp_udm_min,
            "comp_udm_max": comp_udm_max,
            "dsm_rate_min": dsm_rate_min,
            "dsm_rate_max": dsm_rate_max,
            "forecast_min": forecast_min,
            "forecast_max": forecast_max,
            "forecasted_frequency_min": forecast_freq_min,
            "forecasted_frequency_max": forecast_freq_max,
            "net_dsm_amount_min": net_dsm_min,
            "net_dsm_amount_max": net_dsm_max,
            "rostering_min": rostering_min,
            "rostering_max": rostering_max,
            "updated_from": updated_from,
            "updated_to": updated_to,
            "include": include,
            "exclude": exclude,
            "sort": sort,
            "skip": skip,
            "limit": limit,
        }

        result_data = await dashboard_controller.get_demand_raw_v2(filters)

        if not result_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No raw demand data found for the specified filters."
            )

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="demand_raw_view_v2",
            message="User successfully fetched demand raw data (V2)",
            username=username,
            correlation_id=correlation_id,
            record_count=len(result_data),
            success=True,
            **filters # Log all filters
        )

        logger.info(f"Demand raw data (V2) fetched successfully for user {user_id}, filters: {filters}")

        # Construct the new data payload with result and meta
        data_payload = DemandRawV2DataPayload(
            result=result_data,
            meta=PaginationMeta(
                skip=skip,
                limit=limit,
                returned=len(result_data)
            )
        )

        return DemandRawV2Response(
            data=data_payload,
            timestamp=datetime.now().isoformat(),
            correlation_id=correlation_id
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="DemandRawFetchErrorV2",
            error_message=f"Failed to fetch demand raw data (V2): {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__,
            **filters # Log all filters
        )

        logger.exception(f"Error fetching demand raw data (V2) for user {user_id}, filters: {filters}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch raw demand data (V2)."
        )


@router.get(
    "/temp/demand/raw",
    response_model=DemandRawTempV2Response,
    status_code=status.HTTP_200_OK,
    summary="Temp Demand Raw Data (V2)",
    description="""
    Fetches **raw Temp demand data** from the V2 collection with extensive filtering and pagination options.

    ### Query Parameters:
    - `date_from`, `date_to`: Date range (YYYY-MM-DD, inclusive).
    - `from_block`, `to_block`: Time block range (1-96, inclusive).
    - `type`: Type of demand (e.g., 'DAF', 'IDF').
    - `zone`: Geographical zone (e.g., 'Puducherry').
    - `actual_min`, `actual_max`: Min/max filter for 'actual' field.
    - `drawal_min`, `drawal_max`: Min/max filter for 'drawal' field.
    - `mape_min`, `mape_max`: Min/max filter for 'MAPE' field.
    - `mpe_min`, `mpe_max`: Min/max filter for 'MPE' field.
    - `raw_freq_min`, `raw_freq_max`: Min/max filter for 'Raw_Frequency' field.
    - `schedule_min`, `schedule_max`: Min/max filter for 'Schedule' field.
    - `comp_act_min`, `comp_act_max`: Min/max filter for 'comp_act_demand' field.
    - `comp_udm_min`, `comp_udm_max`: Min/max filter for 'comp_udm' field.
    - `dsm_rate_min`, `dsm_rate_max`: Min/max filter for 'dsm_rate' field.
    - `forecast_min`, `forecast_max`: Min/max filter for 'forecast' field.
    - `forecast_freq_min`, `forecast_freq_max`: Min/max filter for 'forecasted_frequency' field.
    - `net_dsm_min`, `net_dsm_max`: Min/max filter for 'net_dsm_amount' field.
    - `rostering_min`, `rostering_max`: Min/max filter for 'rostering' field.
    - `updated_from`, `updated_to`: ISO datetime range for 'updated_at' field (inclusive).
    - `include`, `exclude`: Comma-separated list of fields to include/exclude in the response.
    - `sort`: Comma-separated 'field:dir' for sorting (e.g., 'schedule_date:asc,time_block:desc').
    - `skip`: Number of records to skip (for pagination, default 0).
    - `limit`: Maximum number of records to return (for pagination, omit for no limit).

    ### Notes:
    - All date/time parameters are interpreted in IST.
    - Enhanced with JWT authentication and correlation tracking.
    """,
    response_description="Returns a list of raw demand data entries matching the criteria.",
    responses={
        200: {"description": "Raw demand data retrieved successfully"},
        401: {"description": "Unauthorized - Invalid or missing JWT token"},
        404: {"description": "Data not found for the given filters"},
        422: {"description": "Validation error - Invalid date format or query parameters"},
        500: {"description": "Internal Server Error - Failed to fetch raw demand data"},
    },
)
async def get_temp_demand_raw_v2(
        request: Request,
        date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD, inclusive)", example="2025-10-09"),
        date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD, inclusive)", example="2025-10-10"),
        from_block: Optional[int] = Query(None, description="Lower bound time_block (inclusive)", ge=1, le=96, example=1),
        to_block: Optional[int] = Query(None, description="Upper bound time_block (inclusive)", ge=1, le=96, example=96),
        raw_freq_min: Optional[float] = Query(None, description="Raw_Frequency minimum value"),
        raw_freq_max: Optional[float] = Query(None, description="Raw_Frequency maximum value"),
        rostering_min: Optional[float] = Query(None, description="Rostering minimum value"),
        rostering_max: Optional[float] = Query(None, description="Rostering maximum value"),
        updated_from: Optional[datetime] = Query(None, description="Updated from (ISO datetime, inclusive)", example="2025-10-08T00:00:00Z"),
        updated_to: Optional[datetime] = Query(None, description="Updated to (ISO datetime, inclusive)", example="2025-10-08T23:59:59Z"),
        include: Optional[str] = Query(None, description="Comma-separated list of fields to include (lowercase)", example="zone,time_block"),
        exclude: Optional[str] = Query(None, description="Comma-separated list of fields to exclude (lowercase)", example="_id,created_at"),
        sort: Optional[str] = Query(None, description="Comma-separated 'field:dir' for sorting (e.g., 'scheduled_at:asc,time_block:desc')", example="scheduled_at:asc,time_block:asc"),
        skip: int = Query(0, description="Number of records to skip (for pagination)", ge=0),
        limit: Optional[int] = Query(None, description="Maximum number of records to return (omit for no limit)", ge=1),
        user_context: Dict[str, Any] = Depends(get_user_context),
        audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Fetch raw Temp demand data from the V2 collection with enhanced authentication and logging.
    
    Args:
        request: FastAPI request object
        date_from: Start date filter in YYYY-MM-DD format
        date_to: End date filter in YYYY-MM-DD format
        from_block: Start time block number (1-96)
        to_block: End time block number (1-96)
        raw_freq_min: Minimum value for 'raw_frequency'
        raw_freq_max: Maximum value for 'raw_frequency'
        rostering_min: Minimum value for 'rostering'
        rostering_max: Maximum value for 'rostering'
        updated_from: Start datetime for 'updated_at'
        updated_to: End datetime for 'updated_at'
        include: Comma-separated list of fields to include
        exclude: Comma-separated list of fields to exclude
        sort: Sorting order (e.g., 'field:asc')
        skip: Number of records to skip
        limit: Maximum number of records to return
        user_context: Current authenticated user context
        
    Returns:
        DemandRawV2Response: List of raw demand data entries with metadata
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        audit_logger.log_system_operation(
            service="processing",
            operation="demand_raw_fetch_v2",
            message="Starting demand raw data fetch operation (V2)",
            correlation_id=correlation_id,
            date_from=date_from,
            date_to=date_to,
            from_block=from_block,
            to_block=to_block,
            user_id=user_id
        )

        # Prepare filters for the controller
        filters = {
            "date_from": date_from,
            "date_to": date_to,
            "from_block": from_block,
            "to_block": to_block,            
            "raw_frequency_min": raw_freq_min,
            "raw_frequency_max": raw_freq_max,
            "rostering_min": rostering_min,
            "rostering_max": rostering_max,
            "updated_from": updated_from,
            "updated_to": updated_to,
            "include": include,
            "exclude": exclude,
            "sort": sort,
            "skip": skip,
            "limit": limit,
        }

        result_data = await dashboard_controller.get_temp_demand_raw_v2(filters)

        if not result_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No Temp raw demand data found for the specified filters."
            )

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="demand_raw_view_v2",
            message="User successfully fetched demand raw data (V2)",
            username=username,
            correlation_id=correlation_id,
            record_count=len(result_data),
            success=True,
            **filters # Log all filters
        )

        logger.info(f"Demand raw data (V2) fetched successfully for user {user_id}, filters: {filters}")

        # Construct the new data payload with result and meta
        data_payload = DemandTempRawV2DataPayload(
            result=result_data,
            meta=PaginationMeta(
                skip=skip,
                limit=limit,
                returned=len(result_data)
            )
        )

        return DemandRawTempV2Response(
            data=data_payload,
            timestamp=datetime.now().isoformat(),
            correlation_id=correlation_id
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="DemandRawFetchErrorV2",
            error_message=f"Failed to fetch demand raw data (V2): {str(e)}",
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__,
            **filters # Log all filters
        )

        logger.exception(f"Error fetching demand raw data (V2) for user {user_id}, filters: {filters}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch raw demand data (V2)."
        )
