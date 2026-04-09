from typing import Any, Dict, Optional
from ..services.price_group_service import PriceGroupService


class PriceGroupController:
    def __init__(self, service: PriceGroupService):
        self.service = service


    def create(self, payload: Dict[str, Any], actor: Optional[Dict[str, Any]], req: Dict[str, Any]) -> str:
        return self.service.create(payload, actor, req)


    def get(self, _id: str, actor: Optional[Dict[str, Any]], req: Dict[str, Any]):
        return self.service.get(_id, actor, req)


    async def list(self, filters: Optional[Dict[str, Any]], page: int, page_size: int, actor: Optional[Dict[str, Any]], req: Dict[str, Any]):
        return await self.service.list(filters, page, page_size, actor, req)


    def update(self, _id: str, updates: Dict[str, Any], actor: Optional[Dict[str, Any]], req: Dict[str, Any]):
        return self.service.update(_id, updates, actor, req)


    def delete(self, _id: str, actor: Optional[Dict[str, Any]], req: Dict[str, Any]) -> bool:
        return self.service.delete(_id, actor, req)