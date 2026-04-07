from __future__ import annotations
from datetime import datetime
from typing import Optional, List, Literal, Any, Dict
from pydantic import BaseModel, Field, ConfigDict

class LimitValues(BaseModel):
    model_config = ConfigDict(
        validate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={datetime: lambda v: v.isoformat()},
        json_schema_extra={
            "example": {
                "scheduled_at": "2025-09-25T00:00:00Z",
                "block": 1,
                "id_type": "n_1",
                "market": "RTM",
                "category": "Limits",
                "area": "Puducherry",
                "values": 20.0,
                "unit": "MW",
                "is_active": True,
                "version": 3,
                "source_approval_id": "6710fabc123def4567890123",
                "created_at": "2025-09-24T07:20:00Z",
                "created_by": "u_123",
                "updated_at": "2025-09-24T07:50:00Z",
                "updated_by": "approver_9"
            }
        }
    )
    scheduled_at: datetime = Field(...)
    block: int = Field(...)
    id_type: str = Field(...)
    market: str = Field(default="RTM")
    category: str = Field(default="Limits")
    area: str = Field(default="")
    values: float = Field(...)
    unit: Optional[str] = Field(default="MW")
    is_active: bool = Field(default=True)
    version: int = Field(default=1)
    source_approval_id: Optional[str] = Field(None)
    created_at: Optional[datetime] = Field(None)
    created_by: Optional[str] = Field(None)
    updated_at: Optional[datetime] = Field(None)
    updated_by: Optional[str] = Field(None)

ApprovalStatus = Literal["PENDING", "APPROVED", "REJECTED", "CANCELLED", "EXPIRED"]
RequestType = Literal["FILE_UPLOAD", "EDIT"]
ItemAction = Literal["UPSERT", "DELETE"]

class ApprovalItem(BaseModel):
    model_config = ConfigDict(
        json_encoders={datetime: lambda v: v.isoformat()},
        json_schema_extra={
            "example": {
                "action": "UPSERT",
                "scheduled_at": "2025-09-25T00:00:00Z",
                "block": 1,
                "id_type": "n_1",
                "market": "RTM",
                "category": "Limits",
                "area": "Puducherry",
                "values": 20.0,
                "unit": "MW",
                "comment": "Initial load",
                "row_status": "OK",
                "row_errors": []
            }
        }
    )
    action: ItemAction = Field(default="UPSERT")
    scheduled_at: datetime = Field(...)
    block: int = Field(...)
    id_type: str = Field(...)
    market: str = Field(default="RTM")
    category: str = Field(default="Limits")
    area: str = Field(default="")
    values: Optional[float] = Field(None)
    unit: Optional[str] = Field(default="MW")
    comment: Optional[str] = Field(None)
    row_status: Literal["OK", "ERROR"] = Field(default="OK")
    row_errors: List[str] = Field(default_factory=list)

class StagedFileMeta(BaseModel):
    filename: Optional[str] = Field(None)
    mime_type: Optional[str] = Field(None)
    size_bytes: Optional[int] = Field(None)
    storage_path: Optional[str] = Field(None)
    checksum_sha256: Optional[str] = Field(None)

class StagedCounters(BaseModel):
    total_items: int = Field(default=0)
    valid_items: int = Field(default=0)
    invalid_items: int = Field(default=0)
    applied_upserts: int = Field(default=0)
    applied_deletes: int = Field(default=0)

