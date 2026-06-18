"""
New DSM (Demand Side Management) Processor
Calculates DSM rates and net DSM amounts using new_dsm_calculation.py
Aggregates exchange data from IEX, PXIL, and HPX
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta, time
from typing import Dict, List, Any, Optional, Tuple

from .base_processor import BaseProcessor
from ..core.config import settings

logger = logging.getLogger(__name__)


class NewDSMProcessor(BaseProcessor):
    """
    New DSM Processor for calculating DSM rates and net DSM amounts
    Uses new_dsm_calculation.py and aggregates exchange data from multiple exchanges
    Runs every 15 minutes to process current and future time blocks
    """
    
    def __init__(self):
        super().__init__()
        self.time_blocks_cache = {}  # Cache for time blocks
        self.current_time_block = None
        
    async def execute_cron_job(self) -> Any:
        """
        Main cron job execution method
        Called every 15 minutes to calculate DSM rates and net DSM amounts
        
        Returns:
            Dict[str, Any]: Execution result
        """
        job_type = "new_dsm_rate_calculation"
        job_name = "New DSM Rate and Net DSM Calculation"
        schedule = "*/15 * * * *"  # Default schedule
        start_time = datetime.now()
        
        # Generate and record job start to get execution_id (correlation ID)
        execution_id = await self._record_job_start(job_type, job_name, schedule, start_time)
        if not execution_id:
            logger.error("Failed to record job start for %s", job_type)
            return {
                "status": "error",
                "message": "Failed to record job start",
                "execution_time": 0.0,
                "data_type": "dsm"
            }
            
        # Set execution_id as correlation ID using base processor method
        self.set_correlation_id(execution_id)
        
        try:
            logger.info("Starting New DSM rate and net DSM amount calculation")
            
            # Step 1: Load time blocks into cache if not already loaded
            if not self.time_blocks_cache:
                await self._load_time_blocks_cache()
            
            # Step 2: Determine current time block based on current time
            current_time_block = self._get_current_time_block()
            if not current_time_block:
                logger.warning("Could not determine current time block, using fallback approach")
                current_time_block = 1
            
            logger.info(f"Current time block: {current_time_block}")
            
            # Step 3: Blocks to process (grouped by scheduled_at — one batch per date)
            blocks_to_process = self._get_blocks_to_process(current_time_block)
            grouped = self._group_blocks_by_scheduled_at(blocks_to_process)
            logger.info(
                "Processing %s time blocks across %s scheduled date(s) (batched)",
                len(blocks_to_process),
                len(grouped),
            )

            processed_count = 0
            failed_count = 0
            updated_count = 0

            for scheduled_at, time_blocks in grouped.items():
                try:
                    batch_processed, batch_failed, batch_updated = await self._process_scheduled_date_batch(
                        scheduled_at, time_blocks
                    )
                    processed_count += batch_processed
                    failed_count += batch_failed
                    updated_count += batch_updated
                except Exception as e:
                    failed_count += len(time_blocks)
                    logger.error(
                        "Batch DSM failed for %s blocks %s..%s: %s",
                        scheduled_at.date(),
                        min(time_blocks) if time_blocks else "?",
                        max(time_blocks) if time_blocks else "?",
                        e,
                        exc_info=True,
                    )

            success = processed_count > 0
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds() if start_time else 0.0
            
            result = {
                "status": "success" if success else "error",
                "message": "New DSM data processed successfully" if success else "New DSM data processing failed",
                "execution_time": execution_time,
                "data_type": "dsm",
                "correlation_id": execution_id,
                "cron_job": "new_dsm",
                "processed_count": processed_count,
                "failed_count": failed_count,
                "updated_count": updated_count,
                "blocks_total": len(blocks_to_process),
            }
            
            await self._record_job_completion(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=end_time,
                execution_id=execution_id,
                status=result["status"],
                success=success,
                result=result
            )
            
            logger.info(f"Processing summary: {processed_count} successful, {failed_count} failed out of {len(blocks_to_process)} total")
            
            return result
            
        except Exception as e:
            logger.error(f"Error in New DSM cron job execution: {str(e)}", exc_info=True)
            
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            error_result = {
                "status": "error",
                "message": f"New DSM processing error: {str(e)}",
                "execution_time": execution_time,
                "data_type": "dsm",
                "correlation_id": execution_id,
                "cron_job": "new_dsm"
            }
            
            await self._record_job_completion(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=end_time,
                execution_id=execution_id,
                status="error",
                success=False,
                result=error_result,
                error_message=str(e)
            )
            return error_result
        finally:
            # Clear correlation ID after job completion
            self.clear_correlation_id()
    
    async def _load_time_blocks_cache(self):
        """Load all time blocks from database into cache"""
        try:
            from ..core.database import get_database
            db = get_database()
            time_block_collection = db[settings.MONGODB_TIMEBLOCK_MASTER_COLLECTION]
            
            # Fetch all time blocks and create cache
            cursor = time_block_collection.find({}).sort("timeblock", 1)
            try:
                time_blocks = await cursor.to_list(length=10000)
            finally:
                cursor.close()
            
            for block in time_blocks:
                self.time_blocks_cache[block['timeblock']] = {
                    'timeblock': block['timeblock'],
                    'starttime': block['starttime'],
                    'endtime': block['endtime'],
                    'fulltime': block.get('fulltime', 0)
                }
            
            logger.info(f"Loaded {len(self.time_blocks_cache)} time blocks into cache")
            
        except Exception as e:
            logger.error(f"Error loading time blocks cache: {str(e)}")
            raise
    
    def _get_current_time_block(self) -> Optional[int]:
        """
        Determine current time block based on current time
        
        Returns:
            int: Current time block number (1-96) or None if not found
        """
        try:
            current_time = datetime.now()
            current_time_str = current_time.strftime("%H:%M")
            
            logger.debug(f"Looking for time block for current time: {current_time_str}")
            
            # Find matching time block
            for timeblock, block_info in self.time_blocks_cache.items():
                start_time = block_info['starttime']
                end_time = block_info['endtime']
                
                # Handle time comparison (considering 24-hour format)
                if self._is_time_in_range(current_time_str, start_time, end_time):
                    logger.debug(f"Found matching time block: {timeblock}")
                    return timeblock
            
            # If no exact match found, find the closest time block
            logger.warning(f"No exact time block found for current time: {current_time_str}")
            
            closest_block = None
            for timeblock in sorted(self.time_blocks_cache.keys()):
                block_info = self.time_blocks_cache[timeblock]
                start_time = block_info['starttime']
                
                try:
                    current = datetime.strptime(current_time_str, "%H:%M").time()
                    start = datetime.strptime(start_time, "%H:%M").time()
                    
                    if current >= start:
                        closest_block = timeblock
                    else:
                        break
                except Exception as e:
                    logger.debug(f"Error comparing with block {timeblock}: {str(e)}")
                    continue
            
            if closest_block:
                logger.info(f"Using closest time block: {closest_block} for current time: {current_time_str}")
                return closest_block
            
            logger.warning(f"No time block found for current time: {current_time_str}")
            return None
            
        except Exception as e:
            logger.error(f"Error determining current time block: {str(e)}")
            return None
    
    def _is_time_in_range(self, current_time: str, start_time: str, end_time: str) -> bool:
        """
        Check if current time falls within the time block range
        
        Args:
            current_time: Current time in HH:MM format
            start_time: Block start time in HH:MM format  
            end_time: Block end time in HH:MM format
            
        Returns:
            bool: True if current time is in range
        """
        try:
            from datetime import time
            
            # Handle special case of "24:00"
            if end_time == "24:00":
                end_time = "00:00"
                is_cross_midnight = True
            else:
                is_cross_midnight = False
            
            current = datetime.strptime(current_time, "%H:%M").time()
            start = datetime.strptime(start_time, "%H:%M").time()
            end = datetime.strptime(end_time, "%H:%M").time()
            
            # Handle case where end time is next day
            if is_cross_midnight or end < start:
                return current >= start or current < end
            else:
                return start <= current < end
                
        except Exception as e:
            logger.error(f"Error comparing times: {str(e)}")
            return False
    
    def _get_blocks_to_process(self, current_time_block: int) -> List[Dict[str, Any]]:
        """
        Get list of time blocks to process:
        - Day before yesterday: all blocks (1-96)
        - Yesterday: all blocks (1-96)
        - Today: blocks 1 through current_time_block (up to current time)
        
        Args:
            current_time_block: Current time block number
            
        Returns:
            List of block info dictionaries with timeblock, date
        """
        try:
            blocks_to_process = []
            today = datetime.now().date()
            # day_before_yesterday = today - timedelta(days=2)
            current_time = datetime.now().time()

            # Day before yesterday: all blocks 1-96
            # for block_num in range(1, 97):
            #     if block_num in self.time_blocks_cache:
            #         blocks_to_process.append({
            #             'timeblock': block_num,
            #             'date': day_before_yesterday,
            #             'scheduled_at': datetime.combine(day_before_yesterday, datetime.min.time())
            #         })

            # Yesterday: all blocks 80-96
            if time(0, 0) <= current_time <= time(3, 0):
                yesterday = today - timedelta(days=1)                
                for block_num in range(80, 97):
                    if block_num in self.time_blocks_cache:
                        blocks_to_process.append({
                            'timeblock': block_num,
                            'date': yesterday,
                            'scheduled_at': datetime.combine(yesterday, datetime.min.time())
                        })

            # Today: blocks 1 through current_time_block (inclusive)
            for block_num in range(1, current_time_block + 1):
                if block_num in self.time_blocks_cache:
                    blocks_to_process.append({
                        'timeblock': block_num,
                        'date': today,
                        'scheduled_at': datetime.combine(today, datetime.min.time())
                    })

            logger.info(
                f"Generated {len(blocks_to_process)} blocks to process "
                f"(Day before yesterday: 96, Yesterday: 96, Today: {current_time_block})"
            )
            return blocks_to_process

        except Exception as e:
            logger.error(f"Error generating blocks to process: {str(e)}")
            return []
    
    
    @staticmethod
    def _group_blocks_by_scheduled_at(
        blocks_to_process: List[Dict[str, Any]],
    ) -> Dict[datetime, List[int]]:
        grouped: Dict[datetime, List[int]] = defaultdict(list)
        for info in blocks_to_process:
            grouped[info["scheduled_at"]].append(int(info["timeblock"]))
        return {sa: sorted(set(tbs)) for sa, tbs in grouped.items()}

    async def _process_scheduled_date_batch(
        self,
        scheduled_at: datetime,
        time_blocks: List[int],
    ) -> Tuple[int, int, int]:
        """
        Phase-1 batch path: one demand read, one supply agg, one exchange agg per date.

        Returns:
            (processed_blocks, failed_blocks, documents_updated)
        """
        if not time_blocks:
            return 0, 0, 0

        logger.info(
            "Batch DSM for %s: blocks %s..%s (%s blocks)",
            scheduled_at.date(),
            time_blocks[0],
            time_blocks[-1],
            len(time_blocks),
        )

        demand_map = await self._fetch_demand_idf_map(scheduled_at, time_blocks)
        schedule_map = await self._fetch_schedule_map_for_blocks(scheduled_at, time_blocks)
        acp_map = await self._fetch_acp_map_for_blocks(scheduled_at, time_blocks)

        from libs.helpers.new_dsm_calculation import DSMEngine

        dsm_engine = DSMEngine()
        now = datetime.now()
        bulk_updates: List[Dict[str, Any]] = []
        processed = 0
        failed = 0

        for timeblock in time_blocks:
            demand_record = demand_map.get(timeblock)
            if not demand_record:
                failed += 1
                continue

            record_scheduled_at = demand_record.get("scheduled_at")
            if isinstance(record_scheduled_at, str):
                record_scheduled_at = datetime.fromisoformat(
                    record_scheduled_at.replace("Z", "+00:00")
                )
            if not isinstance(record_scheduled_at, datetime):
                record_scheduled_at = scheduled_at

            schedule_mw = schedule_map.get(timeblock)
            actual_mw = demand_record.get("drawal")
            frequency = demand_record.get("frequency")
            max_dam_mcp, max_rtm_mcp = acp_map.get(timeblock, (0.0, 0.0))

            dsm_rate: Optional[float] = None
            net_dsm_amount: Optional[float] = None

            if not schedule_mw or schedule_mw == 0:
                failed += 1
                continue
            if not actual_mw or actual_mw == 0:
                failed += 1
                continue
            if not frequency or frequency == 0:
                failed += 1
                continue

            try:
                summary = dsm_engine.get_dsm_summary(
                    schedule_mw=schedule_mw,
                    actual_mw=actual_mw,
                    frequency=frequency,
                    acp_dam=max_dam_mcp,
                    acp_rtm=max_rtm_mcp,
                    rr_or_contract=0,
                )
                dsm_rate = summary["dsm_rate"]
                net_dsm_amount = summary["net_dsm_amount"]
            except Exception as calc_error:
                logger.error("DSM calc failed for block %s: %s", timeblock, calc_error)
                failed += 1
                continue

            if dsm_rate is None or net_dsm_amount is None:
                failed += 1
                continue

            bulk_updates.append(
                {
                    "filter": {
                        "scheduled_at": record_scheduled_at,
                        "time_block": timeblock,
                        "type": "IDF",
                    },
                    "update": {
                        "dsm_rate": dsm_rate,
                        "net_dsm_amount": net_dsm_amount,
                        "updated_at": now,
                        "updated_flag": "U",
                    },
                }
            )
            processed += 1

        updated_docs = 0
        if bulk_updates:
            from ..repository.demand_serve_repository import demand_serve_repository

            write_result = await demand_serve_repository.bulk_update_dsm_fields(bulk_updates)
            updated_docs = write_result.get("modified_count", 0) or write_result.get("matched_count", 0)
            logger.info(
                "Batch DSM write for %s: %s updates, modified=%s",
                scheduled_at.date(),
                len(bulk_updates),
                updated_docs,
            )

        return processed, failed, updated_docs

    async def _fetch_demand_idf_map(
        self,
        scheduled_at: datetime,
        time_blocks: List[int],
    ) -> Dict[int, Dict[str, Any]]:
        """One query: IDF demand rows for the day and requested blocks."""
        from ..repository.demand_serve_repository import demand_serve_repository

        records = await demand_serve_repository.find_idf_demand_for_blocks(
            scheduled_at, time_blocks
        )
        result: Dict[int, Dict[str, Any]] = {}
        for record in records:
            tb = record.get("time_block")
            if tb is None:
                continue
            # Prefer latest updated_at if duplicates exist
            existing = result.get(tb)
            if not existing or (record.get("updated_at") or datetime.min) >= (
                existing.get("updated_at") or datetime.min
            ):
                result[int(tb)] = record
        logger.debug(
            "Demand map for %s: %s/%s blocks",
            scheduled_at.date(),
            len(result),
            len(time_blocks),
        )
        return result

    async def _fetch_schedule_map_for_blocks(
        self,
        scheduled_at: datetime,
        time_blocks: List[int],
    ) -> Dict[int, float]:
        """One aggregation: schedule_mw per time_block from supply_serve."""
        if not time_blocks:
            return {}

        try:
            from ..repository.supply_serve_repository import supply_serve_repository

            pipeline = [
                {
                    "$match": {
                        "scheduled_at": scheduled_at,
                        "time_block": {"$in": time_blocks},
                        "dc_sg": "SG",
                    }
                },
                {
                    "$lookup": {
                        "from": "plant_master",
                        "localField": "plant_id",
                        "foreignField": "plant_id",
                        "as": "plant_info",
                    }
                },
                {
                    "$unwind": {
                        "path": "$plant_info",
                        "preserveNullAndEmptyArrays": False,
                    }
                },
                {"$match": {"plant_info.c_s": "central"}},
                {
                    "$group": {
                        "_id": "$time_block",
                        "total_schedule": {"$sum": "$volume"},
                    }
                },
            ]

            rows = await supply_serve_repository.serve_collection.aggregate(
                pipeline
            ).to_list(length=100)

            schedule_map = {
                int(row["_id"]): float(row.get("total_schedule") or 0)
                for row in rows
                if row.get("_id") is not None
            }
            logger.debug(
                "Schedule map for %s: %s blocks",
                scheduled_at.date(),
                len(schedule_map),
            )
            return schedule_map
        except Exception as e:
            logger.error("Error fetching schedule map: %s", e, exc_info=True)
            return {}

    async def _fetch_acp_map_for_blocks(
        self,
        scheduled_at: datetime,
        time_blocks: List[int],
    ) -> Dict[int, Tuple[float, float]]:
        """One aggregation across exchanges; per-block fallback only when ACP is zero."""
        if not time_blocks:
            return {}

        from ..core.database import get_database

        db = get_database()
        pipeline = self._build_batch_acp_pipeline(scheduled_at, time_blocks)
        cursor = db.exchange_dsm_serve.aggregate(pipeline, allowDiskUse=True)
        rows = await cursor.to_list(length=10000)
        acp_map = self._acp_from_aggregate_rows(rows)

        for timeblock in time_blocks:
            dam, rtm = acp_map.get(timeblock, (0.0, 0.0))
            if dam == 0 or rtm == 0:
                acp_map[timeblock] = await self._fetch_cerc_weighted_acp(timeblock, scheduled_at)

        logger.debug(
            "ACP map for %s: %s blocks",
            scheduled_at.date(),
            len(acp_map),
        )
        return acp_map

    def _build_batch_acp_pipeline(
        self,
        scheduled_at: datetime,
        time_blocks: List[int],
    ) -> List[Dict[str, Any]]:
        """CERC-weighted ACP pipeline grouped by time_block and market_type."""
        block_match = {"scheduled_at": scheduled_at, "time_block": {"$in": time_blocks}}

        return [
            {"$match": block_match},
            {
                "$project": {
                    "time_block": 1,
                    "market_type": 1,
                    "value": {
                        "$multiply": [
                            "$price",
                            {
                                "$add": [
                                    "$total_buy_cleared_vol",
                                    "$total_sell_cleared_vol",
                                ]
                            },
                        ]
                    },
                    "volume": {
                        "$add": [
                            "$total_buy_cleared_vol",
                            "$total_sell_cleared_vol",
                        ]
                    },
                }
            },
            {
                "$unionWith": {
                    "coll": "exchange_trade_serve",
                    "pipeline": [
                        {"$match": block_match},
                        {
                            "$project": {
                                "time_block": 1,
                                "market_type": 1,
                                "value": {
                                    "$multiply": [
                                        "$Price",
                                        {
                                            "$add": [
                                                "$Total_Buy_Cleared_Vol",
                                                "$Total_Sell_Cleared_Vol",
                                            ]
                                        },
                                    ]
                                },
                                "volume": {
                                    "$add": [
                                        "$Total_Buy_Cleared_Vol",
                                        "$Total_Sell_Cleared_Vol",
                                    ]
                                },
                            }
                        },
                    ],
                }
            },
            {
                "$unionWith": {
                    "coll": "bid_trade_serve",
                    "pipeline": [
                        {"$match": block_match},
                        {
                            "$group": {
                                "_id": {
                                    "time_block": "$time_block",
                                    "market_type": "$type",
                                },
                                "value": {
                                    "$sum": {
                                        "$multiply": [
                                            "$value",
                                            {"$abs": "$traded"},
                                        ]
                                    }
                                },
                                "volume": {"$sum": {"$abs": "$traded"}},
                            }
                        },
                        {
                            "$project": {
                                "time_block": "$_id.time_block",
                                "market_type": "$_id.market_type",
                                "value": 1,
                                "volume": 1,
                            }
                        },
                    ],
                }
            },
            {
                "$group": {
                    "_id": {
                        "time_block": "$time_block",
                        "market_type": "$market_type",
                    },
                    "total_value": {"$sum": "$value"},
                    "total_volume": {"$sum": "$volume"},
                }
            },
            {
                "$project": {
                    "time_block": "$_id.time_block",
                    "market_type": "$_id.market_type",
                    "acp": {
                        "$cond": [
                            {"$gt": ["$total_volume", 0]},
                            {"$divide": ["$total_value", "$total_volume"]},
                            0,
                        ]
                    },
                    "total_volume": 1,
                }
            },
        ]

    @staticmethod
    def _acp_from_aggregate_rows(
        rows: List[Dict[str, Any]],
    ) -> Dict[int, Tuple[float, float]]:
        """Combine DAM/GDAM/HPDAM and RTM per time_block from aggregation rows."""
        idam_num: Dict[int, float] = defaultdict(float)
        idam_den: Dict[int, float] = defaultdict(float)
        rtm_num: Dict[int, float] = defaultdict(float)
        rtm_den: Dict[int, float] = defaultdict(float)

        for row in rows:
            tb = row.get("time_block")
            if tb is None:
                continue
            tb = int(tb)
            mt = row.get("market_type")
            acp_val = row.get("acp", 0) or 0
            vol = row.get("total_volume", 0) or 0
            if mt in ["DAM", "GDAM", "HPDAM"]:
                idam_num[tb] += acp_val * vol
                idam_den[tb] += vol
            elif mt == "RTM":
                rtm_num[tb] += acp_val * vol
                rtm_den[tb] += vol

        all_blocks = set(idam_num) | set(rtm_num) | set(idam_den) | set(rtm_den)
        result: Dict[int, Tuple[float, float]] = {}
        for tb in all_blocks:
            idam = idam_num[tb] / idam_den[tb] if idam_den[tb] > 0 else 0.0
            rtm = rtm_num[tb] / rtm_den[tb] if rtm_den[tb] > 0 else 0.0
            result[tb] = (float(idam), float(rtm))
        return result

    async def _fallback_previous_acp(
        self,
        timeblock: int,
        scheduled_at: datetime
    ) -> tuple[float, float]:
        """Look back to the most recent earlier date and recompute ACP.

        The documents in ``exchange_dsm_serve`` do not store precomputed ACP
        values, so the fallback simply re‑invokes
        ``_fetch_cerc_weighted_acp`` on the previous timestamp.  If that call
        again returns zeros, the search continues recursively until a non‑zero
        result or the collection is exhausted.
        """

        from ..core.database import get_database
        db = get_database()

        previous = await db.exchange_dsm_serve.find_one(
            {
                "time_block": timeblock,
                "scheduled_at": {"$lt": scheduled_at}
            },
            sort=[("scheduled_at", -1)]
        )

        if previous:
            prev_date = previous.get("scheduled_at")
            if prev_date:
                # recursive call will itself fallback further if needed
                return await self._fetch_cerc_weighted_acp(timeblock, prev_date)

        return 0.0, 0.0
    
    async def _fetch_cerc_weighted_acp(
            self,
            timeblock: int,
            scheduled_at: datetime
        ) -> tuple[float, float]:
            """
            Returns:
                idam_acp_rs_mwh,
                rtm_acp_rs_mwh

            Fully CERC compliant:
            Weighted across ALL exchanges combined.
            """

            from ..core.database import get_database
            db = get_database()

            pipeline = [

                # ================= PXIL =================
                {
                    "$match": {
                        "scheduled_at": scheduled_at,
                        "time_block": timeblock
                    }
                },
                {
                    "$project": {
                        "market_type": 1,
                        "value": {
                            "$multiply": [
                                "$price",
                                {
                                    "$add": [
                                        "$total_buy_cleared_vol",
                                        "$total_sell_cleared_vol"
                                    ]
                                }
                            ]
                        },
                        "volume": {
                            "$add": [
                                "$total_buy_cleared_vol",
                                "$total_sell_cleared_vol"
                            ]
                        }
                    }
                },

                # ================= HPX =================
                {
                    "$unionWith": {
                        "coll": "exchange_trade_serve",
                        "pipeline": [
                            {
                                "$match": {
                                    "scheduled_at": scheduled_at,
                                    "time_block": timeblock
                                }
                            },
                            {
                                "$project": {
                                    "market_type": 1,
                                    "value": {
                                        "$multiply": [
                                            "$Price",
                                            {
                                                "$add": [
                                                    "$Total_Buy_Cleared_Vol",
                                                    "$Total_Sell_Cleared_Vol"
                                                ]
                                            }
                                        ]
                                    },
                                    "volume": {
                                        "$add": [
                                            "$Total_Buy_Cleared_Vol",
                                            "$Total_Sell_Cleared_Vol"
                                        ]
                                    }
                                }
                            }
                        ]
                    }
                },

                # ================= IEX =================
                {
                    "$unionWith": {
                        "coll": "bid_trade_serve",
                        "pipeline": [
                            {
                                "$match": {
                                    "scheduled_at": scheduled_at,
                                    "time_block": timeblock
                                }
                            },
                            {
                                "$group": {
                                    "_id": "$type",
                                    "value": {
                                        "$sum": {
                                            "$multiply": [
                                                "$value",
                                                {"$abs": "$traded"}
                                            ]
                                        }
                                    },
                                    "volume": {
                                        "$sum": {"$abs": "$traded"}
                                    }
                                }
                            },
                            {
                                "$project": {
                                    "market_type": "$_id",
                                    "value": 1,
                                    "volume": 1
                                }
                            }
                        ]
                    }
                },

                # ================= COMBINE ALL EXCHANGES =================
                {
                    "$group": {
                        "_id": "$market_type",
                        "total_value": {"$sum": "$value"},
                        "total_volume": {"$sum": "$volume"}
                    }
                },

                # ================= FINAL ACP =================
                {
                    "$project": {
                        "market_type": "$_id",
                        "acp": {
                            "$cond": [
                                {"$gt": ["$total_volume", 0]},
                                {"$divide": ["$total_value", "$total_volume"]},
                                0
                            ]
                        },
                        "total_volume": 1
                    }
                }
            ]

            cursor = db.exchange_dsm_serve.aggregate(pipeline, allowDiskUse=True)
            results = await cursor.to_list(length=10000)

            # combine results: compute weighted average across IDAM subtypes and RTM
            idam_num = 0.0
            idam_den = 0.0
            rtm_num = 0.0
            rtm_den = 0.0

            for row in results:
                mt = row.get("market_type")
                acp_val = row.get("acp", 0) or 0
                vol = row.get("total_volume", 0) or 0
                if mt in ["DAM", "GDAM", "HPDAM"]:
                    idam_num += acp_val * vol
                    idam_den += vol
                elif mt == "RTM":
                    rtm_num += acp_val * vol
                    rtm_den += vol

            idam_acp = idam_num / idam_den if idam_den > 0 else 0.0
            rtm_acp = rtm_num / rtm_den if rtm_den > 0 else 0.0

            # Fallback if either is missing
            if idam_acp == 0 or rtm_acp == 0:
                return await self._fallback_previous_acp(timeblock, scheduled_at)
            logger.debug(
                "ACP fallback single block %s @ %s: IDAM=%s RTM=%s",
                timeblock,
                scheduled_at,
                idam_acp,
                rtm_acp,
            )
            return float(idam_acp), float(rtm_acp)

    def process_raw_data_to_serve(self, raw_data: List[Dict[str, Any]], metadata: Dict[str, Any], provider_config: Dict[str, Any]) -> bool:
        """
        Implementation of abstract method from BaseProcessor
        Not used for DSM processor as it doesn't process raw data
        """
        logger.info("New DSM processor does not process raw data - this method is not applicable")
        return True


if __name__ == "__main__":
    import asyncio

    processor = NewDSMProcessor()
    asyncio.run(processor.execute_cron_job())
