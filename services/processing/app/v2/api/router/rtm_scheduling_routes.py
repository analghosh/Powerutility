from typing import Optional, Dict, Any, List, Union
from fastapi import APIRouter, Query, HTTPException, status, Depends, Request
from pydantic import BaseModel, Field, RootModel
from datetime import datetime
import logging

from services.processing.app.v2.controller.rtm_scheduling_controller import rtm_scheduling_controller
from ...dependencies import get_user_context
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger

router = APIRouter(prefix="/rtm", tags=["RTM Scheduling V2"])
logger = logging.getLogger(__name__)


# Response Models with Examples
class PlantResultItem(BaseModel):
    """Individual plant record in the result array"""
    plant_name: Optional[str] = Field(None, description="Name of the plant", example="NTPC KORBA")
    key: Optional[str] = Field(None, description="Plant ID/key", example="NTPC_KORBA")
    schedule_date: Optional[str] = Field(None, description="Schedule date in YYYY-MM-DD format", example="2025-11-02")
    category: Optional[str] = Field(None, description="Organization category", example="NTPC")
    px_price: Optional[float] = Field(None, description="Power exchange price", example=3.25)
    remaining_volume: Optional[float] = Field(None, description="Remaining unscheduled volume in MW", example=0.0)
    scheduling_requisition_type: Optional[str] = Field(None, description="Scheduling requisition type", example="NA")
    price: Optional[Union[int, float]] = Field(None, description="Price value", example=0)
    RMP: Optional[float] = Field(None, description="RMP value", example=3.75)
    plant_TM_value: Optional[float] = Field(None, description="Plant TM value", example=0.0)
    genrated_sg_volume: Optional[float] = Field(None, description="Generated SG volume", example=0.0)
    Final_plant_RMP_volume: Optional[float] = Field(None, description="Final plant RMP volume", example=0.0)
    actual_plant_volume: Optional[float] = Field(None, description="Actual plant volume", example=0.0)
    schedule_date_timestamp: Optional[int] = Field(None, description="Schedule date Unix timestamp", example=1762021800)

    model_config = {
        "json_schema_extra": {
            "example": {
                "plant_name": "Pondicherry Power Corporation Ltd Gas Power Plant",
                "key": "PPCLGPP",
                "schedule_date": "2025-11-01",
                "category": "PPCL",
                "px_price": 10.0,
                "remaining_volume": -58.66,
                "scheduling_requisition_type": "NA",
                "price": 0,
                "RMP": 0.0,
                "plant_TM_value": 0.0,
                "genrated_sg_volume": 5.05,
                "Final_plant_RMP_volume": 5.05,
                "actual_plant_volume": 5.05,
                "schedule_date_timestamp": 1761935400
            }
        },
        "extra": "ignore"  # Ignore extra fields from MongoDB
    }


