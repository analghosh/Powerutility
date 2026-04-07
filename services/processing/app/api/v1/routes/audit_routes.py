from fastapi import APIRouter, Query, Request, status
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, ConfigDict

from ....controller.audit_controller import audit_controller

router = APIRouter(
    prefix="/core/audit",
    tags=["Audit Logs"]
)

# ===========================
# Enums (Actions by Category)
# ===========================

class AuthAction(str, Enum):
    login = "Login"
    logout = "Logout"
    change_password = "Change Password"
    reset_password = "Reset Password"
    account_lock = "Account Lock"

class UserRoleAction(str, Enum):
    created = "Created"
    updated = "Updated"
    deleted = "Deleted"
    active_deactive = "Active/Deactive"
    role = "Role"
    permission = "Permission"

class DataAction(str, Enum):
    bid_submitted = "Bid Submitted"
    bid_approved = "Bid Approved"
    bid_rejected = "Bid Rejected"
    file_upload = "File Upload"
    file_download = "File Download"
    export = "Export"
    report_generated = "Report Generated"

# ===========================
# Models (rich + examples)
# ===========================

class ErrorResponse(BaseModel):
    code: str = Field(..., examples=["internal_error", "bad_request"])
    message: str = Field(..., examples=["Failed to list audit logs"])
    detail: Optional[Any] = Field(None, examples=[{"hint": "Check date format: YYYY-MM-DDTHH:MM:SSZ"}])

class AuditLogOut(BaseModel):
    """
    One audit record. `details` may include domain-specific keys like:
      - reason, method, old/new values, roles, statuses,
      - file_type, revision, record_count, etc.
    """
    id: str = Field(..., description="Mongo ObjectId as string", examples=["68891a087c3257b578cc73e5"])
    timestamp: datetime = Field(..., description="UTC timestamp", examples=["2025-07-29T18:59:20.948Z"])

    # taxonomy
    category: Optional[str] = Field(
        None,
        description="Authentication | User & Role Management | Data Accessibility",
        examples=["Authentication"]
    )
    action: Optional[str] = Field(None, description="Action within the category", examples=["Logout"])
    event_type: Optional[str] = Field(None, description="Optional technical event type", examples=["logout"])

    # principals
    user_id: Optional[str] = Field(None, examples=["688918b27eb90a77ea161a27"])
    username: Optional[str] = Field(None, examples=["admin"])
    name: Optional[str] = Field(None, description="Display name", examples=["System Administrator"])
    actor: Optional[str] = Field(None, description="Who performed it (username/id)", examples=["admin"])
    performed_by: Optional[str] = Field(None, description="system/admin/self", examples=["self"])
    target_user_id: Optional[str] = Field(None, description="Affected user (user mgmt)", examples=["64ffe0b27eb90a77ea161a99"])

    # context
    ip_address: Optional[str] = Field(None, examples=["103.110.19.151"])
    user_agent: Optional[str] = Field(
        None,
        examples=["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"]
    )
    success: Optional[bool] = Field(None, description="success/fail", examples=[True])
    error_message: Optional[str] = Field(None, examples=["Wrong password"])
    reason: Optional[str] = Field(None, description="Failure reason (top-level if present)", examples=["Account locked"])
    service: Optional[str] = Field(None, examples=["auth"])

    # resources
    resource_type: Optional[str] = Field(None, description="file/bid/report/data", examples=["bid"])
    resource_id: Optional[str] = Field(None, description="record/bid/file id", examples=["BID-2025-0001"])

    # flexible bag
    details: Dict[str, Any] = Field(
        default_factory=dict,
        examples=[{
            "method": "TOTP",
            "old_role": "viewer",
            "new_role": "editor",
            "file_type": "PII",
            "revision": "R3",
            "record_count": 120
        }]
    )

    model_config = ConfigDict(from_attributes=True)

class PageMeta(BaseModel):
    page: int = Field(..., ge=1, examples=[1])
    page_size: int = Field(..., ge=1, le=200, examples=[50])
    total: int = Field(..., examples=[345])
    has_next: bool = Field(..., examples=[True])
    has_prev: bool = Field(..., examples=[False])

class Envelope(BaseModel):
    """
    Standard envelope for responses.
    """
    data: Any

class PagedAuditResponse(BaseModel):
    records: List[AuditLogOut]
    meta: PageMeta

# ===========================
# Helpers
# ===========================

def _filters_from_query(**kwargs) -> Dict[str, Any]:
    return {k: v for k, v in kwargs.items() if v is not None and v != ""}

