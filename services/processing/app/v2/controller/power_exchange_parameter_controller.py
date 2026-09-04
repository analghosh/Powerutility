import logging
from typing import Dict, Any, List, Optional

from ..services.power_exchange_parameter_services import power_exchange_parameter_service

logger = logging.getLogger(__name__)


class PowerExchangeParameterController:

    async def create_parameter(self, parameter_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            logger.info(f"creating param for exchange: {parameter_data.get('exchange')}, market: {parameter_data.get('market')}")
            result = await power_exchange_parameter_service.create_parameter(parameter_data)
            logger.info("power_exchange_parameter created")
            return result
        except Exception as e:
            logger.error(f"create_parameter controller error: {e}")
            raise

    async def create_parameters_bulk(self, parameters: List[Dict[str, Any]]) -> Dict[str, Any]:
        try:
            logger.info(f"bulk creating {len(parameters)} params")
            result = await power_exchange_parameter_service.create_parameters_bulk(parameters)
            logger.info(f"bulk insert done, count: {result['count']}")
            return result
        except Exception as e:
            logger.error(f"create_parameters_bulk controller error: {e}")
            raise

    async def get_parameter_by_id(self, parameter_id: str) -> Optional[Dict[str, Any]]:
        try:
            return await power_exchange_parameter_service.get_parameter_by_id(parameter_id)
        except Exception as e:
            logger.error(f"get_parameter_by_id controller error: {e}")
            raise

    async def get_parameter_by_exchange(self, exchange: str) -> List[Dict[str, Any]]:
        try:
            return await power_exchange_parameter_service.get_parameter_by_exchange(exchange)
        except Exception as e:
            logger.error(f"get_parameter_by_exchange controller error: {e}")
            raise

    async def get_parameter_by_exchange_and_market(self, exchange: str, market: str) -> Optional[Dict[str, Any]]:
        try:
            return await power_exchange_parameter_service.get_parameter_by_exchange_and_market(exchange, market)
        except Exception as e:
            logger.error(f"get_parameter_by_exchange_and_market controller error: {e}")
            raise

    async def get_all_parameters(
        self,
        skip: int = 0,
        limit: int = 100,
        sort_by: str = "created_at",
        sort_order: int = -1,
        exchange: Optional[str] = None,
        market: Optional[str] = None
    ) -> Dict[str, Any]:
        try:
            logger.info(f"get_all_parameters - exchange={exchange}, market={market}, skip={skip}, limit={limit}")
            result = await power_exchange_parameter_service.get_all_parameters(
                skip=skip, limit=limit, sort_by=sort_by, sort_order=sort_order,
                exchange=exchange, market=market
            )
            logger.info(f"returned {len(result['parameters'])} records")
            return result
        except Exception as e:
            logger.error(f"get_all_parameters controller error: {e}")
            raise

    async def update_parameter(self, parameter_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            result = await power_exchange_parameter_service.update_parameter(parameter_id, update_data)
            if result:
                logger.info(f"updated param: {parameter_id}")
            return result
        except Exception as e:
            logger.error(f"update_parameter controller error: {e}")
            raise

    async def delete_parameter(self, parameter_id: str, deleted_by: Optional[str] = None) -> bool:
        try:
            result = await power_exchange_parameter_service.delete_parameter(parameter_id, deleted_by)
            if result:
                logger.info(f"soft deleted param: {parameter_id}")
            return result
        except Exception as e:
            logger.error(f"delete_parameter controller error: {e}")
            raise


power_exchange_parameter_controller = PowerExchangeParameterController()