class RTMSchedulingTableRecord(BaseModel):
    """RTM Scheduling Table record structure"""
    time_block: int = Field(..., description="Time block number (1-96)", example=1)
    full_time: Optional[str] = Field(None, description="Time range string", example=None)
    start_time: Optional[str] = Field(None, description="Start time", example=None)
    end_time: Optional[str] = Field(None, description="End time", example=None)
    dc_sg_type: Optional[str] = Field(None, description="DC/SG type identifier", example="SG")
    schedule_date: Optional[str] = Field(None, description="Schedule date in YYYY-MM-DD format", example="2025-11-01")
    schedule_date_timestamp: Optional[int] = Field(None, description="Schedule date Unix timestamp", example=1761935400)
    totalAvailability: Optional[float] = Field(None, description="Total available supply in MW", example=371.0)
    demand_forecast: Optional[float] = Field(None, description="Demand forecast in MW", example=430.0)
    forecast_source: Optional[str] = Field(
        None, description="Forecast source: Auto (rtm_forecast_api) or Manual (csv)", example="Auto"
    )
    surplus_deficit: Optional[float] = Field(None, description="Surplus (+) or deficit (-) in MW", example=-59.0)
    rtm_forecasted_price: Optional[float] = Field(None, description="RTM forecasted price in Rs/MWh", example=3300.96)
    surplus_deficit_updated: Optional[float] = Field(None, description="Updated surplus/deficit in MW", example=-58.66)
    total_availablity_updated: Optional[float] = Field(None, description="Updated total availability in MW", example=371.34)
    genrated_sg_volume: Optional[float] = Field(None, description="Generated SG volume in MW", example=5.05)
    result: List[PlantResultItem] = Field(..., description="Array of plant records for this time block")

    model_config = {
        "json_schema_extra": {
            "example": {
                "time_block": 1,
                "full_time": None,
                "start_time": None,
                "end_time": None,
                "dc_sg_type": "SG",
                "schedule_date": "2025-11-01",
                "schedule_date_timestamp": 1761935400,
                "totalAvailability": 371.0,
                "demand_forecast": 430.0,
                "forecast_source": "Auto",
                "surplus_deficit": -59.0,
                "rtm_forecasted_price": 3300.96,
                "surplus_deficit_updated": -58.66,
                "total_availablity_updated": 371.34,
                "genrated_sg_volume": 5.05,
                "result": []
            }
        },
        "extra": "ignore"  # Ignore extra fields from MongoDB
    }


class RTMSchedulingTableResponse(RootModel[Union[List[RTMSchedulingTableRecord], Dict[str, str]]]):
    """Response model for RTM Scheduling Table API"""
    root: Union[List[RTMSchedulingTableRecord], Dict[str, str]]

    model_config = {
        "json_schema_extra": {
            "examples": [
                # Success response - List of records
                [
                    {
                        "time_block": 1,
                        "full_time": "00:00-00:15",
                        "start_time": "00:00",
                        "end_time": "00:15",
                        "dc_sg_type": "SG",
                        "schedule_date": "2025-11-02",
                        "totalAvailability": 5000.0,
                        "demand_forecast": 4800.0,
                        "surplus_deficit": 200.0,
                        "rtm_forecasted_price": 3.50,
                        "result": [
                            {
                                "plant_name": "NTPC KORBA",
                                "key": "NTPC_KORBA",
                                "volume": 150.5,
                                "schedule_date": "2025-11-02",
                                "category": "NTPC",
                                "VC_Price": 2.50
                            }
                        ]
                    },
                    {
                        "time_block": 2,
                        "full_time": "00:15-00:30",
                        "start_time": "00:15",
                        "end_time": "00:30",
                        "dc_sg_type": "SG",
                        "schedule_date": "2025-11-02",
                        "totalAvailability": 5100.0,
                        "demand_forecast": 4900.0,
                        "surplus_deficit": 200.0,
                        "rtm_forecasted_price": 3.55,
                        "result": []
                    }
                ],
                # No data response
                {
                    "detail": "No data "
                }
            ]
        }
    }


class BlockCalculationData(BaseModel):
    """Per-block calculation data matching V1 structure"""
    total_supply_updated: float = Field(..., description="Total supply updated for this time block", example=371.22)
    surplus_deficit_updated: float = Field(..., description="Surplus/deficit updated for this time block", example=-58.66)
    
    model_config = {
        "extra": "ignore"  # Ignore any extra fields from MongoDB
    }


class RTMSchedulingCalculationResponse(BaseModel):
    """Response model for RTM Scheduling Calculation API - matches V1 structure"""
    data: Dict[str, BlockCalculationData] = Field(
        ..., 
        description="Dictionary keyed by time_block (1-96) containing calculation results per block",
        example={
            "1": {
                "total_supply_updated": 371.22,
                "surplus_deficit_updated": -58.66
            },
            "2": {
                "total_supply_updated": 380.15,
                "surplus_deficit_updated": -48.85
            }
        }
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "data": {
                    "1": {
                        "total_supply_updated": 371.22,
                        "surplus_deficit_updated": -58.66
                    },
                    "2": {
                        "total_supply_updated": 380.15,
                        "surplus_deficit_updated": -48.85
                    }
                }
            }
        }
    }