def _meta(total: int, page: int, page_size: int) -> PageMeta:
    return PageMeta(
        page=page,
        page_size=page_size,
        total=total,
        has_next=(page * page_size) < total,
        has_prev=page > 1
    )

# ===========================
# GENERIC LIST (all categories)
# ===========================

@router.get(
    "/logs",
    response_model=Envelope,
    status_code=status.HTTP_200_OK,
    summary="List audit logs (all categories, latest first)",
    description="""
Paginated audit feed across **all categories** with rich filters.

**Tips**
- Default sorting is `-timestamp` (newest first).
- Use `search` to regex-match in user, ip, agent, action, category and `details`.
- Use category-specific endpoints for friendlier action enums:
  - `/core/audit/logs/authentication`
  - `/core/audit/logs/user-role`
  - `/core/audit/logs/data-accessibility`
""",
    responses={
        200: {
            "description": "Audit logs listed successfully",
            "model": Envelope,
            "content": {
                "application/json": {
                    "example": {
                        "data": {
                            "records": [
                                {
                                    "id": "68891a087c3257b578cc73e5",
                                    "timestamp": "2025-07-29T18:59:20.948Z",
                                    "category": "Authentication",
                                    "action": "Logout",
                                    "event_type": "logout",
                                    "user_id": "688918b27eb90a77ea161a27",
                                    "username": "admin",
                                    "ip_address": "103.110.19.151",
                                    "user_agent": "Mozilla/5.0 (...)",
                                    "success": True,
                                    "service": "auth",
                                    "details": {}
                                }
                            ],
                            "meta": {
                                "page": 1, "page_size": 50, "total": 1, "has_next": False, "has_prev": False
                            }
                        }
                    }
                }
            }
        },
        400: {"model": ErrorResponse, "description": "Bad request"},
        500: {"model": ErrorResponse, "description": "Server error"},
    }
)
async def list_audit_logs(
    request: Request,

    # taxonomy
    category: Optional[str] = Query(None, description="Authentication | User & Role Management | Data Accessibility", examples=["Authentication"]),
    action: Optional[str] = Query(None, description="Action string", examples=["Logout"]),

    # principals / actors
    user_id: Optional[str] = Query(None, examples=["688918b27eb90a77ea161a27"]),
    username: Optional[str] = Query(None, examples=["admin"]),
    name: Optional[str] = Query(None, examples=["System Administrator"]),
    actor: Optional[str] = Query(None, description="Who performed the action", examples=["admin"]),
    performed_by: Optional[str] = Query(None, description="system/admin/self", examples=["self"]),
    target_user_id: Optional[str] = Query(None, description="Affected user", examples=["64ffe0b27eb90a77ea161a99"]),

    # technical context
    ip_address: Optional[str] = Query(None, examples=["103.110.19.151"]),
    user_agent: Optional[str] = Query(None, examples=["Chrome/138"]),
    success: Optional[bool] = Query(None, examples=[True]),
    event_type: Optional[str] = Query(None, examples=["login"]),
    service: Optional[str] = Query(None, examples=["auth"]),
    resource_type: Optional[str] = Query(None, description="file/bid/report/data type", examples=["bid"]),
    resource_id: Optional[str] = Query(None, description="record/bid/file id", examples=["BID-2025-0001"]),

    # time
    start_date: Optional[datetime] = Query(None, description="Start datetime (UTC)", examples=["2025-07-01T00:00:00Z"]),
    end_date: Optional[datetime] = Query(None, description="End datetime (UTC)", examples=["2025-07-31T23:59:59Z"]),

    # mapped details.* filters
    reason: Optional[str] = Query(None, description="Failure/lock reason", examples=["Wrong password"]),
    method: Optional[str] = Query(None, description="TOTP/SMS/Email/self-service/admin-forced", examples=["TOTP"]),
    old_value: Optional[str] = Query(None, examples=["john.doe@old.com"]),
    new_value: Optional[str] = Query(None, examples=["john.doe@new.com"]),
    old_role: Optional[str] = Query(None, examples=["viewer"]),
    new_role: Optional[str] = Query(None, examples=["editor"]),
    old_status: Optional[str] = Query(None, examples=["Deactive"]),
    new_status: Optional[str] = Query(None, examples=["Active"]),
    file_type: Optional[str] = Query(None, description="PII/financial/etc.", examples=["PII"]),
    revision: Optional[str] = Query(None, description="Bid/File revision", examples=["R3"]),
    record_count: Optional[int] = Query(None, description="Export/mass op count", examples=[120]),

    # free text
    search: Optional[str] = Query(None, description="Regex search across key fields & details", examples=["admin|locked"]),

    # pagination + sorting
    page: int = Query(1, ge=1, description="1-based page number", examples=[1]),
    page_size: int = Query(50, ge=1, le=200, description="Items per page (max 200)", examples=[50]),
    sort: Optional[str] = Query("-timestamp", description="'-timestamp' (default) or 'timestamp'", examples=["-timestamp"]),
):
    filters = _filters_from_query(
        category=category,
        action=action,
        user_id=user_id,
        username=username,
        name=name,
        actor=actor,
        performed_by=performed_by,
        target_user_id=target_user_id,
        ip_address=ip_address,
        user_agent=user_agent,
        success=success,
        event_type=event_type,
        service=service,
        resource_type=resource_type,
        resource_id=resource_id,
        start_date=start_date,
        end_date=end_date,
        reason=reason,
        method=method,
        old_value=old_value,
        new_value=new_value,
        old_role=old_role,
        new_role=new_role,
        old_status=old_status,
        new_status=new_status,
        file_type=file_type,
        revision=revision,
        record_count=record_count,
        search=search,
    )
    rows, total = await audit_controller.list_logs(filters, page, page_size, sort)
    return Envelope(data=PagedAuditResponse(records=rows, meta=_meta(total, page, page_size)).model_dump())

