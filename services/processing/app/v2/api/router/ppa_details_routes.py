from fastapi import APIRouter, status, Query, Request, UploadFile, File, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional
from datetime import datetime

from ...controller.ppa_details_controller import ppa_details_controller
from ...dependencies import get_user_context
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger

router = APIRouter(prefix="/ppa_details", tags=["PPA Details V2"])


class PpaDetailsUpdate(BaseModel):
    # plant_id, plant_name, category are synced from plant_master — not updatable here
    ppa_signing_date: Optional[str] = Field(None, description="ISO date or YYYY-MM-DD")
    agreement_term: Optional[float] = Field(None, description="Agreement term in years")
    expiry: Optional[str] = Field(None, description="Override expiry date; else computed from COD + term")
    installed_capacity_mw: Optional[float] = None
    no_of_units: Optional[int] = None
    mppmcl_firm_allocation_mw: Optional[float] = None
    unallocated_share_cs_mw: Optional[float] = None
    total_allocation_mw: Optional[float] = Field(
        None, description="Override total; else firm + unallocated"
    )
    commercial_operation_date: Optional[str] = Field(None, description="COD — ISO date or YYYY-MM-DD")
    remark: Optional[str] = None
    no_of_pages_in_agreement: Optional[int] = None
    status: Optional[str] = Field(None, description="Active or Inactive")
    postal_address: Optional[str] = None
    expired_but_extended: Optional[bool] = None


class V2Response(BaseModel):
    data: Any
    timestamp: str
    correlation_id: Optional[str]


def _request_info(request: Request) -> Dict[str, Optional[str]]:
    return {
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
        "method": request.method,
        "path": request.url.path,
        "timestamp": datetime.now().isoformat(),
    }


