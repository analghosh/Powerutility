# from fastapi import APIRouter, HTTPException, status, Query, Body
# from pydantic import BaseModel, Field
# from typing import Any, Dict, Optional
# from datetime import datetime
# import logging

# from ....controller.master_plants_controller import ServeMasterPlantsController

# router = APIRouter(
#     prefix="/processing/master_plants",
#     tags=["Master Plants"],
# )

# logger = logging.getLogger(__name__)
# controller = ServeMasterPlantsController()


# # ---------- Schemas ----------
# class PlantBase(BaseModel):
#     plant_id: Optional[str] = Field(None, description="Short ID like SSTPS")
#     plant_name: str
#     c_s: str
#     category: str
#     technology: str
#     price_group: Optional[str] = None
#     ramping: Optional[float] = None
#     TM: Optional[float] = None
#     FC_price: Optional[float] = None
#     VC_price: Optional[float] = None
#     PX_price: Optional[float] = None
#     multiplier: Optional[float] = None
#     dc_sg: Optional[str] = None
#     volume: Optional[float] = None
#     ppa: Optional[str] = None

# class PlantCreate(PlantBase):
#     plant_name: str = Field(...)

# class PlantUpdate(BaseModel):
#     # all fields optional
#     plant_id: Optional[str] = None
#     plant_name: Optional[str] = None
#     c_s: Optional[str] = None
#     category: Optional[str] = None
#     technology: Optional[str] = None
#     price_group: Optional[str] = None
#     ramping: Optional[float] = None
#     TM: Optional[float] = None
#     FC_price: Optional[float] = None
#     VC_price: Optional[float] = None
#     PX_price: Optional[float] = None
#     multiplier: Optional[float] = None
#     dc_sg: Optional[str] = None
#     volume: Optional[float] = None
#     ppa: Optional[str] = None

# class PlantOut(PlantBase):
#     id: Optional[str] = Field(None, alias="_id")


# class ListResponse(BaseModel):
#     timestamp: str
#     data: Dict[str, Any]


# # ---------- CRUD Endpoints ----------
# @router.post(
#     "/",
#     response_model=Dict[str, str],
#     status_code=status.HTTP_201_CREATED,
#     summary="Create a master plant"
# )
# def create_master_plant(payload: PlantCreate):
#     res = controller.create(payload.dict(by_alias=True, exclude_none=True))
#     return res


# @router.get(
#     "/{plant_oid}",
#     response_model=PlantOut,
#     summary="Get a master plant by Mongo _id"
# )
# def get_master_plant(plant_oid: str):
#     return controller.get(plant_oid)


# @router.get(
#     "/",
#     response_model=ListResponse,
#     summary="List master plants (filters + pagination)"
# )
# def list_master_plants(
#     page: int = Query(1, ge=1),
#     page_size: int = Query(50, ge=1, le=200),
#     plant_name: Optional[str] = Query(None),
#     c_s: Optional[str] = Query(None),
#     category: Optional[str] = Query(None),
#     technology: Optional[str] = Query(None),
# ):
#     filters: Dict[str, Any] = {}
#     for k, v in [("plant_name", plant_name), ("c_s", c_s), ("category", category), ("technology", technology)]:
#         if v is not None:
#             filters[k] = v

#     data = controller.list(filters, page, page_size)
#     return {"timestamp": datetime.now().isoformat(), "data": data}


# @router.patch(
#     "/{plant_oid}",
#     response_model=PlantOut,
#     summary="Update a master plant (partial)"
# )
# def update_master_plant(plant_oid: str, updates: PlantUpdate):
#     doc = controller.update(plant_oid, updates.dict(exclude_none=True))
#     return doc


# @router.delete(
#     "/{plant_oid}",
#     response_model=Dict[str, bool],
#     summary="Delete a master plant by Mongo _id"
# )
# def delete_master_plant(plant_oid: str):
#     return controller.delete(plant_oid)


