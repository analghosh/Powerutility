from typing import Dict, Any, List

from ..services.exchange_config_service import exchange_config_service


class ExchangeConfigController:

    async def get_active_list(self) -> List[Dict[str, Any]]:
        return await exchange_config_service.get_active_list()

    async def switch_environment(self, id: str) -> Dict[str, Any]:
        return await exchange_config_service.switch_environment(id)


exchange_config_controller = ExchangeConfigController()
