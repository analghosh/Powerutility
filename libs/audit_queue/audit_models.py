"""
Enhanced Audit Logging Models with TTL Integration
"""

from __future__ import annotations
from typing import Optional, Dict, List, Union, Literal, Any
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel, Field, field_validator, model_validator
import re
import json

from libs.audit_queue.models import LogPriority


# -------- Enums --------
LogType = Literal["app", "api", "job", "webhook", "audit", "security", "db", "system", "user"]
Priority = Literal["Critical", "High", "Medium", "Low"]
ErrorSeverity = Literal["fatal", "error", "warning", "info"]
UserImpact = Literal["none", "low", "medium", "high"]
ApprovalAction = Literal["approved", "rejected", "revoked"]
TradeSide = Literal["buy", "sell"]


# -------- Sub-Documents --------
class Meta(BaseModel):
    """Metadata for audit log entries"""
    service: str
    type: LogType
    priority: Priority
    correlationId: Optional[str] = None
    version: Optional[str] = None
    provider: Optional[str] = None
    region: Optional[str] = None
    jobName: Optional[str] = None
    
    # Enhanced metadata
    environment: Optional[str] = None  # "prod", "staging", "dev"
    component: Optional[str] = None    # "api", "worker", "scheduler"
    operation: Optional[str] = None    # "create", "update", "delete", "read"
    
    @field_validator("priority")
    @classmethod
    def _validate_priority(cls, v: str) -> str:
        """Ensure priority is properly formatted"""
        return v.title()  # Ensure proper case
    
    @field_validator("correlationId")
    @classmethod
    def _validate_correlation_id(cls, v: Optional[str]) -> Optional[str]:
        """Validate correlation ID format"""
        if v and not re.match(r'^[a-zA-Z0-9\-_]{1,64}$', v):
            raise ValueError(f"Invalid correlation ID format: {v}")
        return v


class Trace(BaseModel):
    """Distributed tracing context"""
    traceId: Optional[str] = None
    spanId: Optional[str] = None
    parentSpanId: Optional[str] = None
    requestId: Optional[str] = None
    workflowId: Optional[str] = None
    jobId: Optional[str] = None
    
    # Enhanced trace context
    sessionId: Optional[str] = None
    userId: Optional[str] = None
    tenantId: Optional[str] = None
    featureFlag: Optional[str] = None
    
    @field_validator("traceId", "spanId", "parentSpanId")
    @classmethod
    def _validate_trace_ids(cls, v: Optional[str]) -> Optional[str]:
        """Validate trace ID format"""
        if v and not re.match(r'^[a-f0-9]{16,32}$', v):
            raise ValueError(f"Invalid trace ID format: {v}")
        return v


class ErrorInfo(BaseModel):
    """Detailed error information"""
    name: str
    code: Optional[Union[str, int]] = None
    message: str
    stack: Optional[str] = None
    cause: Optional[str] = None
    
    # Enhanced error tracking
    severity: Optional[ErrorSeverity] = None
    category: Optional[str] = None  # "validation", "network", "database", "business"
    retryable: Optional[bool] = None
    user_impact: Optional[UserImpact] = None
    
    @field_validator("stack")
    @classmethod
    def _truncate_stack(cls, v: Optional[str]) -> Optional[str]:
        """Truncate stack traces to prevent oversized logs"""
        if v and len(v) > 2000:
            return v[:2000] + "... [truncated]"
        return v
    
    @field_validator("message")
    @classmethod
    def _truncate_message(cls, v: str) -> str:
        """Truncate error messages if too long"""
        if len(v) > 1000:
            return v[:1000] + "... [truncated]"
        return v


class Actor(BaseModel):
    """Actor information (user, system, service)"""
    id: str
    name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    
    # Enhanced actor context
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    organization: Optional[str] = None
    department: Optional[str] = None
    
    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: Optional[str]) -> Optional[str]:
        """Validate email format"""
        if v and not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', v):
            raise ValueError(f"Invalid email format: {v}")
        return v


class ApprovalSubject(BaseModel):
    """Subject of approval (what is being approved)"""
    entityType: str  # e.g., "Bid", "Trade", "Config", "User"
    entityId: str
    version: Optional[str] = None
    
    @field_validator("entityType")
    @classmethod
    def _validate_entity_type(cls, v: str) -> str:
        """Validate entity type format"""
        if not re.match(r'^[A-Z][a-zA-Z0-9]*$', v):
            raise ValueError(f"Entity type must be PascalCase: {v}")
        return v


