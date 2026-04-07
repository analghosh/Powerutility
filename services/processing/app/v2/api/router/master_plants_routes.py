
from fastapi import APIRouter, status, Query, Request, UploadFile, File, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional, List
from datetime import datetime

from ...controller.master_plants_controller import master_plants_controller
from ...dependencies import get_user_context
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/master_plants", tags=["Master Plants V2"])

# Schemas from v1 can be reused or adapted here if necessary
# For simplicity, we'll use the schemas defined in the v1 routes file as a reference

class PlantBase(BaseModel):
    plant_id: Optional[str] = Field(None)
    plant_name: str
    c_s: str
    category: str
    technology: str
    price_group: Optional[str] = None
    ramping: Optional[float] = None
    tm: Optional[float] = Field(None, alias='TM')
    fc_price: Optional[float] = Field(None, alias='FC_price')
    vc_price: Optional[float] = Field(None, alias='VC_price')
    px_price: Optional[float] = Field(None, alias='PX_price')
    multiplier: Optional[float] = None
    dc_sg: Optional[str] = None
    volume: Optional[float] = None
    ppa: Optional[float] = None
    dispatch_type: Optional[str] = None
    approval_no: Optional[str] = None
    min_up_time: Optional[int] = None
    min_shut_down: Optional[int] = None
    max_shut_down: Optional[int] = None
    
    class Config:
        from_attributes = True
        populate_by_name = True

class PlantCreate(PlantBase):
    plant_name: str = Field(...)

class PlantUpdate(BaseModel):
    plant_id: Optional[str] = None
    plant_name: Optional[str] = None
    c_s: Optional[str] = None
    category: Optional[str] = None
    technology: Optional[str] = None
    price_group: Optional[str] = None
    ramping: Optional[float] = None
    tm: Optional[float] = Field(None, alias='TM')
    fc_price: Optional[float] = Field(None, alias='FC_price')
    vc_price: Optional[float] = Field(None, alias='VC_price')
    px_price: Optional[float] = Field(None, alias='PX_price')
    multiplier: Optional[float] = None
    dc_sg: Optional[str] = None
    volume: Optional[float] = None
    ppa: Optional[float] = None
    dispatch_type: Optional[str] = None
    approval_no: Optional[str] = None
    min_up_time: Optional[int] = None
    min_shut_down: Optional[int] = None
    max_shut_down: Optional[int] = None
    
class PlantResponse(PlantBase):
    id: str = Field(..., alias="_id")