@router.get(
    "/scheduling-table",
    response_model=RTMSchedulingTableResponse,
    response_model_exclude_none=True,
    summary="Get RTM Scheduling Table Data",
    description="""
    Returns RTM scheduling table data for all plants organized by time blocks (1-96) for a specific schedule date.
    
    **Query Parameters:**
    - `schedule_date` (required): Date in YYYY-MM-DD format
    
    **Response Structure:**
    - Returns a list of time block records, each containing:
        - Time block information (number, time range, schedule date)
        - Total availability and demand forecast
        - Surplus/deficit calculations
        - RTM forecasted price
        - Array of plant records (`result`) with individual plant scheduling data
    
    **Key Fields:**
    - `time_block`: Block number (1-96, representing 15-minute intervals from 00:00 to 23:45)
    - `totalAvailability`: Total available supply in MW
    - `demand_forecast`: Forecasted demand in MW
    - `surplus_deficit`: Net surplus (+) or deficit (-) in MW
    - `result`: Array of plant-specific scheduling records
    
    **No Data Response:**
    If no data is available, returns: `{"detail": "No data for <schedule_date>"}`
    
    **Authentication:** Required (via Bearer token)
    **Audit:** All requests are logged with user context and correlation ID.
    """,
    response_description="List of RTM scheduling table records grouped by time block, or error message if no data available.",
    responses={
        200: {
            "description": "Successful response with scheduling table data or no data message",
            "content": {
                "application/json": {
                    "examples": {
                        "success": {
                            "summary": "Success with data",
                            "value": [
                                {
                                    "time_block": 1,
                                    "full_time": "00:00-00:15",
                                    "start_time": "00:00",
                                    "end_time": "00:15",
                                    "dc_sg_type": "SG",
                                    "schedule_date": "2025-11-02",
                                    "totalAvailability": 5000.0,
                                    "demand_forecast": 4800.0,
                                    "surplus_deficit": 200.0,
                                    "rtm_forecasted_price": 3.50,
                                    "result": [
                                        {
                                            "plant_name": "NTPC KORBA",
                                            "key": "NTPC_KORBA",
                                            "plant_id": "NTPC_KORBA",
                                            "volume": 150.5,
                                            "schedule_date": "2025-11-02",
                                            "category": "NTPC",
                                            "VC_Price": 2.50,
                                            "remaining_volume": 0.0,
                                            "scheduling_req_type": "NA",
                                            "market_clearing_price": 3.50
                                        }
                                    ]
                                }
                            ]
                        },
                        "no_data": {
                            "summary": "No data available",
                            "value": {"detail": "No data for 2025-11-02"}
                        }
                    }
                }
            }
        }
    }
)
async def get_rtm_scheduling_table(
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Get RTM scheduling table data for all plants for a specific schedule date.
    
    This endpoint provides comprehensive RTM scheduling information including:
    - Time block-wise aggregation (96 blocks per day)
    - Total availability and demand forecasts
    - Surplus/deficit calculations
    - Individual plant scheduling records
    
    Returns data for the specified schedule date.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        logger.info(f"[RTM Scheduling V2] Starting rtm-scheduling-table request for - User: {username} (ID: {user_id}), Correlation ID: {correlation_id}")
        
        audit_logger.log_system_operation(
            service="processing",
            operation="rtm_scheduling_table_fetch",
            message=f"User {username} (ID: {user_id}) requested RTM scheduling table data for",
            correlation_id=correlation_id,
            user_id=user_id
        )
        schedule_date = datetime.now().strftime("%Y-%m-%d")
        result = await rtm_scheduling_controller.rtm_scheduling_table(
            user_context=user_context,
            audit_logger=audit_logger
        )
        
        record_count = len(result) if isinstance(result, list) else (1 if result else 0)
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="rtm_scheduling_table_view",
            message=f"User {username} successfully fetched RTM scheduling table data for ",
            username=username,
            correlation_id=correlation_id,
            record_count=record_count,
            success=True
        )
        
        logger.info(f"[RTM Scheduling V2] Completed rtm-scheduling-table request for  - User: {username}, Records: {record_count}, Correlation ID: {correlation_id}")
        
        return result
        
    except HTTPException as e:
        logger.warning(f"[RTM Scheduling V2] HTTP error in rtm-scheduling-table for - User: {username}, Status: {e.status_code}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMSchedulingTableHTTPError",
            error_message=str(e.detail),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type="HTTPException",
            status_code=e.status_code
        )
        raise
    except Exception as e:
        logger.exception(f"[RTM Scheduling V2] Unexpected error in rtm-scheduling-table for - User: {username}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMSchedulingTableError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process RTM scheduling table request: {str(e)}"
        )


