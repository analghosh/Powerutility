
from typing import Any, Dict, Optional, List
from fastapi import HTTPException, status, UploadFile

from ..services.master_plants_services import master_plants_service
from io import BytesIO
class MasterPlantsController:
    def __init__(self):
        self.service = master_plants_service

    async def create_plant(self, payload: Dict[str, Any], actor: Dict[str, Any], request_info: Dict[str, Any]) -> Dict[str, Any]:
        try:
            new_id = await self.service.create_plant(payload, actor, request_info)
            return {"_id": new_id}
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    async def get_plant(self, plant_oid: str, actor: Dict[str, Any], request_info: Dict[str, Any]) -> Dict[str, Any]:
        doc = await self.service.get_plant(plant_oid, actor, request_info)
        if not doc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plant not found")
        return doc

    async def list_plants(self, filters: Optional[Dict[str, Any]], page: int, page_size: int, actor: Dict[str, Any], request_info: Dict[str, Any]) -> Dict[str, Any]:
        return await self.service.list_plants(filters, page, page_size, actor, request_info)

    async def update_plant(self, plant_oid: str, updates: Dict[str, Any], actor: Dict[str, Any], request_info: Dict[str, Any]) -> Dict[str, Any]:
        doc = await self.service.update_plant(plant_oid, updates, actor, request_info)
        if not doc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plant not found or no change")
        return doc

    async def delete_plant(self, plant_oid: str, actor: Dict[str, Any], request_info: Dict[str, Any]) -> Dict[str, Any]:
        ok = await self.service.delete_plant(plant_oid, actor, request_info)
        if not ok:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plant not found")
        return {"deleted": True}

    async def get_all_plants(self, actor: Dict[str, Any], request_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Retrieve all master plants without pagination for CSV export.
        The service method returns a list of plant dictionaries.
        """
        return await self.service.get_all_plants()

    async def export_csv(self, actor: Dict[str, Any], request_info: Dict[str, Any]) -> BytesIO:
        """Delegate CSV generation to service and return the buffer.
        The controller does not manipulate data; business logic stays in the service.
        """
        return await self.service.export_csv()

    async def process_plant_file(self, file: UploadFile, actor: Dict[str, Any], request_info: Dict[str, Any]) -> Dict[str, Any]:
        return await self.service.process_plant_file(file, actor, request_info)

master_plants_controller = MasterPlantsController()
