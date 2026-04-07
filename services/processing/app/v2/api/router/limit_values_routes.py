from fastapi import APIRouter, Request, HTTPException, status, Depends, UploadFile, File
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field, validator
from datetime import datetime
import logging

from ...controller.limit_values_controller import limit_controller
from ...dependencies import get_user_context
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger

router = APIRouter(
    prefix="/limit-values",
    tags=["Limit Values V2"]
)

logger = logging.getLogger(__name__)


class LatestResponse(BaseModel):
    data: Dict[str, Any]

class StageEditItem(BaseModel):
    scheduled_at: str
    block: int
    id_type: str
    market: Optional[str] = "RTM"
    category: Optional[str] = "Limits"
    area: Optional[str] = ""
    values: Optional[float] = None
    action: Optional[str] = "UPSERT"
    comment: Optional[str] = None

    @validator("scheduled_at")
    def _validate_date(cls, v):
        datetime.strptime(v, "%Y-%m-%d")
        return v

class BatchEditItem(StageEditItem):
    approval_id: str

class BatchApproveBody(BaseModel):
    ids: List[str]

class BatchRejectBody(BaseModel):
    ids: List[str]
    reason: Optional[str] = None

@router.get(
    "/latest",
    status_code=status.HTTP_200_OK,
    summary="Get latest-date limit values with pagination and filters"
)
async def get_latest(
    request: Request,
    page: int = 1,
    page_size: Optional[int] = 96,
    schedule_date: Optional[str] = None,
    id_type: Optional[str] = None,
    market: Optional[str] = None,
    category: Optional[str] = None,
    area: Optional[str] = None,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    # Build filters from query parameters
    filters = {}
    if id_type is not None:
        filters["id"] = id_type
    if market is not None:
        filters["market"] = market
    if category is not None:
        filters["category"] = category
    if area is not None:
        filters["area"] = area
    if schedule_date is None:
        schedule_date = datetime.now().strftime("%Y-%m-%d")
    
    try:
        from_block = (page - 1) * page_size + 1
        to_block = from_block + page_size - 1
        
        audit_logger.log_system_operation(
            service="processing",
            operation="limit_values_latest_fetch",
            message=f"Fetch latest limit values with block-based pagination (page={page}, size={page_size}) and filters={filters}",
            correlation_id=correlation_id,
            user_id=user_id
        )
        
        result = await limit_controller.latest_paginated(
            from_block=from_block,
            to_block=to_block,
            filters=filters,
            date_filter=schedule_date
        )
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="limit_values_latest_view",
            message="Fetched latest with block-based pagination",
            username=username,
            correlation_id=correlation_id,
            record_count=len(result.get("data", [])),
            success=True
        )
        return result
        
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="LimitLatestError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        raise HTTPException(status_code=500, detail="Failed to fetch latest")

@router.get(
    "/sample-format",
    summary="Download sample CSV format for limit values upload"
)
async def get_sample_format(
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    from fastapi.responses import Response
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        csv_content = await limit_controller.get_sample_format()
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="download_sample",
            data_type="limit_values",
            message="Downloaded limit values sample CSV",
            username=user_context.get("username", "anonymous"),
            correlation_id=correlation_id,
            success=True
        )
        
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=limit_values_sample.csv"}
        )
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="LimitSampleDownloadError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        raise

