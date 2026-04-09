from typing import List
import logging
from typing import Any, Dict, Optional
from fastapi import HTTPException, status, UploadFile
from ..services.master_plants_services import ServeMasterPlantsService

logger = logging.getLogger(__name__)


class ServeMasterPlantsController:
    def __init__(self, service: ServeMasterPlantsService = ServeMasterPlantsService()):
        self.service = service

    # --------- helpers ---------
    def _actor_from_user(self, user: Optional[Any]) -> Dict[str, Any]:
        if not user:
            return {"user_id": None, "username": "anonymous"}
        # AuthUserDTO typically exposes id & username
        return {"user_id": getattr(user, "id", None), "username": getattr(user, "username", None)}

    # ---------- CRUD ----------
    def create(self, payload: Dict[str, Any], user: Optional[Any], request_info: Dict[str, Any]) -> Dict[str, Any]:
        try:
            new_id = self.service.create(payload, self._actor_from_user(user), request_info)
            return {"_id": new_id}
        except Exception as e:
            logger.exception("create master plant failed")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    def get(self, _id: str, user: Optional[Any], request_info: Dict[str, Any]) -> Dict[str, Any]:
        doc = self.service.get(_id, self._actor_from_user(user), request_info)
        if not doc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        return doc

    async def list(self, filters: Optional[Dict[str, Any]], page: int, page_size: int,
             user: Optional[Any], request_info: Dict[str, Any]) -> Dict[str, Any]:
        return await self.service.list(filters, page, page_size, self._actor_from_user(user), request_info)

    def update(self, _id: str, updates: Dict[str, Any], user: Optional[Any], request_info: Dict[str, Any]) -> Dict[str, Any]:
        doc = self.service.update(_id, updates, self._actor_from_user(user), request_info)
        if not doc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found or no change")
        return doc

    def delete(self, _id: str, user: Optional[Any], request_info: Dict[str, Any]) -> Dict[str, Any]:
        ok = self.service.delete(_id, self._actor_from_user(user), request_info)
        if not ok:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        return {"deleted": True}

    async def process_plant_file(self, file: UploadFile) -> Dict[str, Any]:
        return await self.service.process_plant_file(file)

    async def get_plants(self, filter_by: Optional[str], filter_value: Optional[str], skip: int, limit: int) -> List[Dict[str, Any]]:
        return await self.service.get_plants(filter_by, filter_value, skip, limit)



# Global instance
serve_master_plants_controller = ServeMasterPlantsController()
