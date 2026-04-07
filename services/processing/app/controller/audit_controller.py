import logging
from typing import Dict, Any, Optional, Tuple
from fastapi import HTTPException, status, Request
from ..services.audit_service import audit_service

logger = logging.getLogger(__name__)

class AuditController:
    def __init__(self):
        self.svc = audit_service

    def _meta_from_request(self, request: Request) -> Dict[str, Optional[str]]:
        return {
            "ip_address": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
            "path": request.url.path,
            "method": request.method,
        }

    async def create_log(self, payload: Dict[str, Any], request: Request) -> Dict[str, Any]:
        try:
            # auto-fill ip/user_agent when not passed
            payload.setdefault("ip_address", self._meta_from_request(request).get("ip_address"))
            payload.setdefault("user_agent", self._meta_from_request(request).get("user_agent"))
            new_id = self.svc.create_log(payload)
            return {"id": new_id}
        except Exception as e:
            logger.exception("Failed to create audit log")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    async def get_log(self, log_id: str) -> Dict[str, Any]:
        doc = self.svc.get_log_by_id(log_id)
        if not doc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audit log not found")
        return doc

    async def list_logs(self, filters: Dict[str, Any], page: int, page_size: int, sort: Optional[str]):
        try:
            return self.svc.list_logs(filters, page, page_size, sort)
        except Exception:
            logger.exception("Failed to list audit logs")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to list audit logs")

    async def user_logs(self, user_id: str, page: int, page_size: int):
        try:
            return self.svc.user_logs(user_id, page, page_size)
        except Exception:
            logger.exception("Failed to list user audit logs")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to list user audit logs")

    async def stats(self, days: int):
        try:
            return self.svc.stats(days)
        except Exception:
            logger.exception("Failed to get audit stats")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get audit stats")

    async def cleanup(self, days: int) -> int:
        try:
            return self.svc.cleanup(days)
        except Exception:
            logger.exception("Failed to cleanup audit logs")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to cleanup audit logs")

# Global controller
audit_controller = AuditController()