class ListResponse(BaseModel):
    timestamp: str
    data: Dict[str, Any]

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
    "/",
    response_model=V2Response,
    status_code=status.HTTP_201_CREATED,
    summary="Create a master plant (V2)"
)
async def create_master_plant(
    request: Request,
    payload: PlantCreate,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    correlation_id = user_context.get("correlation_id")
    audit_logger.log_system_operation(
        service="master_plants_service",
        operation="create_plant",
        message=f"User {user_context.get('user_id', 'anonymous')} created a master plant",
        correlation_id=correlation_id,
        success=True,
        details={"plant_name": payload.plant_name}
    )
    result = await master_plants_controller.create_plant(
        payload.dict(exclude_none=True, by_alias=True),
        actor=user_context,
        request_info=_request_info(request)
    )
    audit_logger.log_user_action(
        service="master_plants_service",
        user_id=user_context.get("user_id", "anonymous"),
        action="create_plant",
        message=f"User {user_context.get('user_id', 'anonymous')} created a master plant successfully",
        username=user_context.get("username", "anonymous"),
        correlation_id=correlation_id,
        success=True,
        details={"plant_name": payload.plant_name}
    )
    return V2Response(data=result, timestamp=datetime.now().isoformat(), correlation_id=correlation_id)

@router.get(
    "/download_csv",
    response_model=None,
    summary="Download master plants as CSV (V2)"
)
async def download_master_plants_csv(
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    correlation_id = user_context.get("correlation_id")
    audit_logger.log_system_operation(
        service="master_plants_service",
        operation="download_csv",
        message=f"User {user_context.get('user_id', 'anonymous')} requested master plants CSV download",
        correlation_id=correlation_id,
        success=True,
        details={}
    )
    # Retrieve CSV buffer from controller (service handles CSV creation)
    csv_buffer = await master_plants_controller.export_csv(actor=user_context, request_info=_request_info(request))
    return StreamingResponse(csv_buffer, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=master_plants.csv"})

@router.get(
    "/{plant_oid}",
    response_model=V2Response,
    summary="Get a master plant by ID (V2)"
)
async def get_master_plant(
    plant_oid: str,
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    correlation_id = user_context.get("correlation_id")
    audit_logger.log_system_operation(
        service="master_plants_service",
        operation="get_plant",
        message=f"User {user_context.get('user_id', 'anonymous')} fetched a master plant",
        correlation_id=correlation_id,
        success=True,
        details={"plant_oid": plant_oid}
    )
    result = await master_plants_controller.get_plant(plant_oid, actor=user_context, request_info=_request_info(request))
    audit_logger.log_user_action(
        service="master_plants_service",
        user_id=user_context.get("user_id", "anonymous"),
        action="get_plant",
        message=f"User {user_context.get('user_id', 'anonymous')} fetched a master plant successfully",
        username=user_context.get("username", "anonymous"),
        correlation_id=correlation_id,
        success=True,
        details={"plant_oid": plant_oid}
    )
    return V2Response(data=result, timestamp=datetime.now().isoformat(), correlation_id=correlation_id)

@router.get(
    "/",
    response_model=V2Response,
    summary="List master plants (V2)"
)
async def list_master_plants(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    plant_name: Optional[str] = Query(None),
    c_s: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    technology: Optional[str] = Query(None),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    correlation_id = user_context.get("correlation_id")
    audit_logger.log_system_operation(
        service="master_plants_service",
        operation="list_plants",
        message=f"User {user_context.get('user_id', 'anonymous')} listed master plants",
        correlation_id=correlation_id,
        success=True,
        details={"filters": {"plant_name": plant_name, "c_s": c_s, "category": category, "technology": technology}}
    )
    filters = {k: v for k, v in {"plant_name": plant_name, "c_s": c_s, "category": category, "technology": technology}.items() if v is not None}
    result = await master_plants_controller.list_plants(filters, page, page_size, actor=user_context, request_info=_request_info(request))
    audit_logger.log_user_action(
        service="master_plants_service",
        user_id=user_context.get("user_id", "anonymous"),
        action="list_plants",
        message=f"User {user_context.get('user_id', 'anonymous')} listed master plants successfully",
        username=user_context.get("username", "anonymous"),
        correlation_id=correlation_id,
        success=True,
        details={"filters": filters}
    )
    return V2Response(data=result, timestamp=datetime.now().isoformat(), correlation_id=correlation_id)



@router.patch(
    "/{plant_oid}",
    response_model=V2Response,
    summary="Update a master plant (V2)"
)
async def update_master_plant(
    plant_oid: str,
    updates: PlantUpdate,
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    correlation_id = user_context.get("correlation_id")
    audit_logger.log_system_operation(
        service="master_plants_service",
        operation="update_plant",
        message=f"User {user_context.get('user_id', 'anonymous')} updated a master plant",
        correlation_id=correlation_id,
        success=True,
        details={"plant_oid": plant_oid, "updates": updates.dict(exclude_none=True, by_alias=True)}
    )
    result = await master_plants_controller.update_plant(plant_oid, updates.dict(exclude_none=True, by_alias=True), actor=user_context, request_info=_request_info(request))
    audit_logger.log_user_action(
        service="master_plants_service",
        user_id=user_context.get("user_id", "anonymous"),
        action="update_plant",
        message=f"User {user_context.get('user_id', 'anonymous')} updated a master plant successfully",
        username=user_context.get("username", "anonymous"),
        correlation_id=correlation_id,
        success=True,   
        details={"plant_oid": plant_oid, "updates": updates.dict(exclude_none=True, by_alias=True)}
    )
    return V2Response(data=result, timestamp=datetime.now().isoformat(), correlation_id=correlation_id)

@router.delete(
    "/{plant_oid}",
    response_model=V2Response,
    summary="Delete a master plant (V2)"
)
async def delete_master_plant(
    plant_oid: str,
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    correlation_id = user_context.get("correlation_id")
    audit_logger.log_system_operation(
        service="master_plants_service",
        operation="delete_plant",
        message=f"User {user_context.get('user_id', 'anonymous')} deleted a master plant",
        correlation_id=correlation_id,
        success=True,
        details={"plant_oid": plant_oid}
    )
    result = await master_plants_controller.delete_plant(plant_oid, actor=user_context, request_info=_request_info(request))
    audit_logger.log_user_action(
        service="master_plants_service",
        user_id=user_context.get("user_id", "anonymous"),
        action="delete_plant",
        message=f"User {user_context.get('user_id', 'anonymous')} deleted a master plant successfully",
        username=user_context.get("username", "anonymous"),
        correlation_id=correlation_id,
        success=True,
        details={"plant_oid": plant_oid}
    )
    return V2Response(data=result, timestamp=datetime.now().isoformat(), correlation_id=correlation_id)

@router.post("/upload", response_model=V2Response, summary="Upload plant file (V2)")
async def upload_plant_file(
    request: Request,
    file: UploadFile = File(...),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    correlation_id = user_context.get("correlation_id")
    audit_logger.log_system_operation(
        service="master_plants_service",
        operation="upload_plant_file",
        message=f"User {user_context.get('user_id', 'anonymous')} uploaded a plant file",
        correlation_id=correlation_id,
        success=True,
        details={"filename": file.filename}
    )
    result = await master_plants_controller.process_plant_file(file, actor=user_context, request_info=_request_info(request))
    audit_logger.log_user_action(
        service="master_plants_service",
        user_id=user_context.get("user_id", "anonymous"),
        action="upload_plant_file",
        message=f"User {user_context.get('user_id', 'anonymous')} uploaded a plant file successfully",
        username=user_context.get("username", "anonymous"),
        correlation_id=correlation_id,
        success=True,
        details={"filename": file.filename}
    )
    return V2Response(data=result, timestamp=datetime.now().isoformat(), correlation_id=correlation_id)