# # # ---------- Utility: Apply plant_id to serve_supply_table ----------
# # @router.post(
# #     "/apply-plant-ids-to-supply",
# #     response_model=Dict[str, Any],
# #     summary="Copy plant_id to serve_supply_table where 4 keys match (no indexes required)"
# # )
# # def apply_plant_ids_to_supply():
# #     """
# #     Iterates master → updates supply with `plant_id` for all matching docs by (plant_name,c_s,category,technology).
# #     Does **not** require unique indexes or $merge.
# #     """
# #     stats = controller.apply_plant_ids_to_supply()
# #     return stats

from fastapi import APIRouter, status, Query, Request, UploadFile, File, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional
from datetime import datetime
import logging

from ....controller.master_plants_controller import serve_master_plants_controller

router = APIRouter(prefix="/processing/master_plants", tags=["Master Plants"])
logger = logging.getLogger(__name__)

# ---------- Schemas ----------
class PlantBase(BaseModel):
    """Base plant schema supporting both legacy and entity field formats"""
    plant_id: Optional[str] = Field(None)
    plant_name: str
    c_s: str
    category: str
    technology: str
    price_group: Optional[str] = None
    ramping: Optional[float] = None
    
    # Entity field names (primary)
    tm: Optional[float] = None
    fc_price: Optional[float] = None
    vc_price: Optional[float] = None
    px_price: Optional[float] = None
    
    multiplier: Optional[float] = None
    dc_sg: Optional[str] = None
    volume: Optional[float] = None
    ppa: Optional[str] = None
    
    class Config:
        alias_generator = lambda field_name: {
            'tm': 'TM',
            'fc_price': 'FC_price', 
            'vc_price': 'VC_price',
            'px_price': 'PX_price'
        }.get(field_name, field_name)
        validate_by_name = True
        extra = "allow"  # Allow extra fields for flexibility

class PlantCreate(PlantBase):
    plant_name: str = Field(...)

class PlantUpdate(BaseModel):
    """Update schema supporting both legacy and entity field formats"""
    plant_id: Optional[str] = None
    plant_name: Optional[str] = None
    c_s: Optional[str] = None
    category: Optional[str] = None
    technology: Optional[str] = None
    price_group: Optional[str] = None
    ramping: Optional[float] = None
    
    # Entity field names (primary)
    tm: Optional[float] = None
    fc_price: Optional[float] = None
    vc_price: Optional[float] = None
    px_price: Optional[float] = None
    
    multiplier: Optional[float] = None
    dc_sg: Optional[str] = None
    volume: Optional[float] = None
    ppa: Optional[str] = None
    
    class Config:
        alias_generator = lambda field_name: {
            'tm': 'TM',
            'fc_price': 'FC_price',
            'vc_price': 'VC_price', 
            'px_price': 'PX_price'
        }.get(field_name, field_name)
        validate_by_name = True
        extra = "allow"

class PlantResponse(BaseModel):
    """Response schema that returns data in legacy format"""
    id: Optional[str] = Field(None, alias="_id")
    plant_id: Optional[str] = None
    plant_name: str
    c_s: str
    category: str
    technology: str
    price_group: Optional[str] = None
    ramping: Optional[float] = None
    
    # Return in legacy format
    TM: Optional[float] = None
    FC_price: Optional[float] = None
    VC_price: Optional[float] = None
    PX_price: Optional[float] = None
    
    multiplier: Optional[float] = None
    dc_sg: Optional[str] = None
    volume: Optional[float] = None
    ppa: Optional[str] = None
    
    class Config:
        validate_by_name = True

class ListResponse(BaseModel):
    timestamp: str
    data: Dict[str, Any]

def _request_info(request: Request) -> Dict[str, Optional[str]]:
    return {
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
        "method": request.method,
        "path": request.url.path,
        "timestamp": datetime.now().isoformat(),
    }

