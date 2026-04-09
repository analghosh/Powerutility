"""
DSM (Demand Side Management) Processor
Calculates DSM rates and net DSM amounts every 15 minutes
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from pymongo import MongoClient
from bson import ObjectId

from .base_processor import BaseProcessor
from ..core.config import settings
from libs.core.job_entities import JobDetail

logger = logging.getLogger(__name__)


class DSMProcessor(BaseProcessor):
    """
    DSM Processor for calculating DSM rates and net DSM amounts
    Runs every 15 minutes to process current and future time blocks
    """
    
    def __init__(self):
        super().__init__()
        self.time_blocks_cache = {}  # Cache for time blocks
        self.current_time_block = None
        
    async def execute_cron_job(self) -> Dict[str, Any]:
        """
        Main cron job execution method
        Called every 15 minutes to calculate DSM rates and net DSM amounts
        
        Returns:
            Dict[str, Any]: Execution result
        """
        # job_type = "dsm_rate_calculation"
        # job_name = "DSM Rate and Net DSM Calculation"
        # schedule = "*/15 * * * *"  # Default schedule
        # start_time = datetime.now()
        
        # # Generate and record job start to get execution_id (correlation ID)
        # execution_id = await self._record_job_start(job_type, job_name, schedule, start_time)
        # if not execution_id:
        #     logger.error("Failed to record job start for %s", job_type)
        #     return {
        #         "status": "error",
        #         "message": "Failed to record job start",
        #         "execution_time": 0.0,
        #         "data_type": "dsm"
        #     }
            
        # # Set execution_id as correlation ID using base processor method
        # self.set_correlation_id(execution_id)

        return {
            "status": "success",
            "message": "dsm skipped",
            "execution_time": 0.0,
            "data_type": "dsm"
        }
        
        # try:
            # logger.info("Starting DSM rate and net DSM amount calculation")
            # 
            # Step 1: Load time blocks into cache if not already loaded
            # if not self.time_blocks_cache:
                # self._load_time_blocks_cache()
            # 
            # Step 2: Determine current time block based on current time
            # current_time_block = self._get_current_time_block()
            # if not current_time_block:
                # logger.warning("Could not determine current time block, using fallback approach")
                # Fallback: use time block 1 and process all blocks
                # current_time_block = 1
            # 
            # logger.info(f"Current time block: {current_time_block}")
            # 
            # Step 3: Calculate time blocks to process (current + future blocks)
            # blocks_to_process = self._get_blocks_to_process(current_time_block)
            # logger.info(f"Processing {len(blocks_to_process)} time blocks")
            # 
            # Step 4: Process each time block
            # processed_count = 0
            # failed_count = 0
            # 
            # logger.info(f"Starting to process {len(blocks_to_process)} time blocks...")
            # 
            # for i, block_info in enumerate(blocks_to_process):
                # timeblock = block_info['timeblock']
                # date = block_info['date']
                # 
                # logger.debug(f"Processing time block {i+1}/{len(blocks_to_process)}: {timeblock} for date {date}")
                # 
                # try:
                    # if self._process_time_block(block_info):
                        # processed_count += 1
                        # logger.debug(f"✅ Successfully processed time block {timeblock} for date {date}")
                    # else:
                        # failed_count += 1
                        # logger.warning(f"❌ Failed to process time block {timeblock} for date {date}")
                # except Exception as e:
                    # failed_count += 1
                    # logger.error(f"💥 Exception processing time block {timeblock} for date {date}: {str(e)}")
                    # continue
            # success = processed_count > 0
            # end_time = datetime.now()
            # execution_time = (end_time - start_time).total_seconds() if start_time else 0.0
            # execution_id = str(ObjectId())
            # 
# 
            # result = {
                # "status": "success" if success else "error",
                # "message": "Demand data processed successfully" if success else "Demand data processing failed",
                # "execution_time": execution_time,
                # "data_type": "demand",
                # "correlation_id": execution_id,
                # "cron_job":"dsm"
                # 
            # }
            # 
            # await self._record_job_completion(
                # job_type=job_type,
                # job_name=job_name,
                # schedule=schedule,
                # execution_start=start_time,
                # execution_end=end_time,
                # execution_id=execution_id,
                # status=result["status"],
                # success=success,
                # result=result
            # )
            # 
            # logger.info(f"Processing summary: {processed_count} successful, {failed_count} failed out of {len(blocks_to_process)} total")
            # 
            # logger.info(f"Successfully processed {processed_count}/{len(blocks_to_process)} time blocks")
            # return processed_count > 0
            # 
        # except Exception as e:
            # logger.error(f"Error in DSM cron job execution: {str(e)}", exc_info=True)
            # 
            # end_time = datetime.now()
            # execution_time = (end_time - start_time).total_seconds()
            # error_result = {
                # "status": "error",
                # "message": f"DSM processing error: {str(e)}",
                # "execution_time": execution_time,
                # "data_type": "demand",
                # "correlation_id": execution_id,
                # "cron_job":"dsm"
            # }
            # 
            # await self._record_job_completion(
                # job_type=job_type,
                # job_name=job_name,
                # schedule=schedule,
                # execution_start=start_time,
                # execution_end=end_time,
                # execution_id=execution_id,
                # status="error",
                # success=False,
                # result=error_result,
                # error_message=str(e)
            # )
            # return False
        # finally:
            # Clear correlation ID after job completion
            # self.clear_correlation_id()
    
    async def _load_time_blocks_cache(self):
        """Load all time blocks from database into cache"""
        try:
            from ..core.database import get_database
            db = get_database()
            time_block_collection = db[settings.MONGODB_TIMEBLOCK_MASTER_COLLECTION]
            
            # Fetch all time blocks and create cache
            cursor = time_block_collection.find({}).sort("timeblock", 1)
            try:
                time_blocks = await cursor.to_list(None)
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
                
                logger.debug(f"Checking time block {timeblock}: {start_time} - {end_time}")
                
                # Handle time comparison (considering 24-hour format)
                if self._is_time_in_range(current_time_str, start_time, end_time):
                    logger.debug(f"Found matching time block: {timeblock}")
                    return timeblock
            
            # If no exact match found, try to find the closest time block
            # This handles edge cases where current time might be exactly at boundary
            logger.warning(f"No exact time block found for current time: {current_time_str}")
            
            # Try to find the time block that should contain this time
            # by checking if current time is after start time of any block
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
        Handles special case where end_time is "24:00" (midnight)
        
        Args:
            current_time: Current time in HH:MM format
            start_time: Block start time in HH:MM format  
            end_time: Block end time in HH:MM format (can be "24:00")
            
        Returns:
            bool: True if current time is in range
        """
        try:
            from datetime import time
            
            # Handle special case of "24:00" which should be treated as "00:00" of next day
            if end_time == "24:00":
                end_time = "00:00"
                is_cross_midnight = True
            else:
                is_cross_midnight = False
            
            current = datetime.strptime(current_time, "%H:%M").time()
            start = datetime.strptime(start_time, "%H:%M").time()
            end = datetime.strptime(end_time, "%H:%M").time()
            
            # Handle case where end time is next day (e.g., 23:45 to 00:00)
            if is_cross_midnight or end < start:
                return current >= start or current < end
            else:
                return start <= current < end
                
        except Exception as e:
            logger.error(f"Error comparing times: {str(e)}")
            return False
    
    def _get_blocks_to_process(self, current_time_block: int) -> List[Dict[str, Any]]:
        """
        Get list of time blocks to process (remaining blocks today + all blocks tomorrow)
        
        Args:
            current_time_block: Current time block number
            
        Returns:
            List of block info dictionaries with timeblock, date
        """
        try:
            blocks_to_process = []
            today = datetime.now().date()
            tomorrow = today + timedelta(days=1)
            
            # Add remaining blocks for today (from current block onwards)
            for block_num in range(current_time_block, 97):  # 1-96 blocks
                if block_num in self.time_blocks_cache:
                    blocks_to_process.append({
                        'timeblock': block_num,
                        'date': today,
                        'scheduled_at': datetime.combine(today, datetime.min.time())
                    })
            
            # Add all blocks for tomorrow
            for block_num in range(1, 97):  # 1-96 blocks
                if block_num in self.time_blocks_cache:
                    blocks_to_process.append({
                        'timeblock': block_num,
                        'date': tomorrow,
                        'scheduled_at': datetime.combine(tomorrow, datetime.min.time())
                    })
            
            logger.info(f"Generated {len(blocks_to_process)} blocks to process (Today: {97-current_time_block}, Tomorrow: 96)")
            return blocks_to_process
            
        except Exception as e:
            logger.error(f"Error generating blocks to process: {str(e)}")
            return []
    
    async def _process_time_block(self, block_info: Dict[str, Any]) -> bool:
        """
        Process a single time block to calculate DSM rate and net DSM amount
        
        Args:
            block_info: Dictionary containing timeblock, date, scheduled_at
            
        Returns:
            bool: True if processing was successful
        """
        try:
            timeblock = block_info['timeblock']
            date = block_info['date']
            scheduled_at = block_info['scheduled_at']
            
            logger.debug(f"🔄 Processing time block {timeblock} for date {date}")
            
            # Step 1: Fetch exchange data for this time block and date
            logger.debug(f"📊 Step 1: Fetching exchange data for time block {timeblock}")
            
            # Debug: Check what exchange data exists in the database
            try:
                from ..core.database import get_database
                db = get_database()
                exchange_collection = db[settings.MONGODB_EXCHANGE_SERVE_COLLECTION]
                
                # Check total exchange records
                total_exchange = exchange_collection.count_documents({})
                logger.debug(f"🔍 DEBUG: Total exchange records in database: {total_exchange}")
                
                # Check records for this date
                date_query = {
                    "scheduled_at": {
                        "$gte": datetime.combine(date, datetime.min.time()),
                        "$lt": datetime.combine(date, datetime.min.time()) + timedelta(days=1)
                    }
                }
                date_records = exchange_collection.count_documents(date_query)
                logger.debug(f"🔍 DEBUG: Exchange records for date {date}: {date_records}")
                
                # Check records for this time block and date
                timeblock_query = {
                    **date_query,
                    "time_block": timeblock
                }
                timeblock_records = exchange_collection.count_documents(timeblock_query)
                logger.debug(f"🔍 DEBUG: Exchange records for date {date} and time_block {timeblock}: {timeblock_records}")
                
                # Show sample record structure
                sample_record = exchange_collection.find_one({})
                if sample_record:
                    logger.debug(f"🔍 DEBUG: Sample exchange record structure: {list(sample_record.keys())}")
                    logger.debug(f"🔍 DEBUG: Sample scheduled_at: {sample_record.get('scheduled_at')}, time_block: {sample_record.get('time_block')}")
                
            except Exception as debug_error:
                logger.error(f"Debug query error: {str(debug_error)}")
            
            exchange_data = await self._fetch_exchange_data(timeblock, scheduled_at)
            if not exchange_data:
                logger.warning(f"❌ Step 1 FAILED: No exchange data found for time block {timeblock} on {date}")
                return False
            
            logger.debug(f"✅ Step 1 SUCCESS: Found {len(exchange_data)} exchange records")
            
            # Step 2: Calculate DSM rate (deviation_rate) from exchange data using dsm_calculation and extract ACP
            logger.debug(f"🧮 Step 2: Calculating DSM rate (deviation_rate) for time block {timeblock}")
            dsm_rate = self._calculate_dsm_rate(exchange_data, timeblock, scheduled_at)
            if dsm_rate is None:
                logger.warning(f"❌ Step 2 FAILED: Could not calculate DSM rate for time block {timeblock} on {date}")
                return False
            
            # Extract ACP from exchange_data (needed for dsm_calculation in net_dsm_amount)
            acp = None
            if exchange_data:
                for record in exchange_data:
                    acp_val = record.get('acp')
                    if acp_val is not None:
                        if acp is None or acp_val > acp:
                            acp = acp_val
            
            # Default ACP if not found (fallback)
            if acp is None:
                logger.warning(f"No ACP found in exchange data, using default ACP=100.0")
                acp = 100.0
            
            logger.debug(f"✅ Step 2 SUCCESS: Calculated DSM rate (deviation_rate): {dsm_rate}, ACP: {acp} for time block {timeblock}")
            
            # Step 3: Fetch demand serve data for net DSM amount calculation
            logger.debug(f"📋 Step 3: Fetching demand serve data for time block {timeblock}")
            
            # Debug: Check what demand data exists in the database
            try:
                from ..core.database import get_database
                db = get_database()
                demand_collection = db[settings.MONGODB_DEMAND_SERVE_COLLECTION]
                
                # Check total demand records
                total_demand = demand_collection.count_documents({})
                logger.debug(f"🔍 DEBUG: Total demand records in database: {total_demand}")
                
                # Check records for this date
                date_query = {
                    "scheduled_at": {
                        "$gte": datetime.combine(date, datetime.min.time()),
                        "$lt": datetime.combine(date, datetime.min.time()) + timedelta(days=1)
                    }
                }
                date_records = demand_collection.count_documents(date_query)
                logger.debug(f"🔍 DEBUG: Demand records for date {date}: {date_records}")
                
                # Check records for this time block and date
                timeblock_query = {
                    **date_query,
                    "time_block": timeblock
                }
                timeblock_records = demand_collection.count_documents(timeblock_query)
                logger.debug(f"🔍 DEBUG: Demand records for date {date} and time_block {timeblock}: {timeblock_records}")
                
                # Show sample record structure
                sample_record = demand_collection.find_one({})
                if sample_record:
                    logger.debug(f"🔍 DEBUG: Sample demand record structure: {list(sample_record.keys())}")
                    logger.debug(f"🔍 DEBUG: Sample scheduled_at: {sample_record.get('scheduled_at')}, time_block: {sample_record.get('time_block')}")
                
            except Exception as debug_error:
                logger.error(f"Debug query error: {str(debug_error)}")
            
            demand_data = self._fetch_demand_serve_data(timeblock, scheduled_at)
            if not demand_data:
                logger.warning(f"❌ Step 3 FAILED: No demand serve data found for time block {timeblock} on {date}")
                logger.debug(f"Debug info - timeblock: {timeblock}, scheduled_at: {scheduled_at}, date: {date}")
                return False
            
            logger.debug(f"✅ Step 3 SUCCESS: Found {len(demand_data)} demand records for processing time block {timeblock} on {date}")
            
            # Step 4: Calculate net DSM amount and batch update demand records
            logger.debug(f"💾 Step 4: Updating demand records with DSM data for time block {timeblock}")
            updated_count = self._batch_update_demand_records_with_dsm(demand_data, dsm_rate, acp, timeblock, date)
            
            if updated_count > 0:
                logger.info(f"✅ Step 4 SUCCESS: Updated {updated_count} demand records with DSM data for time block {timeblock} on {date}")
                return True
            else:
                logger.warning(f"❌ Step 4 FAILED: Updated 0 demand records for time block {timeblock} on {date}")
                return False
            
        except Exception as e:
            logger.error(f"Error processing time block {block_info}: {str(e)}")
            return False
    
    async def _fetch_exchange_data(self, timeblock: int, scheduled_at: datetime) -> List[Dict[str, Any]]:
        """
        Fetch exchange data (RTM and DAM) for the given time block and date using proper repository
        
        Args:
            timeblock: Time block number
            scheduled_at: Scheduled date
            
        Returns:
            List of exchange data records
        """
        try:
            from ..repository.exchange_repository import ExchangeRepository
            
            exchange_repository = ExchangeRepository()
            
            # Query for RTM data
            rtm_query = {
                "scheduled_at": scheduled_at,
                "time_block": timeblock,
                "market_type": "RTM"
            }
            
            # Query for DAM data
            dam_query = {
                "scheduled_at": scheduled_at,
                "time_block": timeblock,
                "market_type": "DAM"
            }
            
            exchange_data = []
            
            logger.debug(f"RTM Query: {rtm_query}")
            logger.debug(f"DAM Query: {dam_query}")
            
            # Fetch RTM data (try main collection first, then historical)
            rtm_records = exchange_repository.find_many(rtm_query)
            logger.debug(f"RTM records from main collection: {len(rtm_records)}")
            
            if not rtm_records:
                try:
                    cursor = exchange_repository.historical_collection.find(rtm_query)
                    try:
                        rtm_records = await cursor.to_list(None)
                    finally:
                        cursor.close()
                    logger.debug(f"RTM records from historical collection: {len(rtm_records)}")
                except Exception as e:
                    logger.debug(f"Error querying historical collection for RTM: {e}")
                    rtm_records = []
            
            exchange_data.extend(rtm_records)
            
            # Fetch DAM data (try main collection first, then historical)
            dam_records = exchange_repository.find_many(dam_query)
            logger.debug(f"DAM records from main collection: {len(dam_records)}")
            
            if not dam_records:
                try:
                    cursor = exchange_repository.historical_collection.find(dam_query)
                    try:
                        dam_records = await cursor.to_list(None)
                    finally:
                        cursor.close()
                    logger.debug(f"DAM records from historical collection: {len(dam_records)}")
                except Exception as e:
                    logger.debug(f"Error querying historical collection for DAM: {e}")
                    dam_records = []
            
            exchange_data.extend(dam_records)
            
            logger.debug(f"Total exchange records found: {len(exchange_data)} (RTM: {len(rtm_records)}, DAM: {len(dam_records)})")
            
            # If no data found, try a broader query to see what's available
            if not exchange_data:
                logger.debug("No exchange data found with exact query, checking what's available...")
                # Check what time blocks exist for this date
                date_query = {
                    "scheduled_at": scheduled_at
                }
                available_records = exchange_repository.find_many(date_query)
                if available_records:
                    available_blocks = [tb for r in available_records if (tb := r.get('time_block')) is not None]
                    if available_blocks:
                        unique_blocks = sorted(set(available_blocks))
                        logger.debug(f"Available time blocks for date {scheduled_at.date()}: {unique_blocks}")
                else:
                    logger.debug(f"No exchange records found for date {scheduled_at.date()}")
            
            return exchange_data
            
        except Exception as e:
            logger.error(f"Error fetching exchange data: {str(e)}")
            return []
    
    def _calculate_dsm_rate(self, exchange_data: List[Dict[str, Any]], timeblock: int, scheduled_at: datetime) -> Optional[float]:
        """
        Calculate DSM rate (deviation_rate) using dsm_calculation helper function.
        This uses deviation_rate from dsm_calculation, which is calculated using get_deviation_charge(acp, frequency).
        
        Args:
            exchange_data: List of exchange data records (contains ACP)
            timeblock: Time block number
            scheduled_at: Scheduled date/time (used to fetch frequency from demand records)
            
        Returns:
            float: DSM rate (deviation_rate) or None if calculation fails
        """
        try:
            from libs.helpers.dsm_calculation import dsm_calculation, get_fifteen_time_block_id
            from datetime import datetime
            
            if not exchange_data:
                return None
            
            # Find maximum ACP value among all exchange records for this time block
            max_acp = None
            for record in exchange_data:
                acp = record.get('acp')
                if acp is not None:
                    if max_acp is None or acp > max_acp:
                        max_acp = acp
            
            if max_acp is None:
                logger.warning(f"No ACP values found in exchange data for time block {timeblock}")
                return None
            
            # Get frequency from demand records for this timeblock and date
            frequency = None
            try:
                from ..repository.demand_serve_repository import demand_serve_repository
                
                # Query for a demand record with this timeblock and date to get frequency
                start_of_day = datetime.combine(scheduled_at.date() if isinstance(scheduled_at, datetime) else scheduled_at, datetime.min.time())
                end_of_day = start_of_day + timedelta(days=1)
                
                demand_records = demand_serve_repository.find_demand_serve_by_date_range(start_of_day, end_of_day)
                for record in demand_records:
                    if record.get('time_block') == timeblock:
                        freq_val = record.get('frequency') or record.get('Raw_Frequency')
                        if freq_val is not None:
                            frequency = float(freq_val)
                            break
            except Exception as fetch_error:
                logger.debug(f"Could not fetch frequency from demand records: {fetch_error}")
            
            # Default frequency if not found (normal operating frequency)
            if frequency is None:
                logger.warning(f"No frequency found for time block {timeblock}, using default frequency=50.0 Hz")
                frequency = 50.0
            
            # Prepare data array for dsm_calculation
            # Convert time_block to start_time string (time_block is 1-indexed, convert to 0-indexed)
            time_block_idx = timeblock - 1 if timeblock > 0 else 0
            start_time = get_fifteen_time_block_id(time_block_idx)
            
            # Get inserted_date from scheduled_at
            if isinstance(scheduled_at, datetime):
                inserted_date = scheduled_at.strftime('%Y-%m-%d')
            else:
                inserted_date = str(scheduled_at).split('T')[0]  # Extract date part
            
            # Prepare minimal data array for dsm_calculation (we only need deviation_rate)
            # Use default values for schedule and drawal since we only need frequency for deviation_rate calculation
            data_array = [{
                'start_time': start_time,
                'frequency': frequency,
                'schedule': 0.0,  # Not used for deviation_rate calculation
                'drawal': 0.0,    # Not used for deviation_rate calculation
                'inserted_date': inserted_date,
                'demand': 0.0     # Not used for deviation_rate calculation
            }]
            
            # Call dsm_calculation - returns dict with numeric keys (0, 1, 2...)
            dsm_result = dsm_calculation(max_acp, data_array)
            
            # Extract deviation_rate from the result (this is used as dsm_rate)
            if dsm_result and isinstance(dsm_result, dict) and 0 in dsm_result:
                result_item = dsm_result[0]
                deviation_rate = float(result_item.get('deviation_rate', 0.0))
                
                # Use deviation_rate as dsm_rate
                dsm_rate = deviation_rate
                
                logger.debug(f"DSM rate (deviation_rate) calculation from dsm_calculation: "
                           f"max_acp={max_acp}, frequency={frequency}, deviation_rate={deviation_rate}")
                
                return dsm_rate
            else:
                logger.warning(f"dsm_calculation returned invalid result: {dsm_result}")
                # Fallback to old calculation method
                dsm_rate = 0.0
                logger.debug(f"Using fallback DSM rate calculation:  dsm_rate={dsm_rate}")
                return dsm_rate
            
        except Exception as e:
            logger.error(f"Error calculating DSM rate (deviation_rate): {str(e)}", exc_info=True)
            # Fallback: try old calculation method
            try:
                
                return  0
            except:
                pass
            return None
    
    def _fetch_demand_serve_data(self, timeblock: int, scheduled_at: datetime) -> List[Dict[str, Any]]:
        """
        Fetch demand serve data for the specific date and time block using proper repository method
        
        Args:
            timeblock: Time block number
            scheduled_at: Scheduled date (should be specific to the date being processed)
            
        Returns:
            List of demand serve data records that need DSM processing
        """
        try:
            from ..repository.demand_serve_repository import demand_serve_repository
            
            # Get records for the specific date only (not a range)
            # scheduled_at should already be the specific date we're processing
            start_of_day = datetime.combine(scheduled_at.date(), datetime.min.time())
            end_of_day = start_of_day + timedelta(days=1)
            
            logger.debug(f"Fetching demand data for specific date: {start_of_day} to {end_of_day}")
            demand_data = demand_serve_repository.find_demand_serve_by_date_range(start_of_day, end_of_day)
            logger.debug(f"Found {len(demand_data)} serve records for date: {scheduled_at.date()}")
            
            # Filter for time blocks >= current timeblock and records that need DSM processing
            filtered_data = []
            total_records = len(demand_data)
            records_by_timeblock = {}
            records_without_dsm = 0
            
            logger.debug(f"Filtering {total_records} records for time block >= {timeblock}")
            
            for record in demand_data:
                record_timeblock = record.get('time_block')
                record_scheduled_at = record.get('scheduled_at')
                
                # Skip if no time block or scheduled_at
                if record_timeblock is None or not record_scheduled_at:
                    logger.debug(f"Skipping record without time_block or scheduled_at: timeblock={record_timeblock}, scheduled_at={record_scheduled_at}")
                    continue
                
                # Track records by time block for debugging
                if record_timeblock not in records_by_timeblock:
                    records_by_timeblock[record_timeblock] = 0
                records_by_timeblock[record_timeblock] += 1
                
                # Convert scheduled_at to date for comparison
                if isinstance(record_scheduled_at, datetime):
                    record_date = record_scheduled_at.date()
                else:
                    record_date = datetime.fromisoformat(str(record_scheduled_at)).date()
                
                scheduled_date = scheduled_at.date() if isinstance(scheduled_at, datetime) else scheduled_at
                
                # Include records that are:
                # 1. From today with time_block >= current timeblock, OR
                # 2. From future dates (any time block)
                include_record = False
                if record_date == scheduled_date and record_timeblock >= timeblock:
                    include_record = True
                    logger.debug(f"Including today's record: timeblock={record_timeblock}, date={record_date}")
                elif record_date > scheduled_date:
                    include_record = True
                    logger.debug(f"Including future record: timeblock={record_timeblock}, date={record_date}")
                
                if include_record:
                    # Include ALL records that match time block and date criteria
                    # No filtering based on existing DSM data - always recalculate
                    filtered_data.append(record)
                    records_without_dsm += 1
                    logger.debug(f"Added record for DSM processing: timeblock={record_timeblock}, date={record_date}")
            
            # Log summary for debugging
            logger.info(f"Filtering summary for timeblock >= {timeblock}:")
            logger.info(f"  Total records in date range: {total_records}")
            logger.info(f"  Records by time block: {dict(sorted(records_by_timeblock.items()))}")
            logger.info(f"  Records selected for processing: {records_without_dsm}")
            logger.info(f"  Final filtered records: {len(filtered_data)}")
            
            # Check if there are any records for the specific timeblock we're looking for
            if timeblock in records_by_timeblock:
                logger.info(f"  Records found for target timeblock {timeblock}: {records_by_timeblock[timeblock]}")
            else:
                logger.warning(f"  No records found for target timeblock {timeblock}")
                # Show available time blocks
                available_blocks = sorted(records_by_timeblock.keys())
                logger.info(f"  Available time blocks: {available_blocks}")
            
            logger.debug(f"Found {len(filtered_data)} demand records needing DSM processing for time block >= {timeblock}")
            
            return filtered_data
            
        except Exception as e:
            logger.error(f"Error fetching demand serve data: {str(e)}")
            return []
    
    def _calculate_net_dsm_amount(self, demand_record: Dict[str, Any], acp: float) -> float:
        """
        Calculate net DSM amount for a demand record using dsm_calculation helper function.
        This uses the comprehensive DSM calculation algorithm.
        
        Args:
            demand_record: Demand serve record containing frequency, schedule, drawal, time_block, etc.
            acp: Area Clearing Price (required for dsm_calculation)
            
        Returns:
            float: Net DSM amount calculated from dsm_calculation
        """
        try:
            from libs.helpers.dsm_calculation import dsm_calculation, get_fifteen_time_block_id
            from datetime import datetime
            
            # Validate ACP
            if acp is None or acp <= 0:
                logger.warning(f"Invalid ACP value: {acp}. Cannot calculate net_dsm_amount.")
                return 0.0
            
            # Extract required fields from demand_record
            time_block = demand_record.get('time_block', 0)
            
            # Safe float conversion
            def safe_float(val):
                if val is None: return 0.0
                try: return float(val)
                except (ValueError, TypeError): return 0.0

            frequency = safe_float(demand_record.get('frequency') or demand_record.get('Raw_Frequency'))
            schedule = safe_float(demand_record.get('schedule'))
            drawal = safe_float(demand_record.get('drawal'))
            # drawal is already assigned above using safe_float
            # drawal = float(demand_record.get('drawal', demand_record.get('actual', 0))) 
            
            # For actual, use safe_float logic, fallback to drawal if needed
            actual_val = demand_record.get('actual')
            if actual_val is None:
                actual = drawal
            else:
                actual = safe_float(actual_val)
            
            # Validate required fields
            if time_block == 0:
                logger.warning(f"Invalid time_block (0) in demand_record. Available keys: {list(demand_record.keys())}")
                return 0.0
            
            if frequency == 0:
                logger.warning(f"Frequency is 0 in demand_record for time_block {time_block}. This may cause incorrect calculation.")
            
            # Get inserted_date from scheduled_at or use current date
            scheduled_at = demand_record.get('scheduled_at')
            if scheduled_at:
                if isinstance(scheduled_at, datetime):
                    inserted_date = scheduled_at.strftime('%Y-%m-%d')
                else:
                    inserted_date = str(scheduled_at).split('T')[0]  # Extract date part
            else:
                inserted_date = datetime.now().strftime('%Y-%m-%d')
            
            # Convert time_block to start_time string (time_block is 1-indexed, convert to 0-indexed)
            time_block_idx = time_block - 1 if time_block > 0 else 0
            start_time = get_fifteen_time_block_id(time_block_idx)
            
            # Prepare data array in the format expected by dsm_calculation
            data_array = [{
                'start_time': start_time,
                'frequency': frequency,
                'schedule': schedule,
                'drawal': drawal,
                'inserted_date': inserted_date,
                'demand': actual if actual else drawal
            }]
            
            logger.debug(f"Calling dsm_calculation with ACP={acp}, data_array={data_array}")
            
            # Call dsm_calculation - returns dict with numeric keys (0, 1, 2...)
            dsm_result = dsm_calculation(acp, data_array)
            
            # Debug: Log dsm_result structure
            if dsm_result is None:
                logger.error(f"dsm_calculation returned None for time_block {time_block}, ACP={acp}")
                return 0.0
            
            if not isinstance(dsm_result, dict):
                logger.error(f"dsm_calculation returned non-dict type: {type(dsm_result)}, value: {dsm_result}")
                return 0.0
            
            if len(dsm_result) == 0:
                logger.error(f"dsm_calculation returned empty dict for time_block {time_block}, ACP={acp}")
                return 0.0
            
            # Check if key 0 exists, if not try to find first numeric key
            if 0 not in dsm_result:
                logger.warning(f"dsm_calculation result doesn't have key 0. Available keys: {list(dsm_result.keys())}")
                # Try to get first key
                first_key = list(dsm_result.keys())[0] if dsm_result else None
                if first_key is not None:
                    logger.info(f"Using first available key: {first_key}")
                    result_item = dsm_result[first_key]
                else:
                    logger.error(f"No keys found in dsm_result: {dsm_result}")
                    return 0.0
            else:
                result_item = dsm_result[0]
            
            if result_item is None:
                logger.error(f"result_item is None for time_block {time_block}")
                return 0.0
            
            # Extract net_dsm_amount from the result
            net_dsm_amount = safe_float(result_item.get('net_dsm_amount'))
            
            logger.debug(f"Net DSM calculation from dsm_calculation: drawal={drawal}, schedule={schedule}, "
                       f"frequency={frequency}, acp={acp}, net_dsm_amount={net_dsm_amount}")
            
            return net_dsm_amount
            
        except KeyError as ke:
            logger.error(f"Missing key in demand_record: {str(ke)}. Available keys: {list(demand_record.keys()) if isinstance(demand_record, dict) else 'N/A'}")
            return 0.0
        except ValueError as ve:
            logger.error(f"Value error in net DSM calculation (likely type conversion): {str(ve)}. demand_record keys: {list(demand_record.keys()) if isinstance(demand_record, dict) else 'N/A'}")
            return 0.0
        except Exception as e:
            logger.error(f"Error calculating net DSM amount: {str(e)}. demand_record type: {type(demand_record)}, "
                        f"keys: {list(demand_record.keys()) if isinstance(demand_record, dict) else 'N/A'}", exc_info=True)
            return 0.0
    
    def _batch_update_demand_records_with_dsm(self, demand_data: List[Dict[str, Any]], dsm_rate: float, acp: float, timeblock: int, date) -> int:
        """
        Batch update demand records with DSM rate and net DSM amount using proper repository method
        Uses delete-insert pattern suitable for time series collections
        
        Args:
            demand_data: List of demand serve records to update
            dsm_rate: Calculated DSM rate
            acp: Area Clearing Price (required for dsm_calculation)
            timeblock: Time block number for logging
            date: Date for logging
            
        Returns:
            int: Number of records successfully updated
        """
        try:
            from ..repository.demand_serve_repository import demand_serve_repository
            from libs.models.demand_serve_entity import DemandServeEntity
            
            if not demand_data:
                return 0
            
            # Group records by unique key (scheduled_at, time_block, type) for batch processing
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
                    
                    # Calculate net DSM amount for this record using dsm_calculation
                    net_dsm_amount = self._calculate_net_dsm_amount(demand_record, acp)
                    
                    # Create updated record with DSM data
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
            logger.info(f"Successfully batch updated {inserted_count} demand records with DSM data for time block {timeblock} on {date}")
            return inserted_count
            
        except Exception as e:
            logger.error(f"Error in batch update of demand records with DSM data: {str(e)}")
            return 0
    

    


    def process_raw_data_to_serve(self, raw_data: List[Dict[str, Any]], metadata: Dict[str, Any], provider_config: Dict[str, Any]) -> bool:
        """
        Implementation of abstract method from BaseProcessor
        Not used for DSM processor as it doesn't process raw data
        
        Returns:
            bool: Always True as this is not applicable for DSM processor
        """
        logger.info("DSM processor does not process raw data - this method is not applicable")
        return True
    


