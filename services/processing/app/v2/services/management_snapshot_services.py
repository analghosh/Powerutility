"""
Management Snapshot V2 Service
Enhanced management snapshot service with business logic and repository integration
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, date, timedelta
from fastapi import HTTPException, status

from ..repository.management_snapshot_repository import management_snapshot_repository
from libs.helpers.timeblock_helper import ist_now, block_from_hhmm
from libs.utils.date_filters import validate_schedule_date, normalize_schedule_date

logger = logging.getLogger(__name__)


class ManagementSnapshotService:
    """
    Service layer for Management Snapshot V2 operations.
    Handles business logic and data processing.
    """

    def __init__(self):
        self.management_snapshot_repository = management_snapshot_repository

    @classmethod
    def _today_ist_str(cls) -> str:
        return datetime.now().date().isoformat()

    @classmethod
    def _current_block_ist(cls) -> int:
        now = datetime.now().time()
        n = (now.hour * 4) + (now.minute // 15) + 1
        return 1 if n < 1 else 96 if n > 96 else n

    @staticmethod
    def _clamp_block(n: Optional[int]) -> Optional[int]:
        if n is None:
            return None
        n = int(n)
        return 1 if n < 1 else 96 if n > 96 else n

    async def get_demand_supply_snapshot(
        self,
        schedule_date: Optional[str] = None,
        from_block: Optional[int] = None,
        to_block: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Get demand-supply snapshot with business logic processing.

        Args:
            schedule_date: Optional date filter in YYYY-MM-DD format
            from_block: Start time block (1-96)
            to_block: End time block (1-96)

        Returns:
            Dict containing demand-supply snapshot data
        """
        try:
            # Normalize and validate the schedule date
            original_date = schedule_date
            schedule_date = normalize_schedule_date(schedule_date)

            logger.info(f"[ManagementSnapshotService] Processing demand-supply snapshot - Original: {original_date}, Normalized: {schedule_date}, from_block: {from_block}, to_block: {to_block}")

            # Validate date format
            if not validate_schedule_date(schedule_date):
                logger.error(f"[ManagementSnapshotService] Invalid date format: {schedule_date}")
                return {}

            # Validate block parameters
            if from_block is not None and not (1 <= from_block <= 96):
                return {}

            if to_block is not None and not (1 <= to_block <= 96):
                return {}

            if from_block is not None and to_block is not None and from_block > to_block:
                return {}

            # Set defaults
            from_block = from_block or 1
            to_block = to_block or 96

            # Get snapshot data from repository
            snapshot_data = await self.management_snapshot_repository.get_demand_supply_snapshot(
                schedule_date_str=schedule_date,
                from_block=from_block,
                to_block=to_block
            )

            # Process and enhance the data
            processed_data = self._process_demand_supply_data(
                snapshot_data=snapshot_data,
                schedule_date=schedule_date,
                from_block=from_block,
                to_block=to_block
            )

            logger.info(f"[ManagementSnapshotService] Successfully processed demand-supply snapshot")

            return processed_data

        except Exception as e:
            logger.error(f"[ManagementSnapshotService] Error processing demand-supply snapshot - schedule_date: {schedule_date}, from_block: {from_block}, to_block: {to_block}, error: {str(e)}")
            raise

    def _process_demand_supply_data(
        self,
        snapshot_data: Dict[str, Any],
        schedule_date: str,
        from_block: int,
        to_block: int,
    ) -> Dict[str, Any]:
        """
        Process and enhance demand-supply snapshot data with business logic.

        Args:
            snapshot_data: Raw snapshot data from repository
            schedule_date: Schedule date
            from_block: Start time block
            to_block: End time block

        Returns:
            Processed demand-supply snapshot data
        """
        try:
            logger.info(
                f"[ManagementSnapshotService] Processing demand-supply data - "
                f"schedule_date: {schedule_date}, from_block: {from_block}, to_block: {to_block}"
            )

            # Create the response structure
            processed_data = {
                "schedule_date": schedule_date,
                "from_block": from_block,
                "to_block": to_block,
                "blocks": []
            }

            # Process each block in the range
            for block_num in range(from_block, to_block + 1):
                block_data = self._create_block_data(
                    block_num=block_num,
                    snapshot_data=snapshot_data,
                )
                processed_data["blocks"].append(block_data)

            # Add summary statistics
            processed_data["summary"] = self._calculate_snapshot_summary(
                blocks=processed_data["blocks"],
            )

            logger.info(
                f"[ManagementSnapshotService] Successfully processed demand-supply data"
            )

            return processed_data

        except Exception as e:
            logger.error(
                f"[ManagementSnapshotService] Error processing demand-supply data - "
                f"error: {str(e)}"
            )
            raise

    def _create_block_data(
        self,
        block_num: int,
        snapshot_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Create data for a single time block."""
        try:
            # Find data for this specific block
            block_data = snapshot_data.get("blocks", {}).get(str(block_num), {})

            return {
                "time_block": block_num,
                "comp_act_demand": block_data.get("comp_act_demand", 0.0),
                "comp_udm": block_data.get("comp_udm", 0.0),
                "total_supply": block_data.get("total_supply", 0.0),
                "gap": block_data.get("gap", 0.0),
                "dam_trade": block_data.get("dam_trade", 0.0),
                "rtm_trade": block_data.get("rtm_trade", 0.0)
            }
        except Exception as e:
            logger.error(
                f"[ManagementSnapshotService] Error creating block data for block {block_num} - "
                f"error: {str(e)}"
            )
            # Return zero-filled data for this block
            return {
                "time_block": block_num,
                "comp_act_demand": 0.0,
                "comp_udm": 0.0,
                "total_supply": 0.0,
                "gap": 0.0,
                "dam_trade": 0.0,
                "rtm_trade": 0.0
            }

    def _calculate_snapshot_summary(
        self,
        blocks: list,
    ) -> Dict[str, Any]:
        """Calculate summary statistics for the snapshot."""
        try:
            if not blocks:
                return {}

            total_demand = sum(block.get("comp_act_demand", 0) for block in blocks)
            total_supply = sum(block.get("total_supply", 0) for block in blocks)
            total_dam_trade = sum(block.get("dam_trade", 0) for block in blocks)
            total_rtm_trade = sum(block.get("rtm_trade", 0) for block in blocks)

            return {
                "total_demand": total_demand,
                "total_supply": total_supply,
                "total_gap": total_demand - total_supply,
                "total_dam_trade": total_dam_trade,
                "total_rtm_trade": total_rtm_trade,
                "total_trades": total_dam_trade + total_rtm_trade,
                "block_count": len(blocks)
            }
        except Exception as e:
            logger.error(
                f"[ManagementSnapshotService] Error calculating snapshot summary - "
                f"error: {str(e)}"
            )
            return {}

    async def iex_dam_rtm_snapshot(
            self,
            schedule_date: Optional[str] = None,
            from_block: Optional[int] = None,
            to_block: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Build the snapshot for DAM/RTM MCV/MCP data for a single date and time_block window.
        """
        date_str = normalize_schedule_date(schedule_date or self._today_ist_str())
        b_from = self._clamp_block(from_block) or 1
        b_to = self._clamp_block(to_block) or 96
        if b_from > b_to:
            b_from, b_to = b_to, b_from

        today_str = self._today_ist_str()
        cur_blk = self._current_block_ist()
        is_today = (date_str == today_str)

        # Parse date string to datetime for MongoDB query
        query_date = datetime.fromisoformat(date_str)

        # Simple pipeline: match -> group -> pivot
        pipeline: List[Dict[str, Any]] = [
            # Match by scheduled_at, market_type, and time_block range
            {
                "$match": {
                    "scheduled_at": query_date,
                    "market_type": {"$in": ["DAM", "RTM"]},
                    "time_block": {"$gte": b_from, "$lte": b_to},
                }
            },
            # Project only required fields
            {
                "$project": {
                    "_id": 0,
                    "time_block": 1,
                    "market_type": 1,
                    "mcv": 1,
                    "mcp": 1,
                }
            },
            # Pivot: group by time_block and create dam_*/rtm_* fields
            {
                "$group": {
                    "_id": "$time_block",
                    "dam_mcv": {"$max": {"$cond": [{"$eq": ["$market_type", "DAM"]}, "$mcv", None]}},
                    "dam_mcp": {"$max": {"$cond": [{"$eq": ["$market_type", "DAM"]}, "$mcp", None]}},
                    "rtm_mcv": {"$max": {"$cond": [{"$eq": ["$market_type", "RTM"]}, "$mcv", None]}},
                    "rtm_mcp": {"$max": {"$cond": [{"$eq": ["$market_type", "RTM"]}, "$mcp", None]}},
                }
            },
            {"$sort": {"_id": 1}},
        ]

        rows = await self.management_snapshot_repository.get_iex_dam_rtm_snapshot(pipeline)

        # Build lookup map
        by_block = {r["_id"]: r for r in rows}
        data: List[Dict[str, Any]] = []

        # Gap-fill and apply RTM future-block masking
        for tb in range(b_from, b_to + 1):
            rec = by_block.get(tb, {})
            hide_rtm = is_today and tb > cur_blk

            dam_mcv = rec.get("dam_mcv")
            dam_mcp = rec.get("dam_mcp")
            rtm_mcv = None if hide_rtm else rec.get("rtm_mcv")
            rtm_mcp = None if hide_rtm else rec.get("rtm_mcp")

            data.append({
                "time_block": tb,
                "dam_mcv": round(dam_mcv, 2) if dam_mcv is not None else None,
                "dam_mcp": round(dam_mcp, 2) if dam_mcp is not None else None,
                "rtm_mcv": round(rtm_mcv, 2) if rtm_mcv is not None else None,
                "rtm_mcp": round(rtm_mcp, 2) if rtm_mcp is not None else None,
            })

        return {"schedule_date": date_str, "data": data}

    async def variable_cost_snapshot(
            self,
            schedule_date: Optional[str],
            from_block: Optional[int] = None,
            to_block: Optional[int] = None,
            exchange: Optional[str] = None,
    ) -> Dict[str, Any]:  # Changed return type hint for clarity
        # Handle None schedule_date by defaulting to today
        if schedule_date is None:
            schedule_date = datetime.now().date().isoformat()
        sDate = datetime.fromisoformat(schedule_date).date()
        b_from = self._clamp_block(from_block) or 1
        b_to = self._clamp_block(to_block) or 96

        # --- PIPELINE IS MODIFIED HERE ---
        # The final $group and $project stages have been removed.
        pipeline: List[Dict[str, Any]] = [
            {
                "$addFields": {
                    "_sdate": {
                        "$let": {
                            "vars": {"t": {"$type": "$scheduled_at"}},
                            "in": {
                                "$trim": {
                                    "input": {
                                        "$cond": [
                                            {"$eq": ["$$t", "date"]},
                                            {
                                                "$dateToString": {
                                                    "date": "$scheduled_at",
                                                    "format": "%Y-%m-%d",
                                                    "timezone": "Asia/Kolkata",
                                                }
                                            },
                                            {"$toString": "$scheduled_at"},
                                        ]
                                    }
                                }
                            }
                        }
                    },
                    "_market": {"$toUpper": {"$ifNull": ["$market_type", ""]}},
                }
            },
            {
                "$match": {
                    "$expr": {
                        "$and": [
                            {"$eq": ["$_sdate", sDate.isoformat()]},
                            {"$in": ["$_market", ["DAM", "RTM"]]},
                            {"$gte": ["$time_block", b_from]},
                            {"$lte": ["$time_block", b_to]},
                            *([{"$eq": ["$px_name", exchange]}] if exchange is not None else []),
                        ]
                    }
                }
            },
            {
                "$lookup": {
                    "from": self.management_snapshot_repository.serve_px_bid,
                    "let": {"sd": "$_sdate", "tb": "$time_block", "mk": "$_market", "ex": "$px_name"},
                    "pipeline": [
                        {
                            "$addFields": {
                                "_sdate": {
                                    "$let": {
                                        "vars": {"t": {"$type": "$scheduled_at"}},
                                        "in": {
                                            "$trim": {
                                                "input": {
                                                    "$cond": [
                                                        {"$eq": ["$$t", "date"]},
                                                        {
                                                            "$dateToString": {
                                                                "date": "$scheduled_at",
                                                                "format": "%Y-%m-%d",
                                                                "timezone": "Asia/Kolkata",
                                                            }
                                                        },
                                                        {"$toString": "$scheduled_at"},
                                                    ]
                                                }
                                            }
                                        }
                                    }
                                },
                                "_market": {"$toUpper": {"$ifNull": ["$type", ""]}},
                            }
                        },
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$_sdate", "$$sd"]},
                                        {"$eq": ["$time_block", "$$tb"]},
                                        {"$eq": ["$_market", "$$mk"]},
                                    ]
                                }
                            }
                        },
                        {"$group": {"_id": None, "traded": {"$sum": "$traded"}}},
                    ],
                    "as": "trade",
                }
            },
            {
                "$addFields": {
                    "Total": {
                        "buy": {"$toDouble": {"$ifNull": ["$buy_bid", 0]}},
                        "sell": {"$toDouble": {"$ifNull": ["$sell_bid", 0]}},
                        "mcp": {"$toDouble": {"$ifNull": ["$mcp", None]}},
                        "mcv": {"$toDouble": {"$ifNull": ["$mcv", None]}},
                    },
                    "traded": {"$ifNull": [{"$first": "$trade.traded"}, 0]},
                }
            },
            {
                "$addFields": {
                    "Ours": {
                        "buy": {"$cond": [{"$gt": ["$traded", 0]}, {"$toDouble": "$traded"}, 0]},
                        "sell": {"$cond": [{"$lt": ["$traded", 0]}, {"$abs": {"$toDouble": "$traded"}}, 0]},
                        "mcp": "$Total.mcp",
                        "mcv": {"$toDouble": "$traded"},
                    }
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "schedule_date": "$_sdate",
                    "time_block": 1,
                    "market": "$_market",
                    "kv": {"k": "$_market", "v": {"Total": "$Total", "Ours": "$Ours"}},
                }
            },
            {
                "$group": {
                    "_id": {"schedule_date": "$schedule_date", "time_block": "$time_block"},
                    "markets": {"$push": "$kv"},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "schedule_date": "$_id.schedule_date",
                    "time_block": "$_id.time_block",
                    "byMarket": {"$arrayToObject": "$markets"},
                }
            },
            {
                "$project": {
                    "schedule_date": 1,
                    "time_block": 1,
                    "DAM": "$byMarket.DAM",
                    "RTM": "$byMarket.RTM",
                }
            },
            {"$sort": {"time_block": 1}},
        ]

        rows = await self.management_snapshot_repository.get_variable_cost_snapshot(pipeline)

        # --- POST-PROCESSING CODE NOW WORKS AS INTENDED ---
        # It expects a flat list of blocks, which the simplified pipeline now provides.
        data: List[Dict[str, Any]] = []
        empty_pair = {"Total": None, "Ours": None}
        by_block = {r["time_block"]: r for r in rows}

        for tb in range(b_from, b_to + 1):
            r = by_block.get(tb)
            DAM = (r.get("DAM") if r else None) or empty_pair
            RTM = (r.get("RTM") if r else None) or empty_pair

            def _round_pair(pair):
                if not pair or not isinstance(pair, dict):
                    return pair
                return {
                    k: (
                        round(v, 2) if isinstance(v, (int, float)) and v is not None else v
                    )
                    for k, v in pair.items()
                }

            data.append(
                {
                    "time_block": tb,
                    "DAM": {"Total": _round_pair(DAM.get("Total")), "Ours": _round_pair(DAM.get("Ours"))},
                    "RTM": {"Total": _round_pair(RTM.get("Total")), "Ours": _round_pair(RTM.get("Ours"))},
                }
            )

        return {"schedule_date": sDate.isoformat(), "data": data}
    async def variable_cost_snapshot_range(
            self,
            date_from: str,
            date_to: str,
            from_block: Optional[int] = None,
            to_block: Optional[int] = None,
            exchange: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        d_from = datetime.fromisoformat(date_from).date()
        d_to = datetime.fromisoformat(date_to).date()

        if d_from > d_to:
            d_from, d_to = d_to, d_from

        b_from = self._clamp_block(from_block) or 1
        b_to = self._clamp_block(to_block) or 96

        pipeline: List[Dict[str, Any]] = [
            {
                "$addFields": {
                    "_sdate": {
                        "$let": {
                            "vars": {"t": {"$type": "$scheduled_at"}},
                            "in": {
                                "$trim": {
                                    "input": {
                                        "$cond": [
                                            {"$eq": ["$$t", "date"]},
                                            {
                                                "$dateToString": {
                                                    "date": "$scheduled_at",
                                                    "format": "%Y-%m-%d",
                                                    "timezone": "Asia/Kolkata",
                                                }
                                            },
                                            {"$toString": "$scheduled_at"},
                                        ]
                                    }
                                }
                            }
                        }
                    },
                    "_market": {"$toUpper": {"$ifNull": ["$market_type", ""]}},
                }
            },
            {
                "$match": {
                    "$expr": {
                        "$and": [
                            {"$gte": ["$_sdate", d_from.isoformat()]},
                            {"$lte": ["$_sdate", d_to.isoformat()]},
                            {"$in": ["$_market", ["DAM", "RTM"]]},
                            {"$gte": ["$time_block", b_from]},
                            {"$lte": ["$time_block", b_to]},
                            *([{"$eq": ["$px_name", exchange]}] if exchange is not None else []),
                        ]
                    }
                }
            },
            {
                "$lookup": {
                    "from": self.management_snapshot_repository.serve_px_bid,
                    "let": {"sd": "$_sdate", "tb": "$time_block", "mk": "$_market", "ex": "$px_name"},
                    "pipeline": [
                        {
                            "$addFields": {
                                "_sdate": {
                                    "$let": {
                                        "vars": {"t": {"$type": "$scheduled_at"}},
                                        "in": {
                                            "$trim": {
                                                "input": {
                                                    "$cond": [
                                                        {"$eq": ["$$t", "date"]},
                                                        {
                                                            "$dateToString": {
                                                                "date": "$scheduled_at",
                                                                "format": "%Y-%m-%d",
                                                                "timezone": "Asia/Kolkata",
                                                            }
                                                        },
                                                        {"$toString": "$scheduled_at"},
                                                    ]
                                                }
                                            }
                                        }
                                    }
                                },
                                "_market": {"$toUpper": {"$ifNull": ["$type", ""]}},
                            }
                        },
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$_sdate", "$$sd"]},
                                        {"$eq": ["$time_block", "$$tb"]},
                                        {"$eq": ["$_market", "$$mk"]},
                                    ]
                                }
                            }
                        },
                        {"$group": {"_id": None, "traded": {"$sum": "$traded"}}},
                    ],
                    "as": "trade",
                }
            },
            {
                "$addFields": {
                    "Total": {
                        "buy": {"$toDouble": {"$ifNull": ["$buy_bid", 0]}},
                        "sell": {"$toDouble": {"$ifNull": ["$sell_bid", 0]}},
                        "mcp": {"$toDouble": {"$ifNull": ["$mcp", None]}},
                        "mcv": {"$toDouble": {"$ifNull": ["$mcv", None]}},
                    },
                    "traded": {"$ifNull": [{"$first": "$trade.traded"}, 0]},
                }
            },
            {
                "$addFields": {
                    "Ours": {
                        "buy": {"$cond": [{"$gt": ["$traded", 0]}, {"$toDouble": "$traded"}, 0]},
                        "sell": {"$cond": [{"$lt": ["$traded", 0]}, {"$abs": {"$toDouble": "$traded"}}, 0]},
                        "mcp": "$Total.mcp",
                        "mcv": {"$toDouble": "$traded"},
                    }
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "schedule_date": "$_sdate",  # Project the date
                    "time_block": 1,
                    "market": "$_market",
                    "kv": {"k": "$_market", "v": {"Total": "$Total", "Ours": "$Ours"}},
                }
            },
            {
                "$group": {
                    "_id": {"schedule_date": "$schedule_date", "time_block": "$time_block"},
                    # Group by date and time_block
                    "markets": {"$push": "$kv"},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "schedule_date": "$_id.schedule_date",
                    "time_block": "$_id.time_block",
                    "byMarket": {"$arrayToObject": "$markets"},
                }
            },
            {
                "$project": {
                    "schedule_date": 1,
                    "time_block": 1,
                    "DAM": "$byMarket.DAM",
                    "RTM": "$byMarket.RTM",
                }
            },
            {"$sort": {"schedule_date": 1, "time_block": 1}},  # Sort by date then time_block
            {
                "$group": {
                    "_id": "$schedule_date",
                    "data": {"$push": {"time_block": "$time_block", "DAM": "$DAM", "RTM": "$RTM"}}
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "schedule_date": "$_id",
                    "data": 1
                }
            }
        ]

        rows = await self.management_snapshot_repository.get_variable_cost_snapshot_range_data(pipeline)

        # Post-process to fill missing time blocks and round values
        results: List[Dict[str, Any]] = []
        current_date = d_from
        while current_date <= d_to:
            date_str = current_date.isoformat()
            daily_data = next((item for item in rows if item["schedule_date"] == date_str), None)

            filled_data: List[Dict[str, Any]] = []
            empty_pair = {"Total": None, "Ours": None}

            for tb in range(b_from, b_to + 1):
                rec = None
                if daily_data:
                    rec = next((item for item in daily_data["data"] if item["time_block"] == tb), None)

                DAM = (rec.get("DAM") if rec else None) or empty_pair
                RTM = (rec.get("RTM") if rec else None) or empty_pair

                def _round_pair(pair):
                    if not pair or not isinstance(pair, dict):
                        return pair
                    return {k: (round(v, 2) if isinstance(v, (int, float)) and v is not None else v) for k, v in
                            pair.items()}

                filled_data.append(
                    {
                        "time_block": tb,
                        "DAM": {"Total": _round_pair(DAM.get("Total")), "Ours": _round_pair(DAM.get("Ours"))},
                        "RTM": {"Total": _round_pair(RTM.get("Total")), "Ours": _round_pair(RTM.get("Ours"))},
                    }
                )
            results.append({"schedule_date": date_str, "data": filled_data})
            current_date += timedelta(days=1)

        return results

# Global instance
management_snapshot_service = ManagementSnapshotService()
