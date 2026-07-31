import logging
from typing import List, Dict, Any, Optional

from ..repository.power_exchange_parameter_repository import power_exchange_parameter_repository

logger = logging.getLogger(__name__)

VALID_EXCHANGES = {"iex", "hpx", "pxil"}
VALID_MARKETS   = {"rtm", "dam", "gdam", "hpdam"}
VALID_BUY_SELL  = {"buy", "sell", "both"}
VALID_BID_TYPES = {"single_bid", "block_bid", "both"}
VALID_OCF       = {"ocf", "standalone"}


class PowerExchangeParameterService:

    def _validate_parameter_data(self, data: Dict[str, Any]) -> None:
        exchange = data.get("exchange")
        if not exchange:
            raise ValueError("exchange is required")
        if exchange.lower() not in VALID_EXCHANGES:
            raise ValueError(f"Invalid exchange '{exchange}'. Must be one of {VALID_EXCHANGES}")
        data["exchange"] = exchange.lower()

        market = data.get("market")
        if not market:
            raise ValueError("market is required")
        market_lower = market.lower()
        if market_lower not in VALID_MARKETS:
            raise ValueError(f"Invalid market '{market}'. Must be one of {VALID_MARKETS}")
        data["market"] = market_lower

        buy_sell = data.get("buy_sell")
        if buy_sell is not None:
            if buy_sell.lower() not in VALID_BUY_SELL:
                raise ValueError(f"Invalid buy_sell '{buy_sell}'. Must be one of {VALID_BUY_SELL}")
            data["buy_sell"] = buy_sell.lower()

        bid_type = data.get("bid_type")
        if bid_type is not None:
            if bid_type.lower() not in VALID_BID_TYPES:
                raise ValueError(f"Invalid bid_type '{bid_type}'. Must be one of {VALID_BID_TYPES}")
            data["bid_type"] = bid_type.lower()

        allocation = data.get("allocation_percent")
        if allocation is not None:
            if not isinstance(allocation, (int, float)) or isinstance(allocation, bool):
                raise ValueError("allocation_percent must be a number")
            if not (0 <= allocation <= 100):
                raise ValueError("allocation_percent must be between 0 and 100")

        ocf = data.get("ocf_standalone")
        if ocf is not None:
            if ocf.lower() not in VALID_OCF:
                raise ValueError(f"Invalid ocf_standalone '{ocf}'. Must be 'ocf' or 'standalone'")
            ocf_lower = ocf.lower()
            # RTM market can only be standalone - business rule
            if market_lower == "rtm" and ocf_lower != "standalone":
                raise ValueError("ocf_standalone must be 'standalone' when market is 'rtm'")
            data["ocf_standalone"] = ocf_lower

    async def create_parameter(self, parameter_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            self._validate_parameter_data(parameter_data)
            existing = await power_exchange_parameter_repository.get_parameter_by_exchange_and_market(
                parameter_data["exchange"], parameter_data["market"]
            )
            if existing:
                raise ValueError(
                    f"Configuration for exchange '{parameter_data['exchange']}' "
                    f"and market '{parameter_data['market']}' already exists"
                )
            return await power_exchange_parameter_repository.create_parameter(parameter_data)
        except Exception as e:
            logger.error(f"create_parameter service error: {e}")
            raise

    async def create_parameters_bulk(self, parameters: List[Dict[str, Any]]) -> Dict[str, Any]:
        try:
            combinations = []
            for param in parameters:
                self._validate_parameter_data(param)
                combinations.append((param["exchange"], param["market"]))

            if len(combinations) != len(set(combinations)):
                raise ValueError("Duplicate exchange/market pairs found in request")

            for exchange, market in combinations:
                existing = await power_exchange_parameter_repository.get_parameter_by_exchange_and_market(exchange, market)
                if existing:
                    raise ValueError(f"Configuration for exchange '{exchange}' and market '{market}' already exists")

            return await power_exchange_parameter_repository.create_parameters_bulk(parameters)
        except Exception as e:
            logger.error(f"create_parameters_bulk service error: {e}")
            raise

    async def get_parameter_by_id(self, parameter_id: str) -> Optional[Dict[str, Any]]:
        try:
            return await power_exchange_parameter_repository.get_parameter_by_id(parameter_id)
        except Exception as e:
            logger.error(f"get_parameter_by_id service error: {e}")
            raise

    async def get_parameter_by_exchange(self, exchange: str) -> List[Dict[str, Any]]:
        try:
            return await power_exchange_parameter_repository.get_parameter_by_exchange(exchange)
        except Exception as e:
            logger.error(f"get_parameter_by_exchange service error: {e}")
            raise

    async def get_parameter_by_exchange_and_market(self, exchange: str, market: str) -> Optional[Dict[str, Any]]:
        try:
            return await power_exchange_parameter_repository.get_parameter_by_exchange_and_market(exchange, market)
        except Exception as e:
            logger.error(f"get_parameter_by_exchange_and_market service error: {e}")
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
            # validate filter values before hitting DB
            if exchange and exchange.lower() not in VALID_EXCHANGES:
                raise ValueError(f"Invalid exchange filter '{exchange}'. Must be one of {VALID_EXCHANGES}")
            if market and market.lower() not in {"rtm", "dam"}:
                raise ValueError(f"Invalid market filter '{market}'. Must be 'rtm' or 'dam'")

            return await power_exchange_parameter_repository.get_all_parameters(
                skip=skip, limit=limit, sort_by=sort_by, sort_order=sort_order,
                exchange=exchange, market=market
            )
        except Exception as e:
            logger.error(f"get_all_parameters service error: {e}")
            raise

    async def update_parameter(self, parameter_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            existing = await power_exchange_parameter_repository.get_parameter_by_id(parameter_id)
            if not existing:
                return None

            # merge incoming changes with existing values so validation has full context
            merged = {
                "exchange":          update_data.get("exchange")          or existing.get("exchange"),
                "market":            update_data.get("market")            or existing.get("market"),
                "buy_sell":          update_data.get("buy_sell")          or existing.get("buy_sell"),
                "bid_type":          update_data.get("bid_type")          or existing.get("bid_type"),
                "allocation_percent": update_data.get("allocation_percent") if "allocation_percent" in update_data else existing.get("allocation_percent"),
                "ocf_standalone":    update_data.get("ocf_standalone")    or existing.get("ocf_standalone"),
            }
            self._validate_parameter_data(merged)

            # apply normalized values from validation back onto the update payload
            for key in ("exchange", "market", "buy_sell", "bid_type", "ocf_standalone"):
                if key in update_data and merged.get(key) is not None:
                    update_data[key] = merged[key]

            # check if exchange+market combo would conflict with another doc
            if "exchange" in update_data or "market" in update_data:
                new_ex = (update_data.get("exchange") or existing.get("exchange")).lower()
                new_mk = (update_data.get("market")   or existing.get("market")).lower()
                if new_ex != existing.get("exchange") or new_mk != existing.get("market"):
                    conflict = await power_exchange_parameter_repository.get_parameter_by_exchange_and_market(new_ex, new_mk)
                    if conflict and str(conflict.get("_id")) != parameter_id:
                        raise ValueError(f"Configuration for exchange '{new_ex}' and market '{new_mk}' already exists")

            return await power_exchange_parameter_repository.update_parameter(parameter_id, update_data)
        except Exception as e:
            logger.error(f"update_parameter service error: {e}")
            raise

    async def delete_parameter(self, parameter_id: str, deleted_by: Optional[str] = None) -> bool:
        try:
            return await power_exchange_parameter_repository.delete_parameter(parameter_id, deleted_by)
        except Exception as e:
            logger.error(f"delete_parameter service error: {e}")
            raise


power_exchange_parameter_service = PowerExchangeParameterService()