# ==========================================================
# CATEGORY 1: Authentication
# ==========================================================

@router.get(
    "/logs/authentication",
    response_model=Envelope,
    status_code=status.HTTP_200_OK,
    summary="Authentication audit (Login/Logout/Password/Lock)",
    description="""
Filter **Authentication** events only:
- Actions: Login, Logout, Change Password, Reset Password, Account Lock
- Common filters: `success`, `reason`, `method`, `ip_address`, `user_agent`
""",
    responses={
        200: {"model": Envelope, "description": "Authentication audit logs"},
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    }
)
async def list_authentication_logs(
    request: Request,
    action: Optional[AuthAction] = Query(None, description="Authentication action", examples=["Login"]),

    # principals
    user_id: Optional[str] = Query(None, examples=["688918b27eb90a77ea161a27"]),
    username: Optional[str] = Query(None, examples=["admin"]),
    name: Optional[str] = Query(None, examples=["System Administrator"]),
    actor: Optional[str] = Query(None, description="Who performed it (system/admin/self)", examples=["self"]),
    performed_by: Optional[str] = Query(None, description="system/admin/self", examples=["self"]),

    # context
    ip_address: Optional[str] = Query(None, examples=["103.110.19.151"]),
    user_agent: Optional[str] = Query(None, examples=["Chrome/138"]),
    success: Optional[bool] = Query(None, examples=[True]),
    reason: Optional[str] = Query(None, description="Failure reason", examples=["Wrong password"]),
    method: Optional[str] = Query(None, description="TOTP/SMS/Email/self-service/admin-forced", examples=["TOTP"]),

    # time/search
    start_date: Optional[datetime] = Query(None, examples=["2025-07-01T00:00:00Z"]),
    end_date: Optional[datetime] = Query(None, examples=["2025-07-31T23:59:59Z"]),
    search: Optional[str] = Query(None, examples=["admin|locked"]),

    # pagination/sorting
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    sort: Optional[str] = Query("-timestamp"),
):
    filters = _filters_from_query(
        category="Authentication",
        action=action.value if action else None,
        user_id=user_id,
        username=username,
        name=name,
        actor=actor,
        performed_by=performed_by,
        ip_address=ip_address,
        user_agent=user_agent,
        success=success,
        reason=reason,
        method=method,
        start_date=start_date,
        end_date=end_date,
        search=search
    )
    rows, total = await audit_controller.list_logs(filters, page, page_size, sort)
    return Envelope(data=PagedAuditResponse(records=rows, meta=_meta(total, page, page_size)).model_dump())

# ===================================================================
# CATEGORY 2: User & Role Management
# ===================================================================