@router.post(
    "/sync",
    response_model=V2Response,
    summary="Sync PPA details rows from plant_master",
)
async def sync_ppa_from_plants(
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Ensure every plant in plant_master has a corresponding ppa_details row
    with the same plant_id, plant_name, and category. Safe to re-run (upsert).
    """
    correlation_id = user_context.get("correlation_id")
    audit_logger.log_system_operation(
        service="ppa_details_service",
        operation="sync_from_plants",
        message=f"User {user_context.get('user_id', 'anonymous')} synced PPA details from plants",
        correlation_id=correlation_id,
        success=True,
        details={},
    )
    result = await ppa_details_controller.sync_from_plants(
        actor=user_context, request_info=_request_info(request)
    )
    audit_logger.log_user_action(
        service="ppa_details_service",
        user_id=user_context.get("user_id", "anonymous"),
        action="sync_from_plants",
        message=f"User {user_context.get('user_id', 'anonymous')} synced PPA details successfully",
        username=user_context.get("username", "anonymous"),
        correlation_id=correlation_id,
        success=True,
        details=result,
    )
    return V2Response(data=result, timestamp=datetime.now().isoformat(), correlation_id=correlation_id)


@router.get(
    "/",
    response_model=V2Response,
    summary="List PPA details (grid)",
)
async def list_ppa_details(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None, description="Text search on plant name, plant ID, category"),
    status_filter: Optional[str] = Query(None, alias="status", description="Active or Inactive"),
    category: Optional[str] = Query(None),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    correlation_id = user_context.get("correlation_id")
    filters = {k: v for k, v in {"status": status_filter, "category": category}.items() if v is not None}
    audit_logger.log_system_operation(
        service="ppa_details_service",
        operation="list_ppa",
        message=f"User {user_context.get('user_id', 'anonymous')} listed PPA details",
        correlation_id=correlation_id,
        success=True,
        details={"filters": filters, "search": search},
    )
    result = await ppa_details_controller.list_ppa(
        filters, search, page, page_size, actor=user_context, request_info=_request_info(request)
    )
    audit_logger.log_user_action(
        service="ppa_details_service",
        user_id=user_context.get("user_id", "anonymous"),
        action="list_ppa",
        message=f"User {user_context.get('user_id', 'anonymous')} listed PPA details successfully",
        username=user_context.get("username", "anonymous"),
        correlation_id=correlation_id,
        success=True,
        details={"filters": filters, "search": search},
    )
    return V2Response(data=result, timestamp=datetime.now().isoformat(), correlation_id=correlation_id)


@router.get(
    "/{ppa_oid}",
    response_model=V2Response,
    summary="Get PPA details by ID",
)
async def get_ppa_details(
    ppa_oid: str,
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    correlation_id = user_context.get("correlation_id")
    audit_logger.log_system_operation(
        service="ppa_details_service",
        operation="get_ppa",
        message=f"User {user_context.get('user_id', 'anonymous')} fetched PPA details",
        correlation_id=correlation_id,
        success=True,
        details={"ppa_oid": ppa_oid},
    )
    result = await ppa_details_controller.get_ppa(
        ppa_oid, actor=user_context, request_info=_request_info(request)
    )
    return V2Response(data=result, timestamp=datetime.now().isoformat(), correlation_id=correlation_id)


@router.patch(
    "/{ppa_oid}",
    response_model=V2Response,
    summary="Update PPA details fields",
)
async def update_ppa_details(
    ppa_oid: str,
    updates: PpaDetailsUpdate,
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    correlation_id = user_context.get("correlation_id")
    payload = updates.dict(exclude_none=True)
    audit_logger.log_system_operation(
        service="ppa_details_service",
        operation="update_ppa",
        message=f"User {user_context.get('user_id', 'anonymous')} updated PPA details",
        correlation_id=correlation_id,
        success=True,
        details={"ppa_oid": ppa_oid, "updates": payload},
    )
    result = await ppa_details_controller.update_ppa(
        ppa_oid, payload, actor=user_context, request_info=_request_info(request)
    )
    audit_logger.log_user_action(
        service="ppa_details_service",
        user_id=user_context.get("user_id", "anonymous"),
        action="update_ppa",
        message=f"User {user_context.get('user_id', 'anonymous')} updated PPA details successfully",
        username=user_context.get("username", "anonymous"),
        correlation_id=correlation_id,
        success=True,
        details={"ppa_oid": ppa_oid},
    )
    return V2Response(data=result, timestamp=datetime.now().isoformat(), correlation_id=correlation_id)


@router.delete(
    "/{ppa_oid}",
    response_model=V2Response,
    summary="Delete PPA details row",
)
async def delete_ppa_details(
    ppa_oid: str,
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    correlation_id = user_context.get("correlation_id")
    result = await ppa_details_controller.delete_ppa(
        ppa_oid, actor=user_context, request_info=_request_info(request)
    )
    audit_logger.log_user_action(
        service="ppa_details_service",
        user_id=user_context.get("user_id", "anonymous"),
        action="delete_ppa",
        message=f"User {user_context.get('user_id', 'anonymous')} deleted PPA details",
        username=user_context.get("username", "anonymous"),
        correlation_id=correlation_id,
        success=True,
        details={"ppa_oid": ppa_oid},
    )
    return V2Response(data=result, timestamp=datetime.now().isoformat(), correlation_id=correlation_id)


@router.post(
    "/{ppa_oid}/upload",
    response_model=V2Response,
    summary="Upload PPA agreement file for a plant row",
)
async def upload_ppa_file(
    ppa_oid: str,
    request: Request,
    file: UploadFile = File(...),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    correlation_id = user_context.get("correlation_id")
    audit_logger.log_system_operation(
        service="ppa_details_service",
        operation="upload_ppa_file",
        message=f"User {user_context.get('user_id', 'anonymous')} uploading PPA file",
        correlation_id=correlation_id,
        success=True,
        details={"ppa_oid": ppa_oid, "filename": file.filename},
    )
    result = await ppa_details_controller.upload_file(
        ppa_oid, file, actor=user_context, request_info=_request_info(request)
    )
    audit_logger.log_user_action(
        service="ppa_details_service",
        user_id=user_context.get("user_id", "anonymous"),
        action="upload_ppa_file",
        message=f"User {user_context.get('user_id', 'anonymous')} uploaded PPA file successfully",
        username=user_context.get("username", "anonymous"),
        correlation_id=correlation_id,
        success=True,
        details={"ppa_oid": ppa_oid, "filename": file.filename},
    )
    return V2Response(data=result, timestamp=datetime.now().isoformat(), correlation_id=correlation_id)


@router.get(
    "/{ppa_oid}/files/{file_id}",
    response_model=None,
    summary="Download / open a PPA agreement file",
)
async def download_ppa_file(
    ppa_oid: str,
    file_id: str,
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Returns the uploaded PPA file so the browser / OS can open it
    (hyperlink target from the grid).
    """
    correlation_id = user_context.get("correlation_id")
    file_meta = await ppa_details_controller.get_file(ppa_oid, file_id)
    audit_logger.log_user_action(
        service="ppa_details_service",
        user_id=user_context.get("user_id", "anonymous"),
        action="download_ppa_file",
        message=f"User {user_context.get('user_id', 'anonymous')} downloaded PPA file",
        username=user_context.get("username", "anonymous"),
        correlation_id=correlation_id,
        success=True,
        details={"ppa_oid": ppa_oid, "file_id": file_id},
    )
    return FileResponse(
        path=str(file_meta["path"]),
        filename=file_meta["original_filename"],
        media_type=file_meta["content_type"],
        content_disposition_type="inline",
    )


@router.delete(
    "/{ppa_oid}/files/{file_id}",
    response_model=V2Response,
    summary="Delete an uploaded PPA agreement file",
)
async def delete_ppa_file(
    ppa_oid: str,
    file_id: str,
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    correlation_id = user_context.get("correlation_id")
    result = await ppa_details_controller.delete_file(
        ppa_oid, file_id, actor=user_context, request_info=_request_info(request)
    )
    audit_logger.log_user_action(
        service="ppa_details_service",
        user_id=user_context.get("user_id", "anonymous"),
        action="delete_ppa_file",
        message=f"User {user_context.get('user_id', 'anonymous')} deleted PPA file",
        username=user_context.get("username", "anonymous"),
        correlation_id=correlation_id,
        success=True,
        details={"ppa_oid": ppa_oid, "file_id": file_id},
    )
    return V2Response(data=result, timestamp=datetime.now().isoformat(), correlation_id=correlation_id)
