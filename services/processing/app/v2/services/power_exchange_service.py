import logging
from typing import Dict, Any, Optional

from fastapi import HTTPException, status

from ..repository.power_exchange_repository import PowerExchangeRepository

logger = logging.getLogger(__name__)


class PowerExchangeService:

    def __init__(self):
        self.repo = PowerExchangeRepository()

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new power exchange record; key+market+exchange must be unique."""
        existing = await self.repo.find_by_key(
            data["key"], data["market"], data["exchange"]
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Record already exists for key '{data['key']}', "
                    f"market '{data['market']}', exchange '{data['exchange']}'"
                )
            )

        result = await self.repo.create(data)
        created = await self.repo.find_by_id(str(result.inserted_id))
        return created

    async def get_by_id(self, id: str) -> Dict[str, Any]:
        record = await self.repo.find_by_id(id)
        if not record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Power exchange record not found: {id}"
            )
        return record

    async def get_list(
        self,
        key: Optional[str] = None,
        market: Optional[str] = None,
        exchange: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        filters = {
            k: v
            for k, v in {"key": key, "market": market, "exchange": exchange}.items()
            if v is not None
        }
        return await self.repo.get_paginated(filters, page, page_size)

    async def update(self, id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        current = await self.get_by_id(id)

        if not data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields provided to update"
            )

        # If the unique key combination changes, make sure it does not collide
        new_key = data.get("key", current.get("key"))
        new_market = data.get("market", current.get("market"))
        new_exchange = data.get("exchange", current.get("exchange"))
        duplicate = await self.repo.find_by_key(
            new_key, new_market, new_exchange, exclude_id=id
        )
        if duplicate:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Another record already exists for key '{new_key}', "
                    f"market '{new_market}', exchange '{new_exchange}'"
                )
            )

        await self.repo.update(id, data)
        return await self.repo.find_by_id(id)

    async def delete(self, id: str) -> Dict[str, Any]:
        await self.get_by_id(id)
        await self.repo.delete(id)
        return {"deleted": True, "id": id}


power_exchange_service = PowerExchangeService()
