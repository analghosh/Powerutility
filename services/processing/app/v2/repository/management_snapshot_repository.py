import logging
import asyncio
from typing import Dict, Any, List
from datetime import datetime

# Assumes your settings and GenericRepository are available
from .base_repository import GenericRepository
from ...core.config import settings

logger = logging.getLogger(__name__)

class ManagementSnapshotRepository:
    """
    Refined repository for Management Snapshot data.
    Uses three separate, concurrent aggregation queries to fetch data.
    """

    def __init__(self):
        self.repository = GenericRepository()
        # Collection names are used in the helper methods
        self.serve_supply = settings.MONGODB_SUPPLY_SERVE_COLLECTION
        self.serve_demand = settings.MONGODB_DEMAND_SERVE_COLLECTION
        self.serve_px_bid = settings.MONGODB_SERVE_PX_BID_COLLECTION
        self.serve_exchange = settings.MONGODB_SERVE_EXCHANGE_COLLECTION

    async def get_demand_supply_snapshot(
        self,
        schedule_date_str: str,
        from_block: int,
        to_block: int
    ) -> Dict[str, Any]:
        """
        Get demand-supply snapshot data using three concurrent aggregation calls.
        """
        try:
            logger.info(f"Fetching snapshot for {schedule_date_str} from block {from_block} to {to_block}")

            if 'T' not in schedule_date_str:
                schedule_date_str += "T00:00:00.000Z"
            schedule_date = datetime.fromisoformat(schedule_date_str.replace("Z", "+00:00"))

            # Run the three independent queries concurrently
            supply_data, demand_data, trade_data = await asyncio.gather(
                self._get_supply_data(schedule_date, from_block, to_block),
                self._get_demand_data(schedule_date, from_block, to_block),
                self._get_trade_data(schedule_date, from_block, to_block)
            )

            # Combine the results in Python
            combined_data = self._combine_snapshot_data(
                supply_data, demand_data, trade_data,
                schedule_date_str, from_block, to_block
            )

            logger.info("Successfully retrieved and combined demand-supply snapshot")
            return combined_data

        except Exception as e:
            logger.error(f"Error fetching demand-supply snapshot: {e}", exc_info=True)
            raise

    async def _get_supply_data(self, schedule_date: datetime, from_block: int, to_block: int) -> Dict[int, Dict]:
        """Fetches and processes supply data."""
        pipeline = [
            {"$match": {
                "scheduled_at": schedule_date,
                "time_block": {"$gte": from_block, "$lte": to_block},
                "dc_sg": "SG"
            }},
            {"$group": {
                "_id": "$time_block",
                "total_supply": {"$sum": "$volume"}
            }}
        ]
        results = await self.repository.aggregate_async(self.serve_supply, pipeline)

        # If no SG results, fallback to DC
        if not results:
            pipeline[0]["$match"]["dc_sg"] = "DC"
            results = await self.repository.aggregate_async(self.serve_supply, pipeline)
            
        return {item['_id']: {"total_supply": item['total_supply']} for item in results}

    async def _get_demand_data(self, schedule_date: datetime, from_block: int, to_block: int) -> Dict[int, Dict]:
        """Fetches and processes demand data."""
        pipeline = [
            {"$match": {
                "scheduled_at": schedule_date,
                "time_block": {"$gte": from_block, "$lte": to_block},
                "type": "IDF"  # Filter for IDF type to avoid duplication
            }},
            {"$sort": {"updated_at": -1}},  # Sort by most recent first
            {"$group": {
                "_id": "$time_block",
                "comp_act_demand": {"$first": "$comp_act_demand"},
                "comp_udm": {"$first": "$comp_udm"}
            }}
        ]
        results = await self.repository.aggregate_async(self.serve_demand, pipeline)
        return {
            item['_id']: {
                "comp_act_demand": item['comp_act_demand'],
                "comp_udm": item['comp_udm']
            } for item in results
        }

    async def _get_trade_data(self, schedule_date: datetime, from_block: int, to_block: int) -> Dict[int, Dict]:
        """Fetches and processes trade data."""
        pipeline = [
            {"$match": {
                "scheduled_at": schedule_date,
                "time_block": {"$gte": from_block, "$lte": to_block},
                "type": {"$in": ["DAM", "RTM"]}
            }},
            {"$group": {
                "_id": "$time_block",
                "dam_trade": {"$sum": {"$cond": [{"$eq": ["$type", "DAM"]}, "$traded", 0]}},
                "rtm_trade": {"$sum": {"$cond": [{"$eq": ["$type", "RTM"]}, "$traded", 0]}}
            }}
        ]
        results = await self.repository.aggregate_async(self.serve_px_bid, pipeline)
        return {
            item['_id']: {
                "dam_trade": item['dam_trade'],
                "rtm_trade": item['rtm_trade']
            } for item in results
        }

    def _combine_snapshot_data(
        self,
        supply_data: Dict[int, Any],
        demand_data: Dict[int, Any],
        trade_data: Dict[int, Any],
        schedule_date_str: str,
        from_block: int,
        to_block: int,
    ) -> Dict[str, Any]:
        """Combines the pre-fetched data from all sources into the final response format."""
        blocks = {}
        
        for block_num in range(from_block, to_block + 1):
            supply_info = supply_data.get(block_num, {"total_supply": 0})
            demand_info = demand_data.get(block_num, {"comp_act_demand": 0, "comp_udm": 0})
            trade_info = trade_data.get(block_num, {"dam_trade": 0, "rtm_trade": 0})
            
            comp_act_demand = demand_info.get("comp_act_demand", 0)
            total_supply = supply_info.get("total_supply", 0)
            
            blocks[str(block_num)] = {
                "comp_act_demand": comp_act_demand,
                "comp_udm": demand_info.get("comp_udm", 0),
                "total_supply": total_supply,
                "gap": total_supply - comp_act_demand,
                "dam_trade": trade_info.get("dam_trade", 0),
                "rtm_trade": trade_info.get("rtm_trade", 0)
            }
            
        return {
            "schedule_date": schedule_date_str.split('T')[0],
            "from_block": from_block,
            "to_block": to_block,
            "blocks": blocks
        }

    async def get_iex_dam_rtm_snapshot(self, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return await self.repository.aggregate_async(self.serve_exchange, pipeline)

    async def get_variable_cost_snapshot(self, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return await self.repository.aggregate_async(self.serve_exchange, pipeline)

    async def get_variable_cost_snapshot_range_data(self, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return await self.repository.aggregate_async(self.serve_exchange, pipeline)

# Global instance
management_snapshot_repository = ManagementSnapshotRepository()