@router.get(
    "/logs/user-role",
    response_model=Envelope,
    status_code=status.HTTP_200_OK,
    summary="User & Role Management audit (Create/Update/Delete/Status/Role/Permission)",
    description="""
Filter **User & Role Management** changes:
- Actions: Created, Updated, Deleted, Active/Deactive, Role, Permission
- Use `old_role/new_role`, `old_status/new_status`, `old_value/new_value`, `target_user_id`
""",
    responses={200: {"model": Envelope}, 400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}}
)
async def list_user_role_logs(
    request: Request,
    action: Optional[UserRoleAction] = Query(None, description="User & Role action", examples=["Updated"]),

    # principals
    actor: Optional[str] = Query(None, description="Who did it (admin/username)", examples=["admin"]),
    target_user_id: Optional[str] = Query(None, description="Affected user", examples=["64ffe0b27eb90a77ea161a99"]),

    # deltas
    old_value: Optional[str] = Query(None, description="Generic previous value", examples=["john.doe@old.com"]),
    new_value: Optional[str] = Query(None, description="Generic new value", examples=["john.doe@new.com"]),
    old_role: Optional[str] = Query(None, examples=["viewer"]),
    new_role: Optional[str] = Query(None, examples=["editor"]),
    old_status: Optional[str] = Query(None, description="Previous status", examples=["Deactive"]),
    new_status: Optional[str] = Query(None, description="Current status", examples=["Active"]),

    # time/search
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    search: Optional[str] = Query(None, examples=["role|permission|status"]),

    # pagination/sorting
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    sort: Optional[str] = Query("-timestamp"),
):
    filters = _filters_from_query(
        category="User & Role Management",
        action=action.value if action else None,
        actor=actor,
        target_user_id=target_user_id,
        old_value=old_value,
        new_value=new_value,
        old_role=old_role,
        new_role=new_role,
        old_status=old_status,
        new_status=new_status,
        start_date=start_date,
        end_date=end_date,
        search=search
    )
    rows, total = await audit_controller.list_logs(filters, page, page_size, sort)
    return Envelope(data=PagedAuditResponse(records=rows, meta=_meta(total, page, page_size)).model_dump())

# ==========================================================
# CATEGORY 3: Data Accessibility
# ==========================================================

@router.get(
    "/logs/data-accessibility",
    response_model=Envelope,
    status_code=status.HTTP_200_OK,
    summary="Data Accessibility audit (Bids/Files/Exports/Reports)",
    description="""
Filter **Data Accessibility** events:
- Actions: Bid Submitted/Approved/Rejected, File Upload/Download, Export, Report Generated
- Use `resource_type`, `resource_id`, `file_type`, `revision`, `record_count`
""",
    responses={200: {"model": Envelope}, 400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}}
)
async def list_data_accessibility_logs(
    request: Request,
    action: Optional[DataAction] = Query(None, description="Data Accessibility action", examples=["File Download"]),
    actor: Optional[str] = Query(None, description="Who performed the operation", examples=["analyst1"]),
    resource_type: Optional[str] = Query(None, description="file/bid/report/data type", examples=["file"]),
    resource_id: Optional[str] = Query(None, description="record/bid/file id", examples=["FILE-8899"]),
    file_type: Optional[str] = Query(None, description="PII/financial/etc.", examples=["PII"]),
    revision: Optional[str] = Query(None, description="Bid/File revision", examples=["R3"]),
    record_count: Optional[int] = Query(None, description="Export/mass op count", examples=[120]),

    # time/search
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    search: Optional[str] = Query(None, examples=["download|export|bid"]),

    # pagination/sorting
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    sort: Optional[str] = Query("-timestamp"),
):
    filters = _filters_from_query(
        category="Data Accessibility",
        action=action.value if action else None,
        actor=actor,
        resource_type=resource_type,
        resource_id=resource_id,
        file_type=file_type,
        revision=revision,
        record_count=record_count,
        start_date=start_date,
        end_date=end_date,
        search=search
    )
    rows, total = await audit_controller.list_logs(filters, page, page_size, sort)
    return Envelope(data=PagedAuditResponse(records=rows, meta=_meta(total, page, page_size)).model_dump())

# ===========================
# USER-SPECIFIC LIST
# ===========================

@router.get(
    "/users/{user_id}/logs",
    response_model=Envelope,
    status_code=status.HTTP_200_OK,
    summary="List logs for a specific user (latest first)",
    description="Convenient view for a single user's audit trail."
)
async def user_audit_logs(
    user_id: str,
    page: int = Query(1, ge=1, examples=[1]),
    page_size: int = Query(50, ge=1, le=200, examples=[50]),
):
    rows, total = await audit_controller.user_logs(user_id, page, page_size)
    return Envelope(data=PagedAuditResponse(records=rows, meta=_meta(total, page, page_size)).model_dump())

# ===========================
# STATS
# ===========================

@router.get(
    "/stats",
    response_model=Envelope,
    status_code=status.HTTP_200_OK,
    summary="Get audit log statistics",
    description="Totals, success/failed counts, success rate %, events by type for the specified window."
)
async def audit_stats(days: int = Query(30, ge=1, le=3650, description="Days to include in stats", examples=[30])):
    res = await audit_controller.stats(days)
    return Envelope(data=res)