class LimitApproval(BaseModel):
    model_config = ConfigDict(
        validate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={datetime: lambda v: v.isoformat()},
        json_schema_extra={
            "example": {
                "status": "PENDING",
                "request_type": "FILE_UPLOAD",
                "title": "RTM Limits 2025-09-25",
                "description": "Upload by ops",
                "requested_by": "u_123",
                "requested_username": "alice",
                "requested_at": "2025-09-24T07:20:00Z",
                "last_edited_at": "2025-09-24T07:45:00Z",
                "last_edited_by": "u_123",
                "items": [
                    {
                        "action": "UPSERT",
                        "scheduled_at": "2025-09-25T00:00:00Z",
                        "block": 1,
                        "id_type": "n_1",
                        "market": "RTM",
                        "category": "Limits",
                        "area": "Puducherry",
                        "values": 20.0,
                        "unit": "MW",
                        "row_status": "OK",
                        "row_errors": []
                    }
                ],
                "file": {
                    "filename": "limits_2025-09-25.csv",
                    "mime_type": "text/csv",
                    "size_bytes": 4821,
                    "storage_path": "s3://bucket/limits/6710f.csv",
                    "checksum_sha256": "ab12...ef"
                },
                "counts": {
                    "total_items": 96,
                    "valid_items": 96,
                    "invalid_items": 0,
                    "applied_upserts": 0,
                    "applied_deletes": 0
                },
                "correlation_id": "corr-abc-123",
                "created_at": "2025-09-24T07:20:00Z",
                "created_by": "u_123"
            }
        }
    )
    status: ApprovalStatus = Field(...)
    request_type: RequestType = Field(...)
    title: Optional[str] = Field(None)
    description: Optional[str] = Field(None)
    requested_by: str = Field(...)
    requested_username: Optional[str] = Field(None)
    requested_at: datetime = Field(default_factory=datetime.utcnow)
    last_edited_at: Optional[datetime] = Field(None)
    last_edited_by: Optional[str] = Field(None)
    approved_by: Optional[str] = Field(None)
    approved_username: Optional[str] = Field(None)
    approved_at: Optional[datetime] = Field(None)
    rejected_by: Optional[str] = Field(None)
    rejected_username: Optional[str] = Field(None)
    rejected_at: Optional[datetime] = Field(None)
    decision_reason: Optional[str] = Field(None)
    items: List[ApprovalItem] = Field(default_factory=list)
    file: Optional[StagedFileMeta] = Field(None)
    counts: Optional[StagedCounters] = Field(default_factory=StagedCounters)
    correlation_id: Optional[str] = Field(None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: str = Field(...)

AuditAction = Literal[
    "UPLOAD_STAGED",
    "STAGE_EDIT",
    "VIEW_PENDING",
    "APPROVE",
    "REJECT",
    "APPLY_UPSERT",
    "APPLY_DELETE",
    "CANCEL_REQUEST"
]
AuditOperation = Literal["INSERT", "UPDATE", "DELETE", "READ"]
AuditEntityType = Literal["limit_approval", "limit_values"]

class AuditKey(BaseModel):
    scheduled_at: Optional[datetime] = Field(None)
    block: Optional[int] = Field(None)
    id_type: Optional[str] = Field(None)
    market: Optional[str] = Field(None)
    category: Optional[str] = Field(None)
    area: Optional[str] = Field(None)

class LimitAuditLog(BaseModel):
    model_config = ConfigDict(
        validate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={datetime: lambda v: v.isoformat()},
        json_schema_extra={
            "example": {
                "ts": "2025-09-24T07:50:20Z",
                "actor_id": "approver_9",
                "actor_username": "bob",
                "action": "APPLY_UPSERT",
                "entity_type": "limit_values",
                "entity_id": "64ab0c...f",
                "operation": "UPDATE",
                "request_id": "6710fabc123def4567890123",
                "correlation_id": "corr-abc-123",
                "success": True,
                "key": {
                    "scheduled_at": "2025-09-25T00:00:00Z",
                    "block": 1,
                    "id_type": "n_1",
                    "market": "RTM",
                    "category": "Limits",
                    "area": "Puducherry"
                },
                "before": {"values": 18.0, "unit": "MW", "version": 2},
                "after": {"values": 20.0, "unit": "MW", "version": 3}
            }
        }
    )
    ts: datetime = Field(default_factory=datetime.utcnow)
    actor_id: str = Field(...)
    actor_username: Optional[str] = Field(None)
    action: AuditAction = Field(...)
    entity_type: AuditEntityType = Field(...)
    entity_id: Optional[str] = Field(None)
    operation: AuditOperation = Field(...)
    success: bool = Field(default=True)
    error_message: Optional[str] = Field(None)
    request_id: Optional[str] = Field(None)
    correlation_id: Optional[str] = Field(None)
    ip: Optional[str] = Field(None)
    user_agent: Optional[str] = Field(None)
    key: Optional[AuditKey] = Field(None)
    before: Optional[Dict[str, Any]] = Field(None)
    after: Optional[Dict[str, Any]] = Field(None)

