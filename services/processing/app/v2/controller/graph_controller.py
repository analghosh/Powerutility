import logging
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime

from fastapi import HTTPException, status
from ..services.graph_services import graph_service

logger = logging.getLogger(__name__)


class GraphController:
    """
    Controller for Graph-related operations.
    Handles HTTP requests and delegates business logic to service layer.
    """

    def __init__(self):
        self.graph_service = graph_service

    async def demand_blocks(
        self,
        markets: Optional[List[str]],
        date_from: Optional[str],
        date_to: Optional[str],
        from_block: Optional[int],
        to_block: Optional[int],
        group_by_date: bool,
        zones: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Get demand blocks data for graph visualization.
        
        Args:
            markets: List of market types to filter by
            date_from: Start date for filtering
            date_to: End date for filtering
            from_block: Starting block number
            to_block: Ending block number
            group_by_date: Whether to group results by date
            zones: List of zones to filter by
            
        Returns:
            Dict containing demand blocks data
        """
        try:
            logger.info(f"Fetching demand blocks data for markets={markets}, zones={zones}")
            return await self.graph_service.demand_blocks(
                markets=markets,
                date_from=date_from,
                date_to=date_to,
                from_block=from_block,
                to_block=to_block,
                group_by_date=group_by_date,
                zones=zones,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(
                "demand_blocks failed | markets=%s date_from=%s date_to=%s from_block=%s to_block=%s group_by_date=%s zones=%s",
                markets, date_from, date_to, from_block, to_block, group_by_date, zones
            )
            raise HTTPException(status_code=500, detail="Failed to fetch demand blocks.")


    async def all_three_unified(
        self,
        markets: Optional[List[str]],
        zones: Optional[List[str]],
        date_from: Optional[str],
        date_to: Optional[str],
        from_block: Optional[int],
        to_block: Optional[int],
        group_by_date: bool,
        denominator: Literal["matched", "expected"],
    ) -> Dict[str, Any]:
        """
        Get unified data combining blocks, demand curve, and MAPE curve.
        
        Args:
            markets: List of market types to filter by
            zones: List of zones to filter by
            date_from: Start date for filtering
            date_to: End date for filtering
            from_block: Starting block number
            to_block: Ending block number
            group_by_date: Whether to group results by date
            denominator: Type of denominator for percentage calculation
            
        Returns:
            Dict containing unified graph data
        """
        try:
            logger.info(f"Fetching unified graph data for markets={markets}, zones={zones}")
            return await self.graph_service.all_three_unified(
                markets=markets,
                zones=zones,
                date_from=date_from,
                date_to=date_to,
                from_block=from_block,
                to_block=to_block,
                group_by_date=group_by_date,
                denominator=denominator,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(
                "all_three_unified failed | markets=%s zones=%s date_from=%s date_to=%s "
                "from_block=%s to_block=%s group_by_date=%s denominator=%s",
                markets, zones, date_from, date_to, from_block, to_block, group_by_date, denominator
            )
            raise HTTPException(status_code=500, detail="Failed to compute combined analysis.")


    async def selected_days_blocks_and_stats(
        self,
        *,
        dates: List[str],
        markets: Optional[List[str]],
        zones: Optional[List[str]],
        from_block: Optional[int],
        to_block: Optional[int],
        group_by_date: bool,
    ) -> Dict[str, Any]:
        """
        Get selected days blocks and statistics data.
        
        Args:
            dates: List of dates to fetch data for
            markets: List of market types to filter by
            zones: List of zones to filter by
            from_block: Starting block number
            to_block: Ending block number
            group_by_date: Whether to group results by date
            
        Returns:
            Dict containing selected days blocks and stats data
        """
        try:
            logger.info(f"Fetching selected days data for dates={dates}, markets={markets}, zones={zones}")
            return await self.graph_service.selected_days_blocks_and_stats(
                dates=dates,
                markets=markets,
                zones=zones,
                from_block=from_block,
                to_block=to_block,
                group_by_date=group_by_date,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(
                "selected_days_blocks_and_stats failed | dates=%s markets=%s zones=%s from_block=%s to_block=%s group_by_date=%s",
                dates, markets, zones, from_block, to_block, group_by_date
            )
            raise HTTPException(status_code=500, detail="Failed to fetch selected days data.")


# Create controller instance
graph_controller = GraphController()