@router.post(
    "/file-upload-stage",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Stage file upload (CSV/JSON) for approval"
)
async def stage_file_upload(
    request: Request,
    file: UploadFile = File(...),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    try:
        result = await limit_controller.stage_upload(file, user_context, audit_logger)
        return {"data": result}
    except Exception as e:
        # Audit logging is now handled in service layer for operations
        # But if controller/service raises exception before logging, we might want to log error here?
        # Service methods have try/except and log errors.
        # But if validation fails in controller before service call?
        # Controller calls service inside try/except.
        # So service should log errors.
        # However, let's keep error logging in route if we want to catch things service didn't catch 
        # OR if we want to be safe. But user asked to MOVE logging.
        # To be strict, remove it.
        # But ensure service logs errors. Service methods I edited log errors.
        # stage_file_upload in service logs errors.
        # So safe to remove.
        logger.error(f"[Limit] stage_file_upload error: {e}") 
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="LimitStageUploadError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        raise

@router.put(
    "/stage/{approval_id}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Edit staged items while PENDING or REJECTED"
)
async def stage_edit(
    request: Request,
    approval_id: str,
    body: List[StageEditItem],
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    try:
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="approval",
            message=f"Editing staged approval {approval_id}",
            username=username,
            correlation_id=correlation_id,
            record_count=len(body),
            success=True
        )
        result = await limit_controller.stage_edit(approval_id, [x.dict() for x in body], user_context)
        return {"data": result}
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="LimitStageEditError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        raise

@router.get(
    "/approvals",
    status_code=status.HTTP_200_OK,
    summary="List approval requests with pagination and filters (default PENDING)"
)
async def list_pending(
    request: Request,
    page: int = 1,
    page_size: int = 50,
    schedule_date: Optional[str] = None,  # Add date parameter (YYYY-MM-DD format)
    block: Optional[int] = None,
    id_type: Optional[str] = None,
    market: Optional[str] = None,
    category: Optional[str] = None,
    area: Optional[str] = None,
    status: Optional[str] = None,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    # Cap page_size to prevent MongoDB aggregation timeouts
    page_size = min(page_size, 1000)
    
    # Build filters from query parameters
    filters = {}
    if block is not None:
        filters["block"] = block
    if id_type is not None:
        filters["id"] = id_type  # Note: approval collection uses "id" field
    if market is not None:
        filters["market"] = market
    if category is not None:
        filters["category"] = category
    if area is not None:
        filters["area"] = area
    if status is not None:
        filters["status"] = status
    
    try:
        audit_logger.log_system_operation(
            service="processing",
            operation="limit_values_list_approvals",
            message=f"List approvals with pagination (page={page}, size={page_size}) and filters={filters}",
            correlation_id=correlation_id,
            user_id=user_id
        )
        result = await limit_controller.list_pending_paginated(
            user_context, 
            page=page, 
            page_size=page_size, 
            filters=filters,
            date_filter=schedule_date
        )
        return result  # Return the full paginated response structure
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="LimitListApprovalsError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        raise HTTPException(status_code=500, detail="Failed to list approvals")

class DecisionBody(BaseModel):
    reason: Optional[str] = None

@router.post(
    "/approve/{approval_id}",
    status_code=status.HTTP_200_OK,
    summary="Approve a staged request and apply to limit_values"
)
async def approve(
    request: Request,
    approval_id: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    try:
        result = await limit_controller.approve(approval_id, user_context, audit_logger)
        return {"data": result}
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="LimitApproveError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        raise

@router.post(
    "/reject/{approval_id}",
    status_code=status.HTTP_200_OK,
    summary="Reject a staged request"
)
async def reject(
    request: Request,
    approval_id: str,
    body: DecisionBody,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    try:
        result = await limit_controller.reject(approval_id, user_context, body.reason, audit_logger)
        return {"data": result}
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="LimitRejectError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        raise

@router.post(
    "/approve-all",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Update single limit value (goes through approval workflow)"
)
async def approve_all(
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    try:
        result = await limit_controller.approve_all(user_context, audit_logger)
        return {"data": result}
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="LimitApproveAllError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        raise  

@router.post(
    "/reject-all",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Reject all pending limit value requests"
)
async def reject_all(
    request: Request,
    body: DecisionBody,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):  
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    try:
        result = await limit_controller.reject_all(user_context, body.reason, audit_logger)
        return {"data": result}
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="LimitRejectAllError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        raise

class SingleUpdateItem(BaseModel):
    scheduled_at: str
    block: int
    id_type: str
    market: Optional[str] = "RTM"
    category: Optional[str] = "Limits"
    area: Optional[str] = ""
    values: Optional[float] = None
    action: Optional[str] = "UPSERT"
    comment: Optional[str] = None
    old_values: Optional[Dict[str, Any]] = None  # Current values for comparison

    @validator("scheduled_at")
    def _validate_date(cls, v):
        datetime.strptime(v, "%Y-%m-%d")
        return v

@router.post(
    "/single-update",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Update single limit value (goes through approval workflow)"
)
async def single_update(
    request: Request,
    body: SingleUpdateItem,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    try:
        result = await limit_controller.single_update(body.dict(), user_context, audit_logger)
        return {"data": result}
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="LimitSingleUpdateError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        raise

@router.post(
    "/multi-update",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Update multiple limit values (goes through approval workflow)"
)
async def multi_update(
    request: Request,
    body: List[SingleUpdateItem],
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    try:
        result = await limit_controller.multi_update([x.dict() for x in body], user_context, audit_logger)
        return {"data": result}
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="LimitMultiUpdateError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        raise

@router.post(
    "/approve-batch",
    status_code=status.HTTP_200_OK,
    summary="Approve multiple staged requests"
)
async def approve_batch(
    request: Request,
    body: BatchApproveBody,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    try:
        result = await limit_controller.approve_batch(body.ids, user_context, audit_logger)
        return {"data": result}
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="LimitApproveBatchError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        raise

@router.post(
    "/reject-batch",
    status_code=status.HTTP_200_OK,
    summary="Reject multiple staged requests"
)
async def reject_batch(
    request: Request,
    body: BatchRejectBody,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    try:
        result = await limit_controller.reject_batch(body.ids, user_context, body.reason, audit_logger)
        return {"data": result}
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="LimitRejectBatchError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        raise

@router.put(
    "/stage/edit-batch",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Edit multiple staged items"
)
async def edit_batch(
    request: Request,
    body: List[BatchEditItem],
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    try:
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="approval",
            message=f"Editing batch {len(body)} items",
            username=username,
            correlation_id=correlation_id,
            success=True
        )
        result = await limit_controller.edit_batch([x.dict() for x in body], user_context)
        return {"data": result}
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="LimitEditBatchError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        raise

@router.get(
    "/download-matrix",
    summary="Download limit values in matrix format (Date, Market, Block, ID1, ID2...) for TODAY only"
)
async def download_matrix(
    request: Request,
    market: Optional[str] = None,
    user_context: Dict[str, Any] = Depends(get_user_context),
    schedule_date: Optional[str] = None,
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    from fastapi.responses import Response
    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        csv_content = await limit_controller.download_matrix(market, schedule_date)
        
        # Determine filename with today's date
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="download_matrix",
            data_type="limit_values",
            message=f"Downloaded limit values matrix CSV for {today_str}",
            username=user_context.get("username", "anonymous"),
            correlation_id=correlation_id,
            success=True
        )
        
        filename = f"limit_values_matrix_{today_str}.csv"
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="LimitMatrixDownloadError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        raise

@router.get(
    "/download-ocf-matrix",
    summary="Download OCF limit values in matrix format (Date, Market, Block, OCF columns...) for TODAY only"
)
async def download_ocf_matrix(
    request: Request,
    market: Optional[str] = None,
    user_context: Dict[str, Any] = Depends(get_user_context),
    schedule_date: Optional[str] = None,
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    from fastapi.responses import Response

    user_id = user_context.get("user_id", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")

    try:
        csv_content = await limit_controller.download_ocf_matrix(market, schedule_date)

        # Determine filename with today's date
        today_str = datetime.now().strftime("%Y-%m-%d")

        audit_logger.log_user_action(
            service="processing",
            user_id=user_id,
            action="download_ocf_matrix",
            data_type="limit_values",
            message=f"Downloaded OCF limit values matrix CSV for {today_str}",
            username=user_context.get("username", "anonymous"),
            correlation_id=correlation_id,
            success=True
        )

        filename = f"ocf_limit_values_matrix_{today_str}.csv"

        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    except Exception as e:
        audit_logger.log_error_event(
            service="processing",
            error_name="OCFMatrixDownloadError",
            error_message=str(e),
            user_id=user_id,
            correlation_id=correlation_id,
            error_type=type(e).__name__
        )
        raise