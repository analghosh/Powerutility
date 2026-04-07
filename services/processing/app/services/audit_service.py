import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from bson import ObjectId
from pymongo import DESCENDING, ASCENDING
from ..repository.audit_repository import AuditRepository

logger = logging.getLogger(__name__)

class AuditService:
    """
    Read-only business logic for audit logs (list + stats).
    """
    def __init__(self):
        self.repo = AuditRepository()

    def _to_out(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        if not doc:
            return {}
        doc = dict(doc)
        doc["id"] = str(doc.pop("_id")) if doc.get("_id") else None

        # The 'actor' field is sometimes a dict, which causes validation errors.
        # This ensures it's always a string before returning.
        if isinstance(doc.get("actor"), dict):
            actor_obj = doc["actor"]
            # Use username, name, or ID as the actor string
            if actor_obj.get("username"):
                doc["actor"] = actor_obj["username"]
            elif actor_obj.get("name"):
                doc["actor"] = actor_obj["name"]
            elif actor_obj.get("id"):
                doc["actor"] = str(actor_obj["id"])
            else:
                # Fallback to a string representation of the object
                doc["actor"] = str(actor_obj)
        return doc

    def _build_query(self, f: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build Mongo query from filters. Supports top-level fields and selected details.* fields.
        """
        q: Dict[str, Any] = {}

        # ——— Top-level direct matches ———
        for key in ["event_type", "category", "action", "user_id", "username",
                    "ip_address", "service", "success", "actor", "performed_by",
                    "resource_type", "resource_id", "target_user_id"]:
            val = f.get(key)
            if val not in (None, ""):
                q[key] = val

        # ——— Time window ———
        start = f.get("start_date")
        end = f.get("end_date")
        if start or end:
            q["timestamp"] = {}
            if start:
                q["timestamp"]["$gte"] = start
            if end:
                q["timestamp"]["$lte"] = end

        # ——— “reason/method/status_text” etc. from details.* ———
        # To keep Swagger simple (no arbitrary dotpaths), we expose a few
        # common detail keys as first-class query params and map them here.
        details_map = {
            "reason": f.get("reason"),
            "method": f.get("method"),                  # e.g., TOTP/SMS/Email/self-service/admin-forced
            "old_value": f.get("old_value"),
            "new_value": f.get("new_value"),
            "old_role": f.get("old_role"),
            "new_role": f.get("new_role"),
            "old_status": f.get("old_status"),
            "new_status": f.get("new_status"),
            "file_type": f.get("file_type"),            # e.g., PII/financial/...
            "revision": f.get("revision"),              # bid/file revision
            "record_count": f.get("record_count"),
        }
        for k, v in details_map.items():
            if v not in (None, ""):
                q[f"details.{k}"] = v

        # ——— Free text across multiple fields (regex, case-insensitive) ———
        search = f.get("search")
        if search:
            regex = {"$regex": search, "$options": "i"}
            q["$or"] = [
                {"username": regex},
                {"actor": regex},
                {"performed_by": regex},
                {"ip_address": regex},
                {"user_agent": regex},
                {"error_message": regex},
                {"service": regex},
                {"event_type": regex},
                {"action": regex},
                {"category": regex},
                {"resource_type": regex},
                {"resource_id": regex},
                {"details": regex},  # serialized dict text match
            ]
        return q

    def list_logs(
        self,
        filters: Dict[str, Any],
        page: int = 1,
        page_size: int = 50,
        sort: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Returns (records, total). Latest-first by default.
        sort: '-timestamp' (default) or 'timestamp'
        """
        q = self._build_query(filters)
        order = ASCENDING if sort == "timestamp" else DESCENDING

        cursor = (
            self.repo.collection
            .find(q)
            .sort("timestamp", order)
            .skip((max(page, 1) - 1) * page_size)
            .limit(page_size)
        )
        rows = [self._to_out(d) for d in cursor]
        total = self.repo.collection.count_documents(q)
        return rows, total

    def user_logs(self, user_id: str, page: int = 1, page_size: int = 50):
        return self.list_logs({"user_id": user_id}, page, page_size)

    def stats(self, days: int = 30) -> Dict[str, Any]:
        return self.repo.get_audit_stats(days=days)

# Global instance
audit_service = AuditService()
