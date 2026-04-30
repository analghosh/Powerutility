from typing import Any, Dict, Optional
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from bson import ObjectId

from libs.models.price_group_entity import PriceGroupCreate, PriceGroupUpdate
from ..repository.price_group_repository import PriceGroupRepository

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
            return None
        return {cls.reverse_mapping.get(k, k): v for k, v in d.items()}


def _safe_audit(audit_logger, kwargs):
    try:
        audit_logger.log_user_action(**kwargs)
    except Exception:
        pass


class PriceGroupService:

    def __init__(self, repo: PriceGroupRepository, audit_logger: AuditLogger):
        self.repo = repo
        self.mapper = PriceGroupPayloadMapper
        self.audit_logger = audit_logger

    def _actor(self, actor: Optional[Dict[str, Any]]):
        actor = actor or {}
        uid = actor.get("user_id")
        if uid in (None, ""):
            uid = "anonymous"
        return {"user_id": str(uid), "username": actor.get("username") or "anonymous"}

    async def create(self, payload: Dict[str, Any], actor: Optional[Dict[str, Any]], req: Dict[str, Any]) -> str:
        from pymongo.errors import DuplicateKeyError

        data = self.mapper.in_map(payload)
        for k, v in list(data.items()):
            if isinstance(v, str):
                data[k] = v.strip()

        try:
            new_id = await self.repo.create(PriceGroupCreate(**data).model_dump())
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
        return new_id

    async def get(self, _id: str, actor: Optional[Dict[str, Any]], req: Dict[str, Any]):
        doc = await self.repo.get(_id)
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
        return await self.repo.list(filters=filters or {}, page=page, page_size=page_size)

    async def update(self, _id: str, updates: Dict[str, Any], actor: Optional[Dict[str, Any]], req: Dict[str, Any]):
        clean = {
            k: v.strip() if isinstance(v, str) else v
            for k, v in self.mapper.in_map(updates).items()
            if v is not None and k != "_id"
        }
        updated = await self.repo.update(_id, PriceGroupUpdate(**clean).model_dump(exclude_none=True))

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

    async def delete(self, _id: str, actor: Optional[Dict[str, Any]], req: Dict[str, Any]) -> bool:
        ok = await self.repo.delete(_id)

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