@router.get(
    "/run-scheduling",
    response_model=RTMSchedulingCalculationResponse,
    response_model_exclude_none=True,
    summary="Calculate RTM Scheduling Table (matches V1 behavior)",
    description="""
    Performs RTM scheduling calculation for a specific schedule date and returns calculation results per time block.
    **Matches V1 API behavior exactly.**

    **Response Structure:**
    ```json
    {
        "data": {
            "1": {
                "total_supply_updated": 371.22,
                "surplus_deficit_updated": -58.66
            },
            "2": {
                "total_supply_updated": 380.15,
                "surplus_deficit_updated": -48.85
            }
        }
    }
    ```
    
    **Key Fields:**
    - `data`: Dictionary keyed by time_block (1-96) containing per-block calculations
    - `total_supply_updated`: Total supply updated for each time block (float)
    - `surplus_deficit_updated`: Surplus/deficit updated for each time block (float)
    
    **Authentication:** Required (via Bearer token)
    **Audit:** All requests are logged with user context and correlation ID.
    """,
    response_description="Dictionary containing calculation results per time block, matching V1 format",
    responses={
        200: {
            "description": "Successful response with per-block calculation results",
            "content": {
                "application/json": {
                    "example": {
                        "data": {
                            "1": {
                                "total_supply_updated": 371.22,
                                "surplus_deficit_updated": -58.66
                            },
                            "2": {
                                "total_supply_updated": 380.15,
                                "surplus_deficit_updated": -48.85
                            }
                        }
                    }
                }
            }
        }
    }
)
async def get_rtm_scheduling_table_calculation(
    request: Request,
    run_with_exchange: bool = Query(False, description="When True, writes computed sell/buy bids back to exchange_serve after scheduling."),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Get RTM scheduling calculation table data.
    
    This endpoint provides detailed scheduling calculation results including:
    - Demand and supply forecasts
    - Market clearing prices
    - Plant-specific scheduling volumes
    - RMP (Real-time Market Price) calculations
    - Surplus/deficit analysis
    
    The calculation is performed for the specified schedule date (or yesterday if not provided, to match V1 behavior).
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    schedule_date = datetime.now().strftime("%Y-%m-%d")
    
    try:
        logger.info(f"[RTM Scheduling V2] Starting rtm-scheduling_table_calculation request - User: {username} (ID: {user_id}), Schedule Date: {schedule_date}, Correlation ID: {correlation_id}")
        
        schedule_date = datetime.now().strftime("%Y-%m-%d")
        audit_logger.log_system_operation(
            service="processing",
            operation="rtm_scheduling_table_calculation_fetch",
            message=f"User {username} (ID: {user_id}) requested RTM scheduling calculation for date: {schedule_date}",
            correlation_id=correlation_id,
            user_id=user_id,
            details={"schedule_date": schedule_date}
        )
        
        result = await rtm_scheduling_controller.rtm_scheduling_cal_table(
            schedule_date=schedule_date,
            run_with_exchange=run_with_exchange,
            user_context=user_context,
            audit_logger=audit_logger
        )
        
        # Validate result structure - should be {"data": {...}}
        if not isinstance(result, dict):
            logger.error(f"[RTM Scheduling V2] Invalid result type from controller: {type(result)}, User: {username}, Correlation ID: {correlation_id}")
            audit_logger.log_error_event(
                service="processing",
                error_name="RTMSchedulingCalculationInvalidResponse",
                error_message=f"Invalid response format: expected dict, got {type(result)}",
                user_id=user_id,
                correlation_id=correlation_id,
                error_type="ValueError",
                details={"schedule_date": schedule_date}
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Invalid response format from scheduling calculation service"
            )
        
        # Validate that result has "data" key
        if "data" not in result:
            logger.error(f"[RTM Scheduling V2] Missing 'data' key in result, User: {username}, Correlation ID: {correlation_id}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Invalid response format: missing 'data' key"
            )
        
        # Get count of blocks for logging
        data_dict = result.get("data", {})
        block_count = len(data_dict) if isinstance(data_dict, dict) else 0
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="rtm_scheduling_table_calculation_view",
            message=f"User {username} successfully calculated RTM scheduling for date: {schedule_date}, Blocks: {block_count}",
            username=username,
            correlation_id=correlation_id,
            record_count=block_count,
            success=True,
            details={"schedule_date": schedule_date, "block_count": block_count}
        )
        
        logger.info(f"[RTM Scheduling V2] Completed rtm-scheduling_table_calculation request - User: {username}, Schedule Date: {schedule_date}, Blocks Calculated: {block_count}, Correlation ID: {correlation_id}")
        
        # Return result directly - controller already wraps in {"data": ...} format
        return result
        
    except HTTPException as e:
        logger.warning(f"[RTM Scheduling V2] HTTP error in rtm-scheduling_table_calculation - User: {username}, Schedule Date: {schedule_date}, Status: {e.status_code}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMSchedulingCalculationHTTPError",
            error_message=str(e.detail),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type="HTTPException",
            status_code=e.status_code,
            details={"schedule_date": schedule_date}
        )
        raise
    except Exception as e:
        logger.exception(f"[RTM Scheduling V2] Unexpected error in rtm-scheduling_table_calculation - User: {username}, Schedule Date: {schedule_date}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMSchedulingCalculationError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__,
            details={"schedule_date": schedule_date}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process RTM scheduling calculation request: {str(e)}"
        )


