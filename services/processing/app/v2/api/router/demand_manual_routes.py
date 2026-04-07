"""
V2 Demand Manual Routes
"""

from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Request, Depends, HTTPException, status, Body, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from datetime import datetime

from ...dependencies import get_user_context
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger
from ...controller.demand_manual_controller import demand_manual_controller
import logging

router = APIRouter(
    prefix="/demand-manual",
    tags=["Demand Manual V2"]
)

logger = logging.getLogger(__name__)

class ManualDemandItem(BaseModel):
    """
    Schema for manual demand data item.
    """
    scheduled_at: str = Field(..., description="Schedule date time (ISO format)", json_schema_extra={"example": "2025-01-18T00:00:00"})
    time_block: int = Field(..., description="Time block (1-96)", ge=1, le=96)
    drawal: float = Field(default=0.0, description="Drawal value")
    rostering: float = Field(default=0.0, description="Rostering value")
    frequency: float = Field(default=0.0, description="Frequency value")
    type: str = Field(default="IDF", description="Market type (IDF/DAF)")

class ManualTempDemandItem(BaseModel):
    """
    Schema for manual demand data item.
    """
    scheduled_at: str = Field(..., description="Schedule date (YYYY-MM-DD)")
    time_block: int = Field(..., description="Time block (1-96)", ge=1, le=96)
    drawal: float = Field(default=0.0, description="Drawal value")
    rostering: float = Field(default=0.0, description="Rostering value")
    frequency: float = Field(default=0.0, description="Frequency value")
    
class ManualDemandResponse(BaseModel):
    """
    Response schema for manual demand update.
    """
    success: bool
    processed_count: Optional[int] = 0
    errors: Optional[List[str]] = []
    message: Optional[str] = ""


@router.post(
    "/",
    response_model=ManualDemandResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Demand Data Manually",
    description="""
    Updates demand serve data with manual entries.
    Calculates actual, mpe, mape, and DSM metrics based on inputs.
    """
)
async def update_demand_manual(
    request: Request,
    items: List[ManualDemandItem] = Body(..., description="List of demand data items"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Log system operation
        audit_logger.log_system_operation(
            service="processing",
            operation="demand_manual_update",
            message=f"Received {len(items)} items for manual demand update",
            correlation_id=correlation_id,
            user_id=user_id
        )
        
        # Convert Pydantic models to list of dicts for controller
        data_list = [item.model_dump() for item in items]
        
        result = await demand_manual_controller.update_manual_demand(data_list)
        
        success = result.get("success", False)
        
        # Log user action
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="demand_manual_update",
            message=f"Manual demand update completed. Success: {success}",
            correlation_id=correlation_id,
            success=success
        )
        
        return ManualDemandResponse(
            success=success,
            processed_count=result.get("processed_count", 0),
            errors=result.get("errors", []),
            message="Demand manual update processed"
        )
        
    except Exception as e:
        logger.exception(f"Error in manual demand update for user {user_id}")
        
        audit_logger.log_error_event(
            service="processing",
            error_name="DemandManualUpdateError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process manual demand update: {str(e)}"
        )



@router.post(
    "/temp",
    response_model=ManualDemandResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Temp Demand Data Manually (V2 - New Collection)"
)
async def update_temp_demand_manual_v2(
    request: Request,
    items: List[ManualTempDemandItem],
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        audit_logger.log_system_operation(
            service="processing",
            operation="demand_manual_update_v2",
            message=f"Received {len(items)} items for manual demand update V2",
            correlation_id=correlation_id,
            user_id=user_id
        )

        data_list = [item.model_dump() for item in items]

        # 🔥 NEW COLLECTION NAME HERE
        result = await demand_manual_controller.update_temp_manual_demand(
            data_list               
        )

        return ManualDemandResponse(
            success=result.get("success", False),
            processed_count=result.get("processed_count", 0),
            errors=result.get("errors", []),
            message="Temp Demand manual update V2 processed"
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@router.post(
    "/temp/upload-csv",
    response_model=ManualDemandResponse,
    status_code=status.HTTP_200_OK,
    summary="Bulk Upload Temp Demand Data via CSV",
    description="""
    Accepts a CSV file and bulk-uploads temp demand data.

    **Expected CSV columns (header row required):**
    - `time_block` — integer (1–96)
    - `drawal` — float
    - `rostering` — float
    - `frequency` — float
    """
)
async def upload_temp_demand_csv(
    request: Request,
    file: UploadFile = File(..., description="CSV file with columns: time_block, drawal, rostering, frequency"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    if not file.filename.endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only CSV files are supported."
        )

    try:
        contents = await file.read()

        audit_logger.log_system_operation(
            service="processing",
            operation="demand_temp_csv_upload",
            message=f"Received CSV '{file.filename}' for temp demand bulk upload",
            correlation_id=correlation_id,
            user_id=user_id
        )

        result = await demand_manual_controller.upload_temp_demand_csv(contents, file.filename or "upload.csv")

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="demand_temp_csv_upload",
            message=f"CSV temp demand upload completed. Success: {result.get('success', False)}",
            correlation_id=correlation_id,
            success=result.get("success", False)
        )

        return ManualDemandResponse(
            success=result.get("success", False),
            processed_count=result.get("processed_count", 0),
            errors=result.get("errors", []),
            message=f"CSV upload processed {result.get('processed_count', 0)} rows"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error in CSV temp demand upload for user {user_id}")
        audit_logger.log_error_event(
            service="processing",
            error_name="DemandTempCSVUploadError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process CSV upload: {str(e)}"
        )


@router.get(
    "/temp/csv-format",
    summary="Export Temp Demand Data as CSV",
    description="""
    Exports today's records from `temp_demand_serve` as a CSV file.

    **Columns:**
    - `scheduled_at` — ISO datetime string
    - `time_block` — integer (1–96)
    - `drawal` — float
    - `rostering` — float
    - `frequency` — float
    """
)
async def download_temp_demand_csv_format(
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
):
    today_str = datetime.now().strftime("%Y%m%d")
    csv_content = await demand_manual_controller.export_temp_demand_csv()
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=temp_demand_{today_str}.csv"}
    )