"""
New DSM (Demand Side Management) Processor
Calculates DSM rates and net DSM amounts using new_dsm_calculation.py
Aggregates exchange data from IEX, PXIL, and HPX
"""

import logging
from datetime import datetime, timedelta,time
from typing import Dict, List, Any, Optional
from pymongo import MongoClient
from bson import ObjectId

from .base_processor import BaseProcessor
from ..core.config import settings
from libs.core.job_entities import JobDetail

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
            
            # Step 3: Calculate time blocks to process (current + future blocks)
            blocks_to_process = self._get_blocks_to_process(current_time_block)
            logger.info(f"Processing {len(blocks_to_process)} time blocks")
            
            # Step 4: Process each time block
            processed_count = 0
            failed_count = 0
            
            logger.info(f"Starting to process {len(blocks_to_process)} time blocks...")
            
            for i, block_info in enumerate(blocks_to_process):
                timeblock = block_info['timeblock']
                scheduled_at: datetime = block_info['scheduled_at']
                date = block_info['date']

                logger.debug(f"Processing time block {i+1}/{len(blocks_to_process)}: {timeblock} for date {date}")

                try:
                    if await self._process_time_block(scheduled_at, timeblock):
                        processed_count += 1
                        logger.debug(f"✅ Successfully processed time block {timeblock} for date {date}")
                    else:
                        failed_count += 1
                        logger.warning(f"❌ Failed to process time block {timeblock} for date {date}")
                except Exception as e:
                    failed_count += 1
                    logger.error(f"💥 Exception processing time block {timeblock} for date {date}: {str(e)}")
                    continue
                    
            success = processed_count > 0
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds() if start_time else 0.0
            
            result = {
                "status": "success" if success else "error",
                "message": "New DSM data processed successfully" if success else "New DSM data processing failed",
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
                status=result["status"],
                success=success,
                result=result
            )
            
            logger.info(f"Processing summary: {processed_count} successful, {failed_count} failed out of {len(blocks_to_process)} total")
            
            return processed_count > 0
            
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
            return False
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
                time_blocks = await cursor.to_list(10000)
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
    
    
    async def _process_time_block(
            self,
            scheduled_at: datetime,
            timeblock: int
        ) -> bool:
            """Process a single time block and return True on success.

            Args:
                scheduled_at: date for which to fetch ACP/demand data
                timeblock: time block number

            Returns:
                bool: whether any demand records were updated
            """

            logger.info(f"Processing DSM for block {timeblock} at {scheduled_at}")

            # 1️ Get weighted ACP once (CERC‑compliant pipeline includes fallback)
            final_wt_acp_dam, final_wt_acp_rtm = \
                await self._fetch_cerc_weighted_acp(
                    timeblock,
                    scheduled_at
                )

            # 2️⃣ Get demand records
            demand_data = await self._fetch_demand_serve_data(
                timeblock,
                scheduled_at
            )

            if not demand_data:
                logger.info("No demand data found")
                return False

            # 3️⃣ Calculate schedule from supply collection
            schedule_mw = await self._calculate_schedule_from_supply(
                scheduled_at,
                timeblock
            )

            # 4️⃣ Run DSM update
            updated = await self._batch_update_demand_records_with_dsm(
                demand_data,
                final_wt_acp_dam,
                final_wt_acp_rtm,
                timeblock,
                scheduled_at
            )

            return updated > 0
    
    

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
            results = await cursor.to_list(10000)

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
            print("========================================================")
            print(f"Fetched CERC‑compliant weighted ACPs for timeblock {timeblock} at {scheduled_at}: IDAM={idam_acp}, RTM={rtm_acp}")
            return float(idam_acp), float(rtm_acp)
            
    async def _fetch_demand_serve_data(self, timeblock: int, scheduled_at: datetime) -> List[Dict[str, Any]]:
        """
        Fetch demand serve data for the specific date and time block.
        Only records with type "IDF" are included; type "DAF" is excluded.
        
        Args:
            timeblock: Time block number
            scheduled_at: Scheduled date
            
        Returns:
            List of demand serve data records (type IDF only)
        """
        try:
            from ..repository.demand_serve_repository import demand_serve_repository
            
            # Get records for the specific date
            start_of_day = datetime.combine(scheduled_at.date(), datetime.min.time())
            end_of_day = start_of_day + timedelta(days=1)
            
            logger.debug(f"Fetching demand data for specific date: {start_of_day} to {end_of_day}")
            demand_data = await demand_serve_repository.find_demand_serve_by_date_range(start_of_day, end_of_day)
            logger.debug(f"Found {len(demand_data)} serve records for date: {scheduled_at.date()}")
            
            # Filter for time blocks >= current timeblock
            filtered_data = []
            
            for record in demand_data:
                record_timeblock = record.get('time_block')
                record_scheduled_at = record.get('scheduled_at')
                record_type = record.get('type', 'IDF')
                
                # Fetch only IDF type; skip DAF and any other type
                if record_type != 'IDF':
                    continue
                
                # Skip if no time block or scheduled_at
                if record_timeblock is None or not record_scheduled_at:
                    continue
                
                # Convert scheduled_at to date for comparison
                if isinstance(record_scheduled_at, datetime):
                    record_date = record_scheduled_at.date()
                else:
                    record_date = datetime.fromisoformat(str(record_scheduled_at)).date()
                
                scheduled_date = scheduled_at.date() if isinstance(scheduled_at, datetime) else scheduled_at
                
                # Include records that match the date and timeblock
                # Process ALL records for future dates, and records >= current timeblock for today
                if record_date == scheduled_date and record_timeblock >= timeblock:
                    filtered_data.append(record)
                elif record_date > scheduled_date:
                    filtered_data.append(record)
            
            logger.debug(f"Found {len(filtered_data)} demand records (type IDF) needing DSM processing for time block >= {timeblock}")
            
            return filtered_data
            
        except Exception as e:
            logger.error(f"Error fetching demand serve data: {str(e)}", exc_info=True)
            return []
    
    async def _calculate_schedule_from_supply(self, scheduled_at: datetime, timeblock: int) -> Optional[float]:
        """
        Calculate schedule_mw from supply_serve collection
        Filters for central plants (c_s = 'central') and SG records (dc_sg = 'SG')
        Aggregates sg_beforelosses field from matching records
        
        Args:
            scheduled_at: Scheduled date
            timeblock: Time block number
            
        Returns:
            float: Aggregated schedule_mw value or None if no data found
        """
        try:
            from ..repository.supply_serve_repository import supply_serve_repository
            from ..core.database import get_database
            
            db = get_database()
            
            # MongoDB aggregation pipeline to calculate schedule
            pipeline = [
                {
                    "$match": {
                        "scheduled_at": scheduled_at,
                        "time_block": timeblock,
                        "dc_sg": "SG"
                    }
                },
                {
                    "$lookup": {
                        "from": "plant_master",
                        "localField": "plant_id",
                        "foreignField": "plant_id",
                        "as": "plant_info"
                    }
                },
                {
                    "$unwind": {
                        "path": "$plant_info",
                        "preserveNullAndEmptyArrays": False  # Only include records with plant info
                    }
                },
                {
                    "$match": {
                        "plant_info.c_s": "central"
                    }
                },
                {
                    "$group": {
                        "_id": None,
                        "total_schedule": {"$sum": "$sg_beforelosses"}
                    }
                }
            ]
            
            cursor = supply_serve_repository.serve_collection.aggregate(pipeline)
            try:
                results = await cursor.to_list(10000)
            finally:
                cursor.close()
            
            if results and len(results) > 0:
                total_schedule = results[0].get('total_schedule', 0)
                logger.debug(f"Calculated schedule_mw from supply_serve: {total_schedule} for timeblock {timeblock} on {scheduled_at}")
                return total_schedule if total_schedule and total_schedule > 0 else None
            else:
                logger.warning(f"No supply data found for schedule calculation for timeblock {timeblock} on {scheduled_at}")
                return None
                
        except Exception as e:
            logger.error(f"Error calculating schedule from supply: {str(e)}", exc_info=True)
            return None
    
    async def _batch_update_demand_records_with_dsm(
        self, 
        demand_data: List[Dict[str, Any]], 
        max_dam_mcp: float, 
        max_rtm_mcp: float, 
        timeblock: int, 
        date
    ) -> int:
        """
        Batch update demand records with DSM rate and net DSM amount using new DSM engine
        
        Args:
            demand_data: List of demand serve records to update
            max_dam_mcp: Maximum DAM MCP across all exchanges
            max_rtm_mcp: Maximum RTM MCP across all exchanges
            timeblock: Time block number for logging
            date: Date for logging
            
        Returns:
            int: Number of records successfully updated
        """
        try:
            from ..repository.demand_serve_repository import demand_serve_repository
            from libs.models.demand_serve_entity import DemandServeEntity
            from libs.helpers.new_dsm_calculation import DSMEngine
            
            if not demand_data:
                return 0
            
            # Create DSM engine instance
            dsm_engine = DSMEngine()
            
            # Group records by unique key for batch processing
            records_by_key = {}
            
            for demand_record in demand_data:
                try:
                    scheduled_at = demand_record.get('scheduled_at')
                    record_timeblock = demand_record.get('time_block')
                    record_type = demand_record.get('type', 'IDF')
                    
                    if not scheduled_at or record_timeblock is None:
                        logger.warning("Skipping record without scheduled_at or time_block")
                        continue
                    
                    # Ensure scheduled_at is datetime
                    if isinstance(scheduled_at, str):
                        scheduled_at = datetime.fromisoformat(scheduled_at.replace('Z', '+00:00'))
                    
                    # Calculate schedule_mw from supply_serve collection
                    schedule_mw = await self._calculate_schedule_from_supply(scheduled_at, record_timeblock)
                    
                    # Extract other required fields for DSM calculation
                    actual_mw = demand_record.get('drawal')
                    frequency = demand_record.get('frequency')
                    rr_or_contract = 0  # Default contract rate
                    
                    # Validate all required fields - if any is missing, set DSM to None (null)
                    if not schedule_mw or schedule_mw == 0 or schedule_mw is None:
                        logger.warning(f"No schedule available for timeblock {record_timeblock} - setting DSM to None")
                        dsm_rate = None
                        net_dsm_amount = None
                    elif not actual_mw or actual_mw == 0 or actual_mw is None:
                        logger.warning(f"No drawal available for timeblock {record_timeblock} - setting DSM to None")
                        dsm_rate = None
                        net_dsm_amount = None
                    elif not frequency or frequency == 0 or frequency is None:
                        logger.warning(f"No frequency available for timeblock {record_timeblock} - setting DSM to None")
                        dsm_rate = None
                        net_dsm_amount = None
                    else:
                        # Calculate DSM using new engine (all required fields are available)
                        try:
                            result = dsm_engine.get_dsm_summary(
                                schedule_mw=schedule_mw,
                                actual_mw=actual_mw,
                                frequency=frequency,
                                acp_dam=max_dam_mcp,
                                acp_rtm=max_rtm_mcp,
                                rr_or_contract=rr_or_contract
                            )
                            
                            dsm_rate = result['dsm_rate']
                            net_dsm_amount = result['net_dsm_amount']
                            
                            logger.debug(f"Calculated DSM for timeblock {record_timeblock}: dsm_rate={dsm_rate}, net_dsm_amount={net_dsm_amount}")
                        except Exception as calc_error:
                            logger.error(f"Error calculating DSM: {str(calc_error)}")
                            dsm_rate = None
                            net_dsm_amount = None
                    
                    # Create updated record with DSM data
                    # If we could not compute a rate/amount, skip this record entirely
                    if dsm_rate is None or net_dsm_amount is None:
                        logger.warning(f"Skipping demand record for timeblock {record_timeblock} due to missing DSM values")
                        continue

                    updated_record = demand_record.copy()
                    
                    # Remove MongoDB _id to let it generate new one
                    if '_id' in updated_record:
                        del updated_record['_id']
                    
                    # Add DSM data
                    updated_record.update({
                        "dsm_rate": dsm_rate,
                        "net_dsm_amount": net_dsm_amount,
                        "updated_at": datetime.now(),
                        "updated_flag": "U"
                    })
                    
                    # Create DemandServeEntity
                    try:
                        demand_entity = DemandServeEntity(**updated_record)
                        
                        # Group by unique key for batch processing
                        unique_key = (scheduled_at, record_timeblock, record_type)
                        if unique_key not in records_by_key:
                            records_by_key[unique_key] = []
                        records_by_key[unique_key].append(demand_entity)
                    except Exception as entity_error:
                        logger.error(f"Error creating DemandServeEntity: {str(entity_error)}")
                        continue
                    
                except Exception as e:
                    logger.error(f"Error preparing record for DSM update: {str(e)}")
                    continue
            
            if not records_by_key:
                logger.warning(f"No valid records to update for time block {timeblock} on {date}")
                return 0
            
            # Prepare bulk operations
            bulk_operations = []
            
            for (scheduled_at, record_timeblock, record_type), entities in records_by_key.items():
                try:
                    # Use the most recent entity if there are duplicates
                    entities.sort(key=lambda x: x.updated_at, reverse=True)
                    entity_to_insert = entities[0]
                    
                    # Create filter criteria for this specific record
                    filter_criteria = {
                        "scheduled_at": scheduled_at,
                        "time_block": record_timeblock,
                        "type": record_type
                    }
                    
                    bulk_operations.append({
                        "filter": filter_criteria,
                        "document": entity_to_insert
                    })
                    
                except Exception as key_error:
                    logger.error(f"Error preparing bulk op for key {scheduled_at}, {record_timeblock}, {record_type}: {str(key_error)}")
                    continue
            
            if not bulk_operations:
                return 0

            # Execute bulk operations
            result = demand_serve_repository.bulk_delete_insert_custom(bulk_operations)
            
            inserted_count = result.get("inserted_count", 0)
            logger.info(f"Successfully batch updated {inserted_count} demand records with new DSM data for time block {timeblock} on {date}")
            return inserted_count
            
        except Exception as e:
            logger.error(f"Error in batch update of demand records with DSM data: {str(e)}", exc_info=True)
            return 0
    
    def process_raw_data_to_serve(self, raw_data: List[Dict[str, Any]], metadata: Dict[str, Any], provider_config: Dict[str, Any]) -> bool:
        """
        Implementation of abstract method from BaseProcessor
        Not used for DSM processor as it doesn't process raw data
        
        Returns:
            bool: Always True as this is not applicable for DSM processor
        """
        logger.info("New DSM processor does not process raw data - this method is not applicable")
        return True


if __name__ == "__main__":
    processor = NewDSMProcessor()
    processor._fetch_demand_serve_data(timeblock=43, scheduled_at=datetime.now())