# ---------- CRUD Endpoints with audit (no auth) ----------
@router.post(
    "/",
    response_model=Dict[str, str],
    status_code=status.HTTP_201_CREATED,
    summary="Create a master plant",
    description="Create a new master plant. Supports both legacy field names (TM, FC_price, VC_price, PX_price) and entity field names (tm, fc_price, vc_price, px_price)."
)
async def create_master_plant(
    request: Request,
    payload: PlantCreate,
):
    """
    Create a new master plant.
    
    The API accepts data in both formats:
    - Legacy format: {"TM": 20.9, "FC_price": 1.707, "VC_price": 5.205, "PX_price": 5.405}
    - Entity format: {"tm": 20.9, "fc_price": 1.707, "vc_price": 5.205, "px_price": 5.405}
    
    The service will automatically transform the data to the appropriate format.
    """
    return serve_master_plants_controller.create(
        payload.dict(exclude_none=True, by_alias=True),
        user=None,                                 # <- no auth
        request_info=_request_info(request),
    )

@router.get(
    "/{plant_oid}",
    response_model=PlantResponse,
    summary="Get a master plant by Mongo _id",
    description="Get a master plant by its MongoDB ObjectId. Returns data in legacy format (TM, FC_price, VC_price, PX_price)."
)
async def get_master_plant(
    plant_oid: str,
    request: Request,
):
    """
    Get a master plant by its MongoDB ObjectId.
    
    Returns data in legacy format with field names like TM, FC_price, VC_price, PX_price.
    """
    return serve_master_plants_controller.get(
        plant_oid,
        user=None,                                 # <- no auth
        request_info=_request_info(request),
    )

@router.get(
    "/",
    response_model=ListResponse,
    summary="List master plants (filters + pagination)",
    description="List master plants with optional filtering and pagination. Returns data in legacy format."
)
async def list_master_plants(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    plant_name: Optional[str] = Query(None),
    c_s: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    technology: Optional[str] = Query(None),
):
    """
    List master plants with optional filtering and pagination.
    
    Returns data in legacy format with field names like TM, FC_price, VC_price, PX_price.
    """
    filters: Dict[str, Any] = {}
    for k, v in [("plant_name", plant_name), ("c_s", c_s), ("category", category), ("technology", technology)]:
        if v is not None:
            filters[k] = v

    data = serve_master_plants_controller.list(
        filters, page, page_size,
        user=None,                                 # <- no auth
        request_info=_request_info(request),
    )
    return {"timestamp": datetime.now().isoformat(), "data": data}

@router.patch(
    "/{plant_oid}",
    response_model=PlantResponse,
    summary="Update a master plant (partial)",
    description="Update a master plant with partial data. Supports both legacy and entity field formats. Returns data in legacy format."
)
async def update_master_plant(
    plant_oid: str,
    updates: PlantUpdate,
    request: Request,
):
    """
    Update a master plant with partial data.
    
    The API accepts updates in both formats:
    - Legacy format: {"TM": 20.9, "FC_price": 1.707}
    - Entity format: {"tm": 20.9, "fc_price": 1.707}
    
    Returns the updated plant data in legacy format.
    """
    return serve_master_plants_controller.update(
        plant_oid,
        updates.dict(exclude_none=True, by_alias=True),
        user=None,                                 # <- no auth
        request_info=_request_info(request),
    )

@router.delete(
    "/{plant_oid}",
    response_model=Dict[str, bool],
    summary="Delete a master plant by Mongo _id"
)
async def delete_master_plant(
    plant_oid: str,
    request: Request,
):
    return serve_master_plants_controller.delete(
        plant_oid,
        user=None,                                 # <- no auth
        request_info=_request_info(request),
    )


@router.post("/upload", response_model=Dict[str, Any])
async def upload_plant_file(
    file: UploadFile = File(..., description="CSV or XLSX file containing plant data")
):
    """
    Upload and process plant data from CSV or XLSX file
    
    The file should contain the following columns:
    - Plant Name
    - State/UT
    - Owner
    - Sector
    - Plant Type
    - Capacity (MW)
    
    Supports both legacy field names (TM, FC_price, VC_price, PX_price) and entity field names (tm, fc_price, vc_price, px_price).
    """
    try:
        result = await serve_master_plants_controller.process_plant_file(file)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )
