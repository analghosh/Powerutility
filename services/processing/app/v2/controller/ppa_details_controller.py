from typing import Any, Dict, Optional
from fastapi import HTTPException, status, UploadFile

from ..services.ppa_details_services import ppa_details_service


class PpaDetailsController:
    def __init__(self):
        self.service = ppa_details_service

    async def get_ppa(self, ppa_oid: str, actor: Dict[str, Any], request_info: Dict[str, Any]) -> Dict[str, Any]:
        doc = await self.service.get_ppa(ppa_oid, actor, request_info)
        if not doc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PPA details not found")
        return doc

    async def list_ppa(
        self,
        filters: Optional[Dict[str, Any]],
        search: Optional[str],
        page: int,
        page_size: int,
        actor: Dict[str, Any],
        request_info: Dict[str, Any],
    ) -> Dict[str, Any]:
        return await self.service.list_ppa(filters, search, page, page_size, actor, request_info)

    async def update_ppa(
        self,
        ppa_oid: str,
        updates: Dict[str, Any],
        actor: Dict[str, Any],
        request_info: Dict[str, Any],
    ) -> Dict[str, Any]:
        try:
            doc = await self.service.update_ppa(ppa_oid, updates, actor, request_info)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        if not doc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PPA details not found")
        return doc

    async def delete_ppa(self, ppa_oid: str, actor: Dict[str, Any], request_info: Dict[str, Any]) -> Dict[str, Any]:
        ok = await self.service.delete_ppa(ppa_oid, actor, request_info)
        if not ok:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PPA details not found")
        return {"deleted": True}

    async def sync_from_plants(self, actor: Dict[str, Any], request_info: Dict[str, Any]) -> Dict[str, Any]:
        return await self.service.sync_all_from_plants()

    async def upload_file(
        self,
        ppa_oid: str,
        file: UploadFile,
        actor: Dict[str, Any],
        request_info: Dict[str, Any],
    ) -> Dict[str, Any]:
        return await self.service.upload_ppa_file(ppa_oid, file, actor, request_info)

    async def get_file(self, ppa_oid: str, file_id: str) -> Dict[str, Any]:
        return await self.service.get_file_path(ppa_oid, file_id)

    async def delete_file(
        self,
        ppa_oid: str,
        file_id: str,
        actor: Dict[str, Any],
        request_info: Dict[str, Any],
    ) -> Dict[str, Any]:
        return await self.service.delete_ppa_file(ppa_oid, file_id, actor, request_info)


ppa_details_controller = PpaDetailsController()