@router.get(
    "/revision-no",
    response_model_exclude_none=True,
    summary="Get Revision Number",
    description="""
    Get latest revision numbers for ISP and SLDC categories.
    Mirrors PHP GetRevisionNo() method.
    
    **Request Parameters:**
    - `schedule_date` (required): Date in YYYY-MM-DD format
    
    **Response:**
    - `srldc_revision_DC`: SR-LDC (State Regional Load Dispatch Centre) revision DC number
    - `srldc_revision_SG`: SR-LDC revision SG number
    - `sldc_revision_DC`: SLDC (State Load Dispatch Centre) revision DC number
    - `sldc_revision_SG`: SLDC revision SG number
    
    **Authentication:** Required (via Bearer token)
    **Audit:** All requests are logged with user context and correlation ID.
    """,
    responses={
        200: {
            "description": "Successful response with revision numbers",
            "content": {
                "application/json": {
                    "example": {
                        "srldc_revision_DC": 5,
                        "srldc_revision_SG": 3,
                        "sldc_revision_DC": 2,
                        "sldc_revision_SG": 2
                    }
                }
            }
        }
    }
)
async def get_revision_no(
    request: Request,
    schedule_date: str = Query(..., description="Schedule date in YYYY-MM-DD format", example="2025-11-02", regex=r"^\d{4}-\d{2}-\d{2}$"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Get revision number for a schedule date.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        logger.info(f"[RTM Scheduling V2] Starting revision-no request - User: {username} (ID: {user_id}), Schedule Date: {schedule_date}, Correlation ID: {correlation_id}")
        
        audit_logger.log_system_operation(
            service="processing",
            operation="rtm_scheduling_revision_no_fetch",
            message=f"User {username} (ID: {user_id}) requested revision number for date: {schedule_date}",
            correlation_id=correlation_id,
            user_id=user_id,
            details={"schedule_date": schedule_date}
        )
        
        result = await rtm_scheduling_controller.get_revision_no(
            schedule_date=schedule_date,
            user_context=user_context,
            audit_logger=audit_logger
        )
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="rtm_scheduling_revision_no_view",
            message=f"User {username} successfully fetched revision number for date: {schedule_date}",
            username=username,
            correlation_id=correlation_id,
            success=True,
            details={"schedule_date": schedule_date, "result": result}
        )
        
        logger.info(f"[RTM Scheduling V2] Completed revision-no request - User: {username}, Schedule Date: {schedule_date}, Correlation ID: {correlation_id}")
        
        return result
        
    except HTTPException as e:
        logger.warning(f"[RTM Scheduling V2] HTTP error in revision-no - User: {username}, Status: {e.status_code}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMSchedulingRevisionNoHTTPError",
            error_message=str(e.detail),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type="HTTPException",
            status_code=e.status_code,
            details={"schedule_date": schedule_date}
        )
        raise
    except Exception as e:
        logger.exception(f"[RTM Scheduling V2] Unexpected error in revision-no - User: {username}, Schedule Date: {schedule_date}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMSchedulingRevisionNoError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__,
            details={"schedule_date": schedule_date}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get revision number: {str(e)}"
        )


@router.get(
    "/graph-data",
    response_model_exclude_none=True,
    summary="Get Scheduling Graph Data",
    description="""
    Get aggregated data for graph visualization.
    Mirrors PHP getSchedulingGraphData() method.
    
    **Query Parameters:**
    - `schedule_date` (required): Date in YYYY-MM-DD format
    - `from_block` (optional): Starting time block (1-96). If not provided, uses all blocks
    - `to_block` (optional): Ending time block (1-96). If not provided, uses all blocks
    
    **Response:**
    List of time block data with:
    - `timeBlock`: Time block number
    - `start_time`, `end_time`, `full_time`: Time range
    - `total_avialability`: Total availability
    - `demand_forecast`: Demand forecast
    - `surplus_deficit`: Surplus/deficit value
    
    **Authentication:** Required (via Bearer token)
    **Audit:** All requests are logged with user context and correlation ID.
    """,
    responses={
        200: {
            "description": "Successful response with graph data",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "timeBlock": 1,
                            "start_time": "00:00",
                            "end_time": "00:15",
                            "full_time": "00:00-00:15",
                            "total_avialability": 1000.0,
                            "demand_forecast": 950.0,
                            "surplus_deficit": 50.0
                        }
                    ]
                }
            }
        }
    }
)
async def get_graph_data(
    request: Request,
    schedule_date: str = Query(..., description="Schedule date in YYYY-MM-DD format", example="2025-11-02", regex=r"^\d{4}-\d{2}-\d{2}$"),
    from_block: Optional[int] = Query(None, ge=1, le=96, description="Starting time block"),
    to_block: Optional[int] = Query(None, ge=1, le=96, description="Ending time block"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Get scheduling graph data for visualization.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        logger.info(f"[RTM Scheduling V2] Starting graph-data request - User: {username} (ID: {user_id}), Schedule Date: {schedule_date}, Correlation ID: {correlation_id}")
        
        audit_logger.log_system_operation(
            service="processing",
            operation="rtm_scheduling_graph_data_fetch",
            message=f"User {username} (ID: {user_id}) requested graph data for date: {schedule_date}, from_block: {from_block}, to_block: {to_block}",
            correlation_id=correlation_id,
            user_id=user_id,
            details={"schedule_date": schedule_date, "from_block": from_block, "to_block": to_block}
        )
        
        result = await rtm_scheduling_controller.get_scheduling_graph_data(
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
            user_context=user_context,
            audit_logger=audit_logger
        )
        
        record_count = len(result) if isinstance(result, list) else 0
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="rtm_scheduling_graph_data_view",
            message=f"User {username} successfully fetched graph data for date: {schedule_date}, from_block: {from_block}, to_block: {to_block}, Records: {record_count}",
            username=username,
            correlation_id=correlation_id,
            record_count=record_count,
            success=True,
            details={"schedule_date": schedule_date, "from_block": from_block, "to_block": to_block}
        )
        
        logger.info(f"[RTM Scheduling V2] Completed graph-data request - User: {username}, Schedule Date: {schedule_date}, Records: {record_count}, Correlation ID: {correlation_id}")
        
        return result
        
    except HTTPException as e:
        logger.warning(f"[RTM Scheduling V2] HTTP error in graph-data - User: {username}, Status: {e.status_code}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMSchedulingGraphDataHTTPError",
            error_message=str(e.detail),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type="HTTPException",
            status_code=e.status_code,
            details={"schedule_date": schedule_date, "from_block": from_block, "to_block": to_block}
        )
        raise
    except Exception as e:
        logger.exception(f"[RTM Scheduling V2] Unexpected error in graph-data - User: {username}, Schedule Date: {schedule_date}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMSchedulingGraphDataError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__,
            details={"schedule_date": schedule_date, "from_block": from_block, "to_block": to_block}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get graph data: {str(e)}"
        )


@router.get(
    "/realtime-data",
    response_model_exclude_none=True,
    summary="Get Real-time Scheduling Table Data",
    description="""
    Get real-time scheduling data for current and upcoming blocks.
    Mirrors PHP getRealtimeSchedulingTbData() method.
    
    **Query Parameters:**
    - `schedule_date` (required): Date in YYYY-MM-DD format
    - `from_block` (optional): Starting time block (default: calculated from current time)
    - `to_block` (optional): Ending time block (default: calculated from current time)
    
    **Response:**
    Dictionary with:
    - `table_data`: Plant-wise scheduling data organized by time blocks
    - `Block_data`: Block-level aggregated data
    
    **Authentication:** Required (via Bearer token)
    **Audit:** All requests are logged with user context and correlation ID.
    """,
    responses={
        200: {
            "description": "Successful response with real-time scheduling data",
            "content": {
                "application/json": {
                    "example": {
                        "table_data": {},
                        "Block_data": {}
                    }
                }
            }
        }
    }
)
async def get_realtime_data(
    request: Request,
    schedule_date: str = Query(..., description="Schedule date in YYYY-MM-DD format", example="2025-11-02", regex=r"^\d{4}-\d{2}-\d{2}$"),
    from_block: Optional[int] = Query(None, description="Starting time block (1-96). If not provided, uses calculated current block", ge=1, le=96),
    to_block: Optional[int] = Query(None, description="Ending time block (1-96). If not provided, uses calculated end block", ge=1, le=96),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Get real-time scheduling table data.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        logger.info(f"[RTM Scheduling V2] Starting realtime-data request - User: {username} (ID: {user_id}), Schedule Date: {schedule_date}, Correlation ID: {correlation_id}")
        
        audit_logger.log_system_operation(
            service="processing",
            operation="rtm_scheduling_realtime_data_fetch",
            message=f"User {username} (ID: {user_id}) requested real-time data for date: {schedule_date}, from_block: {from_block}, to_block: {to_block}",
            correlation_id=correlation_id,
            user_id=user_id,
            details={"schedule_date": schedule_date, "from_block": from_block, "to_block": to_block}
        )
        
        result = await rtm_scheduling_controller.get_realtime_scheduling_tb_data(
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
            user_context=user_context,
            audit_logger=audit_logger
        )
        
        block_count = len(result.get('Block_data', {})) if isinstance(result, dict) else 0
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="rtm_scheduling_realtime_data_view",
            message=f"User {username} successfully fetched real-time data for date: {schedule_date}, from_block: {from_block}, to_block: {to_block}, Blocks: {block_count}",
            username=username,
            correlation_id=correlation_id,
            record_count=block_count,
            success=True,
            details={"schedule_date": schedule_date, "from_block": from_block, "to_block": to_block}
        )
        
        logger.info(f"[RTM Scheduling V2] Completed realtime-data request - User: {username}, Schedule Date: {schedule_date}, Blocks: {block_count}, Correlation ID: {correlation_id}")
        
        return result
        
    except HTTPException as e:
        logger.warning(f"[RTM Scheduling V2] HTTP error in realtime-data - User: {username}, Status: {e.status_code}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMSchedulingRealtimeDataHTTPError",
            error_message=str(e.detail),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type="HTTPException",
            status_code=e.status_code,
            details={"schedule_date": schedule_date, "from_block": from_block, "to_block": to_block}
        )
        raise
    except Exception as e:
        logger.exception(f"[RTM Scheduling V2] Unexpected error in realtime-data - User: {username}, Schedule Date: {schedule_date}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMSchedulingRealtimeDataError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__,
            details={"schedule_date": schedule_date, "from_block": from_block, "to_block": to_block}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get realtime data: {str(e)}"
        )


@router.get(
    "/all-scheduling-data",
    response_model_exclude_none=True,
    summary="Get All Scheduling Data",
    description="""
    Get historical scheduling data across multiple dates.
    Mirrors PHP getAllSchedulingData() method.
    
    **Request Parameters:**
    - `exclude_date_type` (optional): "exclude_1" (today-tomorrow), "exclude_2" (yesterday-today), 
                                     "all" (yesterday-tomorrow, default)
    
    **Response:**
    List of scheduling records with complete plant and block information.
    
    **Authentication:** Required (via Bearer token)
    **Audit:** All requests are logged with user context and correlation ID.
    """,
    responses={
        200: {
            "description": "Successful response with all scheduling data",
            "content": {
                "application/json": {
                    "example": []
                }
            }
        }
    }
)
async def get_all_scheduling_data(
    request: Request,
    exclude_date_type: str = Query("all", description="Date exclusion type: exclude_1, exclude_2, or all", example="all"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Get all scheduling data.
    """
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        logger.info(f"[RTM Scheduling V2] Starting all-scheduling-data request - User: {username} (ID: {user_id}), Exclude Date Type: {exclude_date_type}, Correlation ID: {correlation_id}")
        
        audit_logger.log_system_operation(
            service="processing",
            operation="rtm_scheduling_all_data_fetch",
            message=f"User {username} (ID: {user_id}) requested all scheduling data",
            correlation_id=correlation_id,
            user_id=user_id,
            details={"exclude_date_type": exclude_date_type}
        )
        
        result = await rtm_scheduling_controller.get_all_scheduling_data(
            exclude_date_type=exclude_date_type,
            user_context=user_context,
            audit_logger=audit_logger
        )
        
        record_count = len(result) if isinstance(result, list) else 0
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="rtm_scheduling_all_data_view",
            message=f"User {username} successfully fetched all scheduling data, Records: {record_count}",
            username=username,
            correlation_id=correlation_id,
            record_count=record_count,
            success=True,
            details={"exclude_date_type": exclude_date_type}
        )
        
        logger.info(f"[RTM Scheduling V2] Completed all-scheduling-data request - User: {username}, Records: {record_count}, Correlation ID: {correlation_id}")
        
        return result
        
    except HTTPException as e:
        logger.warning(f"[RTM Scheduling V2] HTTP error in all-scheduling-data - User: {username}, Status: {e.status_code}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMSchedulingAllDataHTTPError",
            error_message=str(e.detail),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type="HTTPException",
            status_code=e.status_code,
            details={"exclude_date_type": exclude_date_type}
        )
        raise
    except Exception as e:
        logger.exception(f"[RTM Scheduling V2] Unexpected error in all-scheduling-data - User: {username}, Correlation ID: {correlation_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="RTMSchedulingAllDataError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__,
            details={"exclude_date_type": exclude_date_type}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get all scheduling data: {str(e)}"
        )
