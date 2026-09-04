from typing import Dict, Any, Optional

from ..services.power_exchange_service import power_exchange_service


class PowerExchangeController:

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return await power_exchange_service.create(data)

    async def get_by_id(self, id: str) -> Dict[str, Any]:
        return await power_exchange_service.get_by_id(id)

    async def get_list(
        self,
        key: Optional[str] = None,
        market: Optional[str] = None,
        exchange: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        return await power_exchange_service.get_list(key, market, exchange, page, page_size)

    async def update(self, id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        return await power_exchange_service.update(id, data)

    async def delete(self, id: str) -> Dict[str, Any]:
        return await power_exchange_service.delete(id)


power_exchange_controller = PowerExchangeController()
