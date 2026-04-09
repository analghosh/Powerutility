from typing import Any, Dict, Optional
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from bson import ObjectId

from libs.models.price_group_entity import PriceGroupCreate, PriceGroupUpdate
from ..repository.price_group_repository import PriceGroupRepository

# Import your queue-based audit logger
import sys, os
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
from libs.audit_queue.simple_logger import AuditLogger

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

class PriceGroupPayloadMapper:
    field_mapping = {
        "price_group": "price_group",
        "price_from": "price_from",
        "price_to": "price_to",
    }
    reverse_mapping = {v: k for k, v in field_mapping.items()}

    @classmethod
    def in_map(cls, d):
        return {cls.field_mapping.get(k, k): v for k, v in (d or {}).items()}

    @classmethod
    def out_map(cls, d):
        if not d:
            return d
        out = {cls.reverse_mapping.get(k, k): v for k, v in d.items()}

        # If repo ever returns _id, normalize to price_group_id
        if "_id" in out and "price_group_id" not in out:
            out["price_group_id"] = str(out.pop("_id"))

        return out



def _jsonify(v, *, target_tz: ZoneInfo = IST, assume_naive_is_utc: bool = True):
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, datetime):
        dt = v
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc if assume_naive_is_utc else target_tz)
        dt = dt.astimezone(target_tz)
        return dt.isoformat()  # e.g. 2025-09-29T18:45:00+05:30
    return v

def _safe_audit(audit_logger: AuditLogger, event: dict) -> None:
    if not event:
        return

    e = dict(event)
    uid = e.get("user_id") or "anonymous"
    e["user_id"] = str(uid)
    e["username"] = str(e.get("username") or "anonymous")

    payload = {k: _jsonify(v) for k, v in e.items()}

    # Keep timestamp if your logger accepts strings; otherwise pass datetime
    payload.setdefault("timestamp", datetime.now(timezone.utc).isoformat())

    try:
        ok = audit_logger.log_user_action(**payload)
        if ok is False:
            logger.error("Audit logger returned False | event=%s", payload)
    except Exception:
        logger.exception("Audit logger raised | event=%s", payload)



class PriceGroupService:
    def __init__(self, repo: PriceGroupRepository, audit_logger: AuditLogger):
        self.repo = repo
        self.mapper = PriceGroupPayloadMapper
        self.audit_logger = audit_logger

    def _actor(self, actor: Optional[Dict[str, Any]]):
        actor = actor or {}
        uid = actor.get("user_id")
        if uid in (None, ""):
            uid = "anonymous"              # pick a policy: "anonymous" / "system"
        return {"user_id": str(uid), "username": actor.get("username") or "anonymous"}

    def create(self, payload: Dict[str, Any], actor: Optional[Dict[str, Any]], req: Dict[str, Any]) -> str:
        from pymongo.errors import DuplicateKeyError
        
        data = self.mapper.in_map(payload)
        for k, v in list(data.items()):
            if isinstance(v, str):
                data[k] = v.strip()
        
        try:
            new_id = self.repo.create(PriceGroupCreate(**data).model_dump())
        except DuplicateKeyError as e:
            a = self._actor(actor)
            _safe_audit(self.audit_logger, {
                "service": "processing",
                "user_id": a["user_id"],
                "action": "price_group_create",
                "message": f"Failed to create price group - duplicate key: {data.get('price_group')}",
                "username": a["username"],
                "ip_address": req.get("ip_address"),
                "user_agent": req.get("user_agent"),
                "resource": "price_group",
                "payload": data,
                "success": False,
                "error": str(e),
            })
            logger.warning("PriceGroup creation failed - duplicate | price_group=%s error=%s", data.get("price_group"), str(e))
            raise ValueError(f"Price group '{data.get('price_group')}' already exists")

        a = self._actor(actor)
        _safe_audit(self.audit_logger, {
            "service": "processing",
            "user_id": a["user_id"],
            "action": "price_group_create",
            "message": "Created price group",
            "username": a["username"],
            "ip_address": req.get("ip_address"),
            "user_agent": req.get("user_agent"),
            "resource": "price_group",
            "resource_id": new_id,
            "payload": data,
            "success": True,
        })

        logger.info("PriceGroup created | _id=%s price_group=%s", new_id, data.get("price_group"))
        return new_id

    def get(self, _id: str, actor: Optional[Dict[str, Any]], req: Dict[str, Any]):
        doc = self.repo.get(_id)
        a = self._actor(actor)
        _safe_audit(self.audit_logger, {
            "service": "processing",
            "user_id": a["user_id"],
            "action": "price_group_get",
            "message": f"Get price group {_id}",
            "username": a["username"],
            "ip_address": req.get("ip_address"),
            "user_agent": req.get("user_agent"),
            "resource": "price_group",
            "resource_id": _id,
            "found": bool(doc),
            "success": True,
        })
        return self.mapper.out_map(doc) if doc else None

    async def list(self, filters: Optional[Dict[str, Any]], page: int, page_size: int, actor: Optional[Dict[str, Any]], req: Dict[str, Any]):
        res = await self.repo.list(filters=filters or {}, page=page, page_size=page_size)
        # Skip unnecessary mapping since repository already returns the correct format
        return res

    def update(self, _id: str, updates: Dict[str, Any], actor: Optional[Dict[str, Any]], req: Dict[str, Any]):
        clean = {
            k: v.strip() if isinstance(v, str) else v
            for k, v in self.mapper.in_map(updates).items()
            if v is not None and k != "_id"
        }
        updated = self.repo.update(_id, PriceGroupUpdate(**clean).model_dump(exclude_none=True))

        a = self._actor(actor)
        _safe_audit(self.audit_logger, {
            "service": "processing",
            "user_id": a["user_id"],
            "action": "price_group_update",
            "message": f"Update price group {_id}",
            "username": a["username"],
            "ip_address": req.get("ip_address"),
            "user_agent": req.get("user_agent"),
            "resource": "price_group",
            "resource_id": _id,
            "updates": clean,
            "success": bool(updated),
        })
        return self.mapper.out_map(updated) if updated else None

    def delete(self, _id: str, actor: Optional[Dict[str, Any]], req: Dict[str, Any]) -> bool:
        ok = self.repo.delete(_id)

        a = self._actor(actor)
        _safe_audit(self.audit_logger, {
            "service": "processing",
            "user_id": a["user_id"],
            "action": "price_group_delete",
            "message": f"Delete price group {_id}",
            "username": a["username"],
            "ip_address": req.get("ip_address"),
            "user_agent": req.get("user_agent"),
            "resource": "price_group",
            "resource_id": _id,
            "success": ok,
        })
        return ok