class ApprovalSignature(BaseModel):
    """Digital signature for approvals"""
    id: Optional[str] = None
    algo: Optional[str] = None  # e.g., "RS256", "Ed25519"
    signedBy: Optional[str] = None
    signedAt: Optional[datetime] = None
    
    @field_validator("signedAt")
    @classmethod
    def _signed_at_utc(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Ensure timezone-aware UTC"""
        if v and not v.tzinfo:
            return v.replace(tzinfo=timezone.utc)
        return v


class Approval(BaseModel):
    """Approval workflow information"""
    flow: str                  # "BidApproval", "TradeApproval", etc.
    step: int
    stepName: Optional[str] = None
    action: ApprovalAction
    subject: ApprovalSubject
    actor: Actor               # the approver
    decisionAt: datetime
    reason: Optional[str] = None
    policy: Optional[Dict[str, Optional[str]]] = None  # {id?, version?}
    evidenceRef: Optional[str] = None
    evidenceSha256: Optional[str] = None
    signature: Optional[ApprovalSignature] = None
    
    @field_validator("decisionAt")
    @classmethod
    def _decision_at_utc(cls, v: datetime) -> datetime:
        """Ensure timezone-aware UTC"""
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    
    @field_validator("step")
    @classmethod
    def _validate_step(cls, v: int) -> int:
        """Validate step number"""
        if v < 1:
            raise ValueError("Step must be positive")
        return v


class Bid(BaseModel):
    """Bid context information"""
    market: str                # e.g., "DAM", "RTM"
    instrumentId: str
    side: TradeSide
    quantity: float
    price: float
    timeInForce: Optional[str] = None
    validityDate: Optional[str] = None
    
    @field_validator("quantity", "price")
    @classmethod
    def _validate_positive_values(cls, v: float) -> float:
        """Validate positive values"""
        if v <= 0:
            raise ValueError(f"Value must be positive: {v}")
        return v


class Trade(BaseModel):
    """Trade context information"""
    tradeId: str
    instrumentId: str
    side: TradeSide
    quantity: float
    price: float
    settlementDate: Optional[str] = None
    counterparty: Optional[str] = None
    venueId: Optional[str] = None
    
    @field_validator("quantity", "price")
    @classmethod
    def _validate_positive_values(cls, v: float) -> float:
        """Validate positive values"""
        if v <= 0:
            raise ValueError(f"Value must be positive: {v}")
        return v


# -------- Root Document --------
class AuditLogDoc(BaseModel):
    """Enhanced audit log document with TTL integration"""
    # Primary fields
    ts: datetime = Field(..., description="Event timestamp (UTC)")
    meta: Meta
    message: str
    
    # Status and performance
    status: Optional[str] = None            # e.g., "ok", "failed", "approved", "rejected"
    durationMs: Optional[int] = None        # latency if relevant
    
    # Context
    actor: Optional[Actor] = None           # who initiated the activity (not approver)
    trace: Optional[Trace] = None
    error: Optional[ErrorInfo] = None
    
    # Business context
    approval: Optional[Approval] = None     # present for approvals
    bid: Optional[Bid] = None               # lightweight context
    trade: Optional[Trade] = None
    
    # Additional data
    tags: Optional[List[str]] = None
    data: Optional[Dict[str, object]] = None  # keep small (< ~8 KB)
    
    # Evidence
    evidenceRef: Optional[str] = None
    evidenceSha256: Optional[str] = None
    
    # Schema versioning
    log_schema: Optional[Dict[str, int]] = None  # {version: 1}
    
    # TTL Management (auto-calculated)
    ttl_expiry_date: Optional[datetime] = None
    retention_policy: Optional[str] = None  # "standard", "extended", "permanent"
    
    @field_validator("ts")
    @classmethod
    def _ts_utc(cls, v: datetime) -> datetime:
        """Ensure timezone-aware UTC"""
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    
    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        """Limit and clean tags"""
        if v:
            # Remove duplicates, limit count, clean strings
            cleaned = list(set([tag.strip()[:50] for tag in v if tag.strip()]))
            return cleaned[:20]  # Max 20 tags
        return v
    
    @field_validator("durationMs")
    @classmethod
    def _validate_duration(cls, v: Optional[int]) -> Optional[int]:
        """Validate duration is reasonable"""
        if v is not None and (v < 0 or v > 86400000):  # Max 24 hours
            raise ValueError(f"Duration must be between 0 and 86400000 ms, got {v}")
        return v
    
    @model_validator(mode="after")
    def _calculate_ttl(self) -> "AuditLogDoc":
        """Calculate TTL expiry date based on priority"""
        if self.meta.priority and not self.ttl_expiry_date:
            priority_map = {
                "Critical": LogPriority.CRITICAL,
                "High": LogPriority.HIGH, 
                "Medium": LogPriority.MEDIUM,
                "Low": LogPriority.LOW
            }
            priority = priority_map.get(self.meta.priority)
            if priority and priority.ttl_days:
                self.ttl_expiry_date = self.ts + timedelta(days=priority.ttl_days)
                self.retention_policy = "standard"
            else:
                self.ttl_expiry_date = None  # Critical = no expiry
                self.retention_policy = "permanent"
        return self
    
    @model_validator(mode="after")
    def _validate_data_size(self) -> "AuditLogDoc":
        """Ensure data payload stays within reasonable limits"""
        if self.data:
            # Serialize to check size
            data_size = len(json.dumps(self.data, default=str))
            if data_size > 8192:  # 8KB limit
                raise ValueError(f"Data payload too large: {data_size} bytes (max 8KB)")
        
        if self.message and len(self.message) > 10000:  # 10KB limit
            self.message = self.message[:10000] + "... [truncated]"
            
        return self
    
    @model_validator(mode="after")
    def _approval_minimums(self) -> "AuditLogDoc":
        """Validate approval requirements"""
        if self.approval:
            if not self.approval.actor or not self.approval.actor.id:
                raise ValueError("approval.actor.id is required when approval is present")
            if not self.approval.decisionAt:
                raise ValueError("approval.decisionAt is required when approval is present")
        return self
    
    def _safe_timestamp_to_string(self, ts_value: Any) -> str:
        """Safely convert timestamp to string"""
        try:
            # Use getattr to safely access isoformat method
            if hasattr(ts_value, 'isoformat') and callable(getattr(ts_value, 'isoformat')):
                return getattr(ts_value, 'isoformat')()
            else:
                return str(ts_value)
        except Exception:
            current_time = datetime.now(timezone.utc)
            return current_time.isoformat()
    
    def to_audit_queue_format(self) -> Dict[str, Any]:
        """Convert to audit queue format for processing"""
        timestamp_str = self._safe_timestamp_to_string(self.ts)
        
        return {
            "timestamp": timestamp_str,
            "record_type": self.meta.type,  # Use the log type as record type
            "service": self.meta.service,
            "event_type": self.meta.type,
            "priority": self.meta.priority.lower(),
            "message": self.message,
            "status": self.status,
            "duration_ms": self.durationMs,
            "actor": self.actor.model_dump() if self.actor else None,
            "trace": self.trace.model_dump() if self.trace else None,
            "error": self.error.model_dump() if self.error else None,
            "approval": self.approval.model_dump() if self.approval else None,
            "bid": self.bid.model_dump() if self.bid else None,
            "trade": self.trade.model_dump() if self.trade else None,
            "tags": self.tags,
            "data": self.data,
            "evidence_ref": self.evidenceRef,
            "evidence_sha256": self.evidenceSha256,
            "correlation_id": self.meta.correlationId,
            "job_id": self.trace.jobId if self.trace else None,
            "user_id": self.actor.id if self.actor else None,
            "username": self.actor.name if self.actor else None,
            "ip_address": self.actor.ip_address if self.actor else None,
            "user_agent": self.actor.user_agent if self.actor else None,
            "ttl_expiry_date": self.ttl_expiry_date.isoformat() if self.ttl_expiry_date else None,
            "retention_policy": self.retention_policy,
            "created_at": self.ts,
            "schema_version": self.log_schema.get("version", 1) if self.log_schema else 1
        }


# -------- Convenience Functions --------
def create_audit_log(
    service: str,
    log_type: LogType,
    message: str,
    priority: Priority = "Medium",
    **kwargs
) -> AuditLogDoc:
    """Create a basic audit log entry"""
    meta = Meta(
        service=service,
        type=log_type,
        priority=priority,
        **kwargs.get("meta_kwargs", {})
    )
    
    return AuditLogDoc(
        ts=kwargs.get("timestamp", datetime.now(timezone.utc)),
        meta=meta,
        message=message,
        status=kwargs.get("status"),
        durationMs=kwargs.get("duration_ms"),
        actor=kwargs.get("actor"),
        trace=kwargs.get("trace"),
        error=kwargs.get("error"),
        approval=kwargs.get("approval"),
        bid=kwargs.get("bid"),
        trade=kwargs.get("trade"),
        tags=kwargs.get("tags"),
        data=kwargs.get("data"),
        evidenceRef=kwargs.get("evidence_ref"),
        evidenceSha256=kwargs.get("evidence_sha256"),
        log_schema={"version": 1}
    )


def create_error_log(
    service: str,
    error: ErrorInfo,
    message: str,
    priority: Priority = "High",
    **kwargs
) -> AuditLogDoc:
    """Create an error audit log entry"""
    return create_audit_log(
        service=service,
        log_type="app",
        message=message,
        priority=priority,
        error=error,
        status="failed",
        **kwargs
    )


def create_approval_log(
    service: str,
    approval: Approval,
    message: str,
    priority: Priority = "High",
    **kwargs
) -> AuditLogDoc:
    """Create an approval audit log entry"""
    return create_audit_log(
        service=service,
        log_type="audit",
        message=message,
        priority=priority,
        approval=approval,
        status=approval.action,
        **kwargs
    )
