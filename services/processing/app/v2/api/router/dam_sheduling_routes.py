"""
DAM Scheduling Routes for V2 API
Simplified API with only essential endpoints.
"""

from fastapi import APIRouter, Request, UploadFile, File, Depends, Query, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional, List
from datetime import datetime
import logging

from ...controller.dam_scheduling_controller import dam_scheduling_controller
from ...dependencies import get_user_context
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger
from ...repository.base_repository import _serialize_value

router = APIRouter(prefix="/dam-scheduling", tags=["DAM Scheduling V2"])

logger = logging.getLogger(__name__)


class V2Response(BaseModel):
    data: Any
    timestamp: str
    correlation_id: Optional[str]


def _request_info(request: Request) -> Dict[str, Any]:
    """Extract request information for audit logging."""
    return {
        "method": request.method,
        "url": str(request.url),
        "headers": dict(request.headers),
        "client_host": request.client.host if request.client else None
    }


@router.post("/upload", response_model=V2Response, summary="Upload DAM scheduling file for bulk upsert")
async def upload_dam_scheduling_file(
    request: Request,
    file: UploadFile = File(...),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Upload and process DAM scheduling file for bulk upsert."""
    correlation_id = user_context.get("correlation_id")
    
    audit_logger.log_system_operation(
        service="dam_scheduling_service",
        operation="upload_scheduling_file",
        message=f"User {user_context.get('user_id', 'anonymous')} uploaded a DAM scheduling file",
        correlation_id=correlation_id,
        success=True,
        details={"filename": file.filename}
    )
    
    result = await dam_scheduling_controller.process_scheduling_file(
        file, 
        actor=user_context, 
        request_info=_request_info(request)
    )
    
    audit_logger.log_user_action(
        service="processing",
        user_id=user_context.get("user_id", "anonymous"),
        action="upload",
        message=f"User {user_context.get('user_id', 'anonymous')} uploaded DAM scheduling file successfully",
        username=user_context.get("username", "anonymous"),
        correlation_id=correlation_id,
        success=True,
        details={"filename": file.filename, "records_processed": result.get("total_records", 0)}
    )
    
    return V2Response(data=result, timestamp=datetime.now().isoformat(), correlation_id=correlation_id)


@router.get("/plant/{plant_id}", response_model=V2Response, summary="Get DAM scheduling records by plant ID")
async def get_dam_scheduling_by_plant_id(
    plant_id: str,
    start_date: Optional[str] = Query(None, description="Start date in YYYY-MM-DD format (defaults to today)"),
    end_date: Optional[str] = Query(None, description="End date in YYYY-MM-DD format (defaults to tomorrow)"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Get DAM scheduling records for a specific plant with date range filtering (defaults to today-tomorrow)."""
    correlation_id = user_context.get("correlation_id")
    
    result = await dam_scheduling_controller.get_dam_scheduling_by_plant_id(plant_id, start_date, end_date)
    
    audit_logger.log_user_action(
        service="dam_scheduling_service",
        user_id=user_context.get("user_id", "anonymous"),
        action="get_dam_scheduling_by_plant_id",
        message=f"User {user_context.get('user_id', 'anonymous')} retrieved DAM scheduling records for plant",
        username=user_context.get("username", "anonymous"),
        correlation_id=correlation_id,
        success=True,
        details={
            "plant_id": plant_id,
            "start_date": start_date,
            "end_date": end_date,
            "record_count": result.get("count", 0)
        }
    )
    
    return V2Response(data=result, timestamp=datetime.now().isoformat(), correlation_id=correlation_id)


@router.get("/csv-format", summary="Get CSV format template")
async def get_csv_format_template(
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Get CSV format template with headers only (no data)."""
    correlation_id = user_context.get("correlation_id")
    
    # Create CSV template with just headers
    csv_template = "schedule_date,time_block,plant_id,schedule_volume\n"
    
    audit_logger.log_user_action(
        service="dam_scheduling_service",
        user_id=user_context.get("user_id", "anonymous"),
        action="get_csv_format",
        message=f"User {user_context.get('user_id', 'anonymous')} downloaded CSV format template",
        username=user_context.get("username", "anonymous"),
        correlation_id=correlation_id,
        success=True,
        details={"template_type": "csv_format"}
    )
    
    return Response(
        content=csv_template,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=dam_scheduling_template.csv"}
    )


@router.post("/commit", response_model=V2Response, summary="Commit staged DAM scheduling data")
async def commit_staged_scheduling(
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Commit staged DAM scheduling data to main collection."""
    correlation_id = user_context.get("correlation_id")
    user_id = user_context.get("user_id")
    
    audit_logger.log_system_operation(
        service="dam_scheduling_service",
        operation="commit_staged_data",
        message=f"User {user_id} committing staged DAM scheduling data",
        correlation_id=correlation_id,
        success=True,
        details={"user_id": user_id}
    )
    
    result = await dam_scheduling_controller.commit_staged_scheduling(user_id)
    
    audit_logger.log_user_action(
        service="dam_scheduling_service",
        user_id=user_id,
        action="commit_staged_data",
        message=f"User {user_id} committed staged DAM scheduling data",
        username=user_context.get("username", "anonymous"),
        correlation_id=correlation_id,
        success=result.get("success", False),
        details={
            "committed": result.get("committed", 0),
            "inserted": result.get("inserted", 0),
            "updated": result.get("updated", 0)
        }
    )
    
    return V2Response(data=result, timestamp=datetime.now().isoformat(), correlation_id=correlation_id)


@router.get("/staged/preview", response_model=V2Response, summary="Get preview of staged data (limited)")
async def get_staged_preview(
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Get preview of staged DAM scheduling data (limited to 50 records)."""
    correlation_id = user_context.get("correlation_id")
    user_id = user_context.get("user_id")
    
    result = await dam_scheduling_controller.get_staged_preview(user_id)
    
    audit_logger.log_user_action(
        service="dam_scheduling_service",
        user_id=user_id,
        action="get_staged_preview",
        message=f"User {user_id} retrieved staged data preview",
        username=user_context.get("username", "anonymous"),
        correlation_id=correlation_id,
        success=True,
        details={"staged_count": result.get("total_staged", 0)}
    )
    
    return V2Response(data=result, timestamp=datetime.now().isoformat(), correlation_id=correlation_id)


@router.get("/staged/data", response_model=V2Response, summary="Get paginated staged data for full review")
async def get_staged_data_paginated(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=1000, description="Number of records per page (max 1000)"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Get paginated staged DAM scheduling data for full review before committing."""
    correlation_id = user_context.get("correlation_id")
    user_id = user_context.get("user_id")
    
    result = await dam_scheduling_controller.get_staged_data_paginated(user_id, page, page_size)
    
    audit_logger.log_user_action(
        service="dam_scheduling_service",
        user_id=user_id,
        action="get_staged_data_paginated",
        message=f"User {user_id} retrieved paginated staged data",
        username=user_context.get("username", "anonymous"),
        correlation_id=correlation_id,
        success=True,
        details={
            "page": page,
            "page_size": page_size,
            "total_records": result.get("pagination", {}).get("total_records", 0),
            "records_returned": result.get("pagination", {}).get("records_on_page", 0)
        }
    )
    
    return V2Response(data=result, timestamp=datetime.now().isoformat(), correlation_id=correlation_id)


@router.delete("/staged/cancel", response_model=V2Response, summary="Cancel/clear staged data")
async def cancel_staged_data(
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Cancel/clear staged DAM scheduling data."""
    correlation_id = user_context.get("correlation_id")
    user_id = user_context.get("user_id")
    
    result = await dam_scheduling_controller.cancel_staged_data(user_id)
    
    audit_logger.log_user_action(
        service="dam_scheduling_service",
        user_id=user_id,
        action="cancel_staged_data",
        message=f"User {user_id} canceled staged data",
        username=user_context.get("username", "anonymous"),
        correlation_id=correlation_id,
        success=result.get("success", False),
        details={"user_id": user_id}
    )
    
    return V2Response(data=result, timestamp=datetime.now().isoformat(), correlation_id=correlation_id)


@router.get("/debug/staging-status", response_model=V2Response, summary="Debug staging collection status")
async def debug_staging_status(
    user_context: Dict[str, Any] = Depends(get_user_context)
):
    """Debug endpoint to check staging collection status."""
    correlation_id = user_context.get("correlation_id")
    user_id = user_context.get("user_id")
    
    # Get staging collection info
    repo = dam_scheduling_controller.service.repository
    
    staging_col = repo.repository.db[repo.staging_collection_name]

    # Count all staged records for this user
    total_staged = await staging_col.count_documents({"staged_by": user_id})

    # Count staged records with status
    staged_with_status = await staging_col.count_documents({"staged_by": user_id, "status": "staged"})

    # Get sample records with different filters
    sample_records_all = await staging_col.find({"staged_by": user_id}).limit(3).to_list(3)

    sample_records_with_status = await staging_col.find({"staged_by": user_id, "status": "staged"}).limit(3).to_list(3)

    # Get any records for this user (to see field structure)
    any_user_records = await staging_col.find(
        {"staged_by": user_id},
        {"staged_by": 1, "status": 1, "plant_id": 1, "staged_at": 1, "_id": 1}
    ).limit(5).to_list(5)

    # Count all records in staging collection (any user)
    total_all_staged = await staging_col.count_documents({})
    
    return V2Response(
        data={
            "user_id": user_id,
            "staging_collection": repo.staging_collection_name,
            "total_staged_for_user": total_staged,
            "staged_with_status": staged_with_status,
            "total_all_staged": total_all_staged,
            "sample_records_all": [_serialize_value(record) for record in sample_records_all] if sample_records_all else [],
            "sample_records_with_status": [_serialize_value(record) for record in sample_records_with_status] if sample_records_with_status else [],
            "field_structure": [_serialize_value(record) for record in any_user_records] if any_user_records else []
        },
        timestamp=datetime.now().isoformat(),
        correlation_id=correlation_id
    )


@router.get("/debug/validate-plant/{plant_id}", response_model=V2Response, summary="Debug plant validation")
async def debug_validate_plant(
    plant_id: str,
    user_context: Dict[str, Any] = Depends(get_user_context)
):
    """Debug endpoint to test plant validation."""
    correlation_id = user_context.get("correlation_id")
    
    # Test plant validation
    exists = await dam_scheduling_controller.service.repository.validate_plant_exists(plant_id)
    
    return V2Response(
        data={
            "plant_id": plant_id,
            "exists": exists,
            "collection": dam_scheduling_controller.service.repository.plant_master_collection
        },
        timestamp=datetime.now().isoformat(),
        correlation_id=correlation_id
    )


# ----------- Schema models for dam-scheduling endpoint (Swagger-friendly) -----------

class PlantDataOrder(BaseModel):
    px_price: float = Field(..., description="PX price for the plant")
    plant_id: str = Field(..., description="Plant ID")
    category: str = Field(..., description="Plant category")
    plant_name: str = Field(..., description="Plant name")
    volume: float = Field(..., description="Scheduled volume (MW)")

class PlantData(BaseModel):
    forecast: float = Field(..., description="Forecast value")
    total_supply: float = Field(..., description="Total supply")
    surplus: float = Field(..., description="Surplus (total_supply - forecast)")
    deficit: float = Field(..., description="Deficit")
    bid_volume: float = Field(..., description="Bid volume (total_supply - forecast)")
    remaining_volume: float = Field(..., description="Remaining volume (same as bid_volume)")
    updated_volume: float = Field(..., description="Updated volume")
    dam_forecasted_price: float = Field(..., description="DAM forecasted price from limit_values collection")
    updated_total_supply: float = Field(..., description="Updated total supply (cumulative volume of all plants)")
    demand_forecast: float = Field(..., description="Demand forecast")
    plant_data_order: List[PlantDataOrder] = Field(..., description="Plant data ordered by px_price (descending)")

class TimeBlockData(BaseModel):
    time_block: int = Field(..., ge=1, le=96, description="15-min block number (1..96)")
    plant_data: PlantData = Field(..., description="Plant data for this time block")

class DamScheduleResponse(BaseModel):
    timestamp: str = Field(..., description="Response timestamp")
    data: List[TimeBlockData] = Field(..., description="Array of time block data")

    class Config:
        json_schema_extra = {
            "example": {
                "timestamp": "2025-01-27T10:30:00.000Z",
                "data": [
                    {
                        "time_block": 1,
                        "plant_data": {
                            "forecast": 1000.0,
                            "total_supply": 1200.0,
                            "surplus": 200.0,
                            "deficit": 0.0,
                            "bid_volume": 200.0,
                            "remaining_volume": 200.0,
                            "updated_volume": 1200.0,
                            "dam_forecasted_price": 3.5,
                            "updated_total_supply": 1200.0,
                            "demand_forecast": 1000.0,
                            "plant_data_order": [
                                {
                                    "px_price": 4.5,
                                    "plant_id": "PLANT001",
                                    "category": "Thermal",
                                    "plant_name": "Plant 1",
                                    "volume": 500.0
                                },
                                {
                                    "px_price": 3.8,
                                    "plant_id": "PLANT002",
                                    "category": "Solar",
                                    "plant_name": "Plant 2",
                                    "volume": 300.0
                                }
                            ]
                        }
                    }
                ]
            }
        }

class ErrorResponse(BaseModel):
    detail: str = Field(..., description="Error message")

    class Config:
        json_schema_extra = {"example": {"detail": "Failed to fetch fresh DAM schedule."}}


# ----------------------------- dam-scheduling endpoint -----------------------------

@router.get(
    "/dam-scheduling",
    response_model=DamScheduleResponse,
    responses={
        200: {"description": "OK", "model": DamScheduleResponse},
        422: {"description": "Validation Error", "model": ErrorResponse},
        500: {"description": "Server Error", "model": ErrorResponse},
    },
    status_code=status.HTTP_200_OK,
    summary="DAM schedule for a date",
    description=(
        "Returns DAM scheduling data with the following structure:\n\n"
        "```json\n"
        "{\n"
        "  \"data\": [\n"
        "    {\n"
        "      \"time_block\": 1,\n"
        "      \"plant_data\": {\n"
        "        \"forecast\": <float>,\n"
        "        \"total_supply\": <float>,\n"
        "        \"surplus\": <float>,\n"
        "        \"deficit\": <float>,\n"
        "        \"bid_volume\": <float>,\n"
        "        \"remaining_volume\": <float>,\n"
        "        \"updated_volume\": <float>,\n"
        "        \"dam_forecasted_price\": <float>,\n"
        "        \"updated_total_supply\": <float>,\n"
        "        \"demand_forecast\": <float>,\n"
        "        \"plant_data_order\": [\n"
        "          {\n"
        "            \"px_price\": <float>,\n"
        "            \"plant_id\": <string>,\n"
        "            \"category\": <string>,\n"
        "            \"plant_name\": <string>,\n"
        "            \"volume\": <float>\n"
        "          }\n"
        "        ]\n"
        "      }\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "```\n\n"
        "- **Time zone**: input `schedule_date` is interpreted in **IST (UTC+05:30)**.\n"
        "- **Default date**: If no date provided, defaults to **tomorrow's date**.\n"
        "- **Blocks**: The `data` array spans **1..96** blocks (15-minute intervals).\n"
        "- **Plant ordering**: Plants are ordered by `px_price` in descending order.\n"
        "- **Precision**: All numeric values are rounded to **2 decimal places**.\n"
        "- **Data sources**: \n"
        "  - Plant data from `serve_dam_scheduling` collection\n"
        "  - Plant details from `plant_master` collection\n"
        "  - Forecast data from `demand_serve` collection (type='DAF', scheduled_at field)\n"
        "  - DAM price from `limit_values` collection (latest date available)"
    ),
    operation_id="getDamScheduleByDateV2",
    response_model_exclude_none=False,
)
async def dam_schedule_by_date(
    request: Request,
    schedule_date: Optional[str] = Query(
        None,
        description="Schedule date in YYYY-MM-DD (IST). Defaults to tomorrow's date if omitted.",
        examples={
            "tomorrow": {"summary": "Use tomorrow's date (default in service)", "value": None},
            "custom": {"summary": "Specific date", "value": "2025-01-27"},
        },
        regex=r"^\d{4}-\d{2}-\d{2}$",
    ),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """
    Get DAM scheduling data for a given date.
    
    This endpoint provides comprehensive DAM scheduling information including:
    - Time block data (1-96 blocks)
    - Plant data with pricing and volume information
    - Forecast and supply calculations
    - Plant ordering by PX price (descending)
    """
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        logger.info(f"User {user_id} requested DAM schedule for date: {schedule_date}")
        
        # Log system operation start
        audit_logger.log_system_operation(
            service="dam_scheduling_service",
            operation="fetch_dam_schedule_by_date",
            message=f"User {user_id} requested DAM schedule for date: {schedule_date}",
            correlation_id=correlation_id,
            user_id=user_id,
            success=True
        )
        
        payload = await dam_scheduling_controller.schedule_by_date(schedule_date)
        
        # Log user action
        audit_logger.log_user_action(
            service="dam_scheduling_service",
            user_id=user_id,
            action="fetch_dam_schedule_by_date",
            message=f"User {user_id} fetched DAM schedule successfully",
            username=user_context.get("username", "anonymous"),
            correlation_id=correlation_id,
            success=True,
            details={"schedule_date": schedule_date}
        )
        
        logger.info(f"Successfully fetched DAM schedule for date: {schedule_date}")
        
        return DamScheduleResponse(
            timestamp=datetime.now().isoformat(),
            data=payload["data"],
        )
    except HTTPException as e:
        logger.warning(f"HTTP error fetching DAM schedule: {e.detail}")
        audit_logger.log_user_action(
            service="dam_scheduling_service",
            user_id=user_id,
            action="fetch_dam_schedule_by_date",
            message=f"Failed to fetch DAM schedule: {e.detail}",
            username=user_context.get("username", "anonymous"),
            correlation_id=correlation_id,
            success=False,
            details={"schedule_date": schedule_date, "error": e.detail}
        )
        raise e
    except Exception:
        logger.exception("Error fetching DAM schedule by date")
        audit_logger.log_user_action(
            service="dam_scheduling_service",
            user_id=user_id,
            action="fetch_dam_schedule_by_date",
            message="Failed to fetch DAM schedule due to internal error",
            username=user_context.get("username", "anonymous"),
            correlation_id=correlation_id,
            success=False,
            details={"schedule_date": schedule_date}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch DAM schedule.",
        )
