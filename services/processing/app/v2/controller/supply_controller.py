
"""
Supply V2 Controller
Enhanced supply controller with proper error handling and logging
"""

import logging
from typing import Dict, Any, Optional, List, Tuple
from fastapi import HTTPException, status

from ..services.supply_services import supply_service

logger = logging.getLogger(__name__)


class SupplyController:
    """
    Controller for Supply V2 operations.
    Handles HTTP requests and delegates business logic to service layer.
    """

    def __init__(self):
        self.supply_service = supply_service

    async def get_supply_graph_bundle(self, schedule_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Get supply graph bundle data.
        
        Args:
            schedule_date: Optional date filter in YYYY-MM-DD format
            
        Returns:
            Dict containing supply graph bundle data
        """
        try:
            logger.info(f"Fetching supply graph bundle data for schedule_date: {schedule_date}")
            result = await self.supply_service.get_supply_graph_bundle(schedule_date)
            return result
        except Exception as e:
            logger.error(f"Error in supply graph bundle controller: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch supply graph bundle data: {str(e)}"
            )

    async def get_supply_block_bundle(self, schedule_date: Optional[str] = None, time_block: Optional[int] = None) -> Dict[str, Any]:
        """
        Get supply block bundle data.
        
        Args:
            schedule_date: Optional date filter in YYYY-MM-DD format
            time_block: Optional time block filter (1-96)
            
        Returns:
            Dict containing supply block bundle data
        """
        try:
            logger.info(f"Fetching supply block bundle data for schedule_date: {schedule_date}")
            result = await self.supply_service.get_supply_block_bundle(schedule_date, time_block)
            return result
        except Exception as e:
            logger.error(f"Error in supply block bundle controller: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch supply block bundle data: {e}"
            )

    async def get_supply_by_technology_block(self, schedule_date: Optional[str] = None, time_block: Optional[int] = None) -> Dict[str, Any]:
        """Get single-block supply grouped by technology."""
        try:
            return await self.supply_service.get_supply_by_technology_block(schedule_date, time_block)
        except Exception as e:
            logger.error(f"Error in get_supply_by_technology_block controller: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch supply by technology: {e}"
            )

    async def get_supply_by_price_group_block(self, schedule_date: Optional[str] = None, time_block: Optional[int] = None) -> Dict[str, Any]:
        """Get single-block supply grouped by price group."""
        try:
            return await self.supply_service.get_supply_by_price_group_block(schedule_date, time_block)
        except Exception as e:
            logger.error(f"Error in get_supply_by_price_group_block controller: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch supply by price group: {e}"
            )

    async def get_supply_raw(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        from_block: Optional[int] = None,
        to_block: Optional[int] = None,
        plant_id: Optional[List[str]] = None,
        plant_name: Optional[List[str]] = None,
        c_s: Optional[List[str]] = None,
        category: Optional[List[str]] = None,
        technology: Optional[List[str]] = None,
        price_group: Optional[List[str]] = None,
        dc_sg: Optional[List[str]] = None,
        ramping_min: Optional[float] = None,
        ramping_max: Optional[float] = None,
        TM_min: Optional[float] = None,
        TM_max: Optional[float] = None,
        FC_min: Optional[float] = None,
        FC_max: Optional[float] = None,
        VC_min: Optional[float] = None,
        VC_max: Optional[float] = None,
        px_min: Optional[float] = None,
        px_max: Optional[float] = None,
        mult_min: Optional[float] = None,
        mult_max: Optional[float] = None,
        vol_min: Optional[float] = None,
        vol_max: Optional[float] = None,
        updated_from: Optional[str] = None,
        updated_to: Optional[str] = None,
        include: Optional[str] = None,
        exclude: Optional[str] = None,
        sort: Optional[str] = None,
        skip: int = 0,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Get raw supply data with dynamic filtering."""
        try:
            # Build filters dict for the service
            filters: Dict[str, Any] = {}

            def _maybe_in(name: str, values: Optional[List[str]]):
                if values:
                    filters[name] = values

            _maybe_in("plant_id", plant_id)
            _maybe_in("plant_name", plant_name)
            _maybe_in("c_s", c_s)
            _maybe_in("category", category)
            _maybe_in("technology", technology)
            _maybe_in("price_group", price_group)
            _maybe_in("dc_sg", dc_sg)

            def _range(field: str, lo: Optional[float], hi: Optional[float]):
                rng: Dict[str, Any] = {}
                if lo is not None: rng["$gte"] = lo
                if hi is not None: rng["$lte"] = hi
                if rng: filters[field] = rng

            _range("ramping", ramping_min, ramping_max)
            _range("TM", TM_min, TM_max)
            _range("FC_price", FC_min, FC_max)
            _range("VC_price", VC_min, VC_max)
            _range("px_price", px_min, px_max)
            _range("multiplier", mult_min, mult_max)
            _range("volume", vol_min, vol_max)

            if updated_from or updated_to:
                rng: Dict[str, Any] = {}
                if updated_from: rng["$gte"] = {"$date": updated_from} if "T" in updated_from else updated_from
                if updated_to:   rng["$lte"] = {"$date": updated_to} if "T" in updated_to else updated_to
                filters["updated_at"] = rng

            include_fields = [s.strip() for s in include.split(",")] if include else None
            exclude_fields = [s.strip() for s in exclude.split(",")] if exclude else None

            sort_list: Optional[List[Tuple[str, int]]] = None
            if sort:
                sort_list = []
                parts = [p.strip() for p in sort.split(",") if p.strip()]
                for p in parts:
                    if ":" in p:
                        f, d = p.split(":", 1)
                        direction = 1 if d.lower() in ("1", "asc", "ascending", "+") else -1
                    else:
                        f, direction = p, 1
                    sort_list.append((f.strip(), direction))
            else:
                # Default sort order
                sort_list = [("schedule_date", 1), ("time_block", 1)]

            result = await self.supply_service.get_supply_raw(
                date_from=date_from,
                date_to=date_to,
                from_block=from_block,
                to_block=to_block,
                filters=filters,
                include_fields=include_fields,
                exclude_fields=exclude_fields,
                sort=sort_list,
                skip=skip,
                limit=limit,
            )
            return result
        except Exception as e:
            logger.error(f"Error in raw supply controller: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch raw supply data: {e}"
            )


# Global instance
supply_controller = SupplyController()
