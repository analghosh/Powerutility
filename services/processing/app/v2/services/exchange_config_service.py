import logging
from typing import Dict, Any, List

from fastapi import HTTPException, status

from ..repository.exchange_config_repository import ExchangeConfigRepository

logger = logging.getLogger(__name__)


class ExchangeConfigService:

    def __init__(self):
        self.repo = ExchangeConfigRepository()

    async def get_active_list(self) -> List[Dict[str, Any]]:
        return await self.repo.get_active_list()

    async def switch_environment(self, id: str) -> Dict[str, Any]:
        """
        Switches the active environment (UAT ↔ PROD) for a (market, bidType, exchange) group.
        Activates the counterpart first so there is always at least one active.
        Both updates are by _id only — no update_many.
        """
        current = await self.repo.find_by_id(id)
        if not current:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Config not found: {id}"
            )

        market = current.get("market")
        bid_type = current.get("bidType")
        exchange = current.get("exchange")
        current_env = (current.get("environment") or "").upper()

        env_map = {"UAT": "PROD", "PROD": "UAT"}
        opposite_env = env_map.get(current_env)
        if not opposite_env:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown environment '{current_env}'. Expected UAT or PROD."
            )

        counterpart = await self.repo.find_counterpart(market, bid_type, exchange, opposite_env)
        if not counterpart:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No counterpart config found for {exchange} / {market} / {bid_type} / {opposite_env}"
            )

        # Activate counterpart first — ensures at least one is always active
        await self.repo.update_config(counterpart["_id"], {"is_active": True})

        # Then deactivate current
        await self.repo.update_config(id, {"is_active": False})

        return {
            "switched_from": current_env,
            "switched_to": opposite_env,
            "deactivated_id": id,
            "activated_id": counterpart["_id"],
            "market": market,
            "bidType": bid_type,
            "exchange": exchange
        }


exchange_config_service = ExchangeConfigService()
