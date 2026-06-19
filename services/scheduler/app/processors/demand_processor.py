"""
Demand data processor for handling demand data processing logic.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta, date, time
from zoneinfo import ZoneInfo
from typing import Dict, List, Any, Optional

from .base_processor import BaseProcessor
from libs.models.demand_serve_entity import DemandServeEntity, DemandServeHistoricalEntity
from libs.enum.updated_flag import UpdatedFlag
from libs.core.job_entities import JobDetail
from libs.models.forecast_serve_entity import FORECAST_SOURCE_CSV
from ..core.config import settings
from ..repository.demand_serve_repository import demand_serve_repository
from ..repository.forecast_serve_repository import forecast_serve_repository
from ..repository.supply_serve_repository import supply_serve_repository
from ..repository.exchange_repository import ExchangeRepository
from libs.audit_queue.simple_logger import AuditLogger
from libs.middleware import get_correlation_id
from ..core.dependencies import get_audit_logger

logger = logging.getLogger(__name__)


class DemandProcessor(BaseProcessor):
    """
    Demand data processor that handles demand data processing and storage.
    """
    
    def __init__(self):
        super().__init__()
        
        # Use the existing demand serve repository
        self.demand_repository = demand_serve_repository
        self.forecast_repository = forecast_serve_repository
        self.supply_repository = supply_serve_repository
        
        # Initialize exchange repository for fetching area prices
        self.exchange_repository = ExchangeRepository()
        
        # Initialize master plants repository for demand multiplier calculation
        from ..repository.master_plants_repository import MasterPlantsRepository
        self.master_plants_repository = MasterPlantsRepository()



    def _parse_idf_forecast_from_raw(self, raw_record: Dict[str, Any]) -> Optional[float]:
        """Parse IDF forecast from CSV row; None if column missing or empty."""
        if "forecast" not in raw_record:
            return None
        val = raw_record.get("forecast")
        if val is None or val == "":
            return None
        try:
            return self._safe_float(val)
        except (TypeError, ValueError):
            return None

    def _calculate_demand_multiplier(self, plants: List[Dict[str, Any]]) -> float:
        """
        Calculate the demand multiplier as the average of valid plant multipliers.
        Excludes plants with:
        - 0 or None multiplier
        - IDs: "OA_PX", "OA_GNA", "OA_TGNA"
        - c_s = "state" (case-insensitive)
        """
        valid_multipliers = []
        excluded_ids = ["OA_PX", "OA_GNA", "OA_TGNA"]
        
        for plant in plants:
            plant_id = plant.get("plant_id")
            multiplier = plant.get("multiplier")
            c_s = plant.get("c_s")
            
            # Check for excluded IDs
            if plant_id in excluded_ids:
                continue
                
            # Check for state plants
            if c_s and str(c_s).lower() == "state":
                continue
                
            # Check for valid multiplier
            if multiplier and multiplier > 0:
                valid_multipliers.append(multiplier)
                
        if not valid_multipliers:
            return 0.0
            
        return sum(valid_multipliers) / len(valid_multipliers)

    async def _fetch_exchange_data_for_dsm_for_one_time_block(self, scheduled_at: datetime, time_blocks: int) -> Dict[str, Any]:
        """
        Fetch exchange data (RTM and DAM) for DSM calculation based on scheduled_at and a single time_block.
        
        Args:
            scheduled_at: The scheduled date for which to fetch exchange data
            time_blocks: Single time block to fetch data for
        Returns:
            Dict containing RTM and DAM area price data and ACP values
        """
        try:
            logger.info(    
                f"Fetching exchange data for DSM calculation - scheduled_at: {scheduled_at}, time_block: {time_blocks}"
            )
            
            # Debug: Log the scheduled_at type and format
            logger.debug(f"Exchange query - scheduled_at type: {type(scheduled_at)}, value: {scheduled_at}")
            
            
            # Fetch RTM data using correct field names (check both main and historical collections)
            rtm_query = {
                "scheduled_at": scheduled_at,  # Use 'Date' instead of 'scheduled_at'
                "time_block": time_blocks,  # Use string format instead of integer
                "market_type": "RTM"  
            }
            logger.debug(f"RTM Query: {rtm_query}")
            
            # Try main exchange collection first
            rtm_records = await self.exchange_repository.find_one(rtm_query)
            logger.debug(f"RTM Records Found (main): {rtm_records is not None}")
            
            # If not found in main collection, try historical collection
            if not rtm_records:
                logger.debug("RTM data not found in main collection, checking historical...")
                try:
                    rtm_records = await self.exchange_repository.historical_collection.find_one(rtm_query)
                    logger.debug(f"RTM Records Found (historical): {rtm_records is not None}")
                except Exception as e:
                    logger.debug(f"Error querying historical collection for RTM: {e}")
                    rtm_records = None
            
            # If still no exact match, try date-only matching for RTM
            if not rtm_records:
                scheduled_date = scheduled_at.date() if hasattr(scheduled_at, 'date') else scheduled_at
                rtm_date_query = {
                    "scheduled_at": {"$gte": datetime.combine(scheduled_date, datetime.min.time()),
                           "$lt": datetime.combine(scheduled_date, datetime.min.time()) + timedelta(days=1)},
                    "time_block": time_blocks,
                    "market_type": "RTM"  
                }
                logger.debug(f"RTM Date Range Query: {rtm_date_query}")
                
                # Try date range in main collection first
                rtm_records = await self.exchange_repository.find_one(rtm_date_query)
                logger.debug(f"RTM Records Found (main date range): {rtm_records is not None}")
                
                # If still not found, try date range in historical collection
                if not rtm_records:
                    try:
                        rtm_records = await self.exchange_repository.historical_collection.find_one(rtm_date_query)
                        logger.debug(f"RTM Records Found (historical date range): {rtm_records is not None}")
                    except Exception as e:
                        logger.debug(f"Error querying historical collection for RTM date range: {e}")
                        rtm_records = None

            dam_query = {
                "scheduled_at": scheduled_at,  # Use 'Date' instead of 'scheduled_at'
                "time_block": time_blocks,  # Use string format instead of integer
                "market_type": "DAM"
            }
            logger.debug(f"DAM Query: {dam_query}")
            
            # Try main exchange collection first
            dam_records = await self.exchange_repository.find_one(dam_query)
            logger.debug(f"DAM Records Found (main): {dam_records is not None}")
            
            # If not found in main collection, try historical collection
            if not dam_records:
                logger.debug("DAM data not found in main collection, checking historical...")
                try:
                    dam_records = await self.exchange_repository.historical_collection.find_one(dam_query)
                    logger.debug(f"DAM Records Found (historical): {dam_records is not None}")
                except Exception as e:
                    logger.debug(f"Error querying historical collection for DAM: {e}")
                    dam_records = None
            
            # If still no exact match, try date-only matching for DAM
            if not dam_records:
                scheduled_date = scheduled_at.date() if hasattr(scheduled_at, 'date') else scheduled_at
                dam_date_query = {
                    "scheduled_at": {"$gte": datetime.combine(scheduled_date, datetime.min.time()),
                           "$lt": datetime.combine(scheduled_date, datetime.min.time()) + timedelta(days=1)},
                    "time_block": time_blocks,
                    "market_type": "DAM"
                }
                logger.debug(f"DAM Date Range Query: {dam_date_query}")
                
                # Try date range in main collection first
                dam_records = await self.exchange_repository.find_one(dam_date_query)
                logger.debug(f"DAM Records Found (main date range): {dam_records is not None}")
                
                # If still not found, try date range in historical collection
                if not dam_records:
                    try:
                        dam_records = await self.exchange_repository.historical_collection.find_one(dam_date_query)
                        logger.debug(f"DAM Records Found (historical date range): {dam_records is not None}")
                    except Exception as e:
                        logger.debug(f"Error querying historical collection for DAM date range: {e}")
                        dam_records = None
            
            # Process RTM data (handle None case and correct field names)
            if rtm_records:
                # Use correct field name for MCP
                rtm_w1 = rtm_records.get("acp", 0.0) or rtm_records.get("mcp", 0.0)
            else:
                rtm_w1 = 0.0
                logger.debug(f"No RTM data found for Date: {scheduled_at}, Time Block: {time_blocks}")
            
            rtm_data = {
                "W1": rtm_w1,  # Use ACP, fallback to MCP
                "time_block": time_blocks
            }

            # Process DAM data (handle None case and correct field names)
            if dam_records:
                # Use correct field name for MCP
                dam_w1 = dam_records.get("acp", 0.0) or dam_records.get("mcp", 0.0)
            else:
                dam_w1 = 0.0
                logger.debug(f"No DAM data found for Date: {scheduled_at}, Time Block: {time_blocks}")
            dam_data = {
                "W1": dam_w1,  # Use ACP, fallback to MCP
                "time_block": time_blocks
            }

            # Process ACP value (handle None cases and correct field names)
            acp_value = 0.0
            if rtm_records and rtm_records.get("acp", 0) > 0:
                acp_value = rtm_records.get("acp", 0)
            elif dam_records and dam_records.get("acp", 0) > 0:
                acp_value = dam_records.get("acp", 0)
            elif rtm_records and rtm_records.get("mcp", 0) > 0:
                acp_value = rtm_records.get("mcp", 0)
            elif dam_records and dam_records.get("mcp", 0) > 0:
                acp_value = dam_records.get("mcp", 0)
            else:
                acp_value = 100.0  # Default ACP if no data found

            return {
                "rtm_data": rtm_data,
                "dam_data": dam_data,
                "acp_values": [acp_value]
            }
            
        except Exception as e:  
            logger.error(f"Error fetching exchange data for DSM calculation: {str(e)}")
            # Return empty data with default ACP
            return {
                "rtm_data": {},
                "dam_data": {},
                "acp_values": [100.0]  # Default ACP
            }


    async def _fetch_exchange_data_for_dsm(self, scheduled_at: datetime, time_blocks: List[int]) -> Dict[str, Any]:
        """
        Fetch exchange data (RTM and DAM) for DSM calculation based on scheduled_at and time_blocks.
        Optimized: Fetches ALL data in one go and matches in-memory.
        
        Args:
            scheduled_at: The scheduled date for which to fetch exchange data
            time_blocks: List of time blocks to fetch data for
            
        Returns:
            Dict containing RTM and DAM area price data and ACP values
        """
        try:
            logger.info(f"Fetching exchange data for DSM calculation (Batch) - scheduled_at: {scheduled_at}")
            
            # Fetch ALL RTM and DAM data for this date in one query
            # Assuming exchange_repository has a method to fetch by date/market, if not we use find_many directly
            # We need start/end of day
            start_date = scheduled_at.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = start_date + timedelta(days=1)
            
            query = {
                "scheduled_at": {"$gte": start_date, "$lt": end_date},
                "market_type": {"$in": ["RTM", "DAM"]},
                "time_block": {"$in": time_blocks}
            }
            
            # AWAIT the async find_many cursor builder and resolve to a list
            all_records = await self.exchange_repository.find_many(query, limit=1000)
            
            # Index by (time_block, market_type)
            exchange_map = {}
            for rec in all_records:
                tb = rec.get("time_block")
                mt = rec.get("market_type")
                if tb and mt:
                    exchange_map[(tb, mt)] = rec

            # Fetch RTM data
            rtm_data = []
            dam_data = []
            acp_values = []
            
            for time_block in time_blocks:
                # Get RTM from map
                rtm_record = exchange_map.get((time_block, "RTM"), {})
                rtm_w1 = rtm_record.get("acp", 0.0) or rtm_record.get("mcp", 0.0)
                rtm_data.append({
                    "W1": rtm_w1,
                    "time_block": time_block
                })
                
                # Get DAM from map
                dam_record = exchange_map.get((time_block, "DAM"), {})
                dam_w1 = dam_record.get("acp", 0.0) or dam_record.get("mcp", 0.0)
                dam_data.append({
                    "W1": dam_w1,
                    "time_block": time_block
                })
                
                # Get ACP for this time block (prefer RTM ACP, fallback to DAM ACP, then MCP)
                acp_value = 0.0
                if rtm_record.get("acp", 0) > 0:
                    acp_value = rtm_record.get("acp", 0)
                elif dam_record.get("acp", 0) > 0:
                    acp_value = dam_record.get("acp", 0)
                elif rtm_record.get("mcp", 0) > 0:
                    acp_value = rtm_record.get("mcp", 0)
                elif dam_record.get("mcp", 0) > 0:
                    acp_value = dam_record.get("mcp", 0)
                else:
                    acp_value = 100.0  # Default ACP if no data found
                
                acp_values.append(acp_value)
            
            logger.info(f"Fetched batch exchange data - Matched {len(all_records)} records")
            
            return {
                "rtm_data": rtm_data,
                "dam_data": dam_data,
                "acp_values": acp_values
            }
            
        except Exception as e:
            logger.error(f"Error fetching exchange data for DSM calculation: {str(e)}")
            # Return empty data with default ACP
            return {
                "rtm_data": [],
                "dam_data": [],
                "acp_values": [100.0] * len(time_blocks)  # Default ACP
            }
    
    async def execute_cron_job(self) -> Dict[str, Any]:
        """
        Execute demand data processing as a cron job with correlation ID
        """
        job_type = "demand_data_processing"
        job_name = "Demand Data Processing"
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
                "data_type": "demand",
            }
        
        # Set execution_id as correlation ID using base processor method
        self.set_correlation_id(execution_id)
        
        logger.info("=== EXECUTING DEMAND DATA PROCESSING JOB ===")
        
        try:
            # Create metadata for demand data processing with correlation ID
            metadata = {
                "data_type": "demand",
                "scraped_at": start_time,
                "source": "demand_processor_cron",
                "triggered_by": "cron_job",
                "execution_id": execution_id,
                "correlation_id": execution_id
            }
            
            # Process the metadata trigger - returns a detailed result dict
            trigger_res = await self.process_metadata_trigger(metadata)
            success = trigger_res.get("success", False)
            
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            
            # Record job completion
            result = {
                "status": "success" if success else "error",
                "message": "Demand data processed successfully" if success else "Demand data processing failed",
                "execution_time": execution_time,
                "data_type": "demand",
                "correlation_id": execution_id,
                "cron_job":"demand",
                "trigger_details": trigger_res
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
            
            if success:
                logger.info("Demand data processing completed successfully in %.2fs", execution_time)
            else:
                logger.error("Demand data processing failed after %.2fs", execution_time)
                
            return result
                
        except Exception as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            error_result = {
                "status": "error",
                "message": f"Demand data processing error: {str(e)}",
                "execution_time": execution_time,
                "data_type": "demand",
                "correlation_id": execution_id
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
            
            logger.error("Error executing demand data processing job: %s", str(e))
            return error_result
        finally:
            # Clear correlation ID after job completion
            self.clear_correlation_id()
    
    async def execute_archival_job(self) -> Dict[str, Any]:
        """
        Execute demand data archival as a separate cron job with its own correlation ID
        Moves data older than or equal to today-2 into historical (keeps yesterday,today,tomorrow in serve)
        """
        job_type = "demand_serve_archival"
        job_name = "Demand Data Archival"
        schedule = "0 2 * * *"  # Daily at 2 AM
        start_time = datetime.now()
        
        # Generate and record job start to get execution_id (correlation ID)
        execution_id = await self._record_job_start(job_type, job_name, schedule, start_time)
        if not execution_id:
            logger.error("Failed to record job start for %s", job_type)
            return {
                "status": "error",
                "message": "Failed to record job start",
                "execution_time": 0.0,
                "data_type": "demand"
            }
        
        # Set execution_id as correlation ID using base processor method
        self.set_correlation_id(execution_id)
        
        logger.info("=== EXECUTING DEMAND DATA ARCHIVAL JOB ===")
        
        try:
            # Archive old demand data based on new 3-day active window (yesterday,today,tomorrow)
            archive_result = await self.demand_repository.archive_old_demand_data(days_to_keep=2)
            
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            
            if archive_result.get("error"):
                error_result = {
                    "status": "error",
                    "message": f"Demand data archival error: {archive_result['error']}",
                    "execution_time": execution_time,
                    "data_type": "demand",
                    "correlation_id": execution_id
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
                    error_message=archive_result["error"]
                )
                
                logger.error("Demand data archival failed: %s", archive_result["error"])
                return error_result
            else:
                archived_count = archive_result.get("archived_count", 0)
                success_result = {
                    "status": "success",
                    "message": f"Archived {archived_count} demand records",
                    "execution_time": execution_time,
                    "archived_count": archived_count,
                    "cutoff_date": archive_result.get("cutoff_date"),
                    "data_type": "demand",
                    "correlation_id": execution_id
                }
                
                await self._record_job_completion(
                    job_type=job_type,
                    job_name=job_name,
                    schedule=schedule,
                    execution_start=start_time,
                    execution_end=end_time,
                    execution_id=execution_id,
                    status="success",
                    success=True,
                    result=success_result
                )
                
                logger.info("Demand data archival completed: %d records archived in %.2fs", 
                           archived_count, execution_time)
                return success_result
                
        except Exception as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            error_result = {
                "status": "error",
                "message": f"Demand data archival error: {str(e)}",
                "execution_time": execution_time,
                "data_type": "demand",
                "correlation_id": execution_id,
                "cron_job":"demand"
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
            
            logger.error("Error executing demand data archival job: %s", str(e))
    async def _get_supply_schedule_map(self, raw_data: Any) -> Dict[Any, Dict[int, Dict[str, float]]]:
        """Fetch supply schedule data for the dates in raw_data (list of dicts or list of dates)"""
        try:
            unique_dates = set()
            
            # Handle list of dates directly
            if isinstance(raw_data, list) and raw_data and isinstance(raw_data[0], (date, datetime)):
                 for d in raw_data:
                     if isinstance(d, datetime):
                         unique_dates.add(d.date())
                     elif isinstance(d, date):
                         unique_dates.add(d)
            # Handle raw data dicts
            elif isinstance(raw_data, list):
                for item in raw_data:
                    if not isinstance(item, dict):
                        continue
                    scheduled_at = item.get("scheduled_at") or item.get("timestamp") or item.get("date_time")
                    if scheduled_at:
                        if isinstance(scheduled_at, str):
                            try:
                                dt = datetime.fromisoformat(scheduled_at.replace('Z', '+00:00'))
                                unique_dates.add(dt.date())
                            except ValueError:
                                pass
                        elif isinstance(scheduled_at, datetime):
                            unique_dates.add(scheduled_at.date())
            
            if not unique_dates:
                return {}
            
            sorted_dates = sorted(list(unique_dates))
            start_date = datetime.combine(sorted_dates[0], datetime.min.time())
            end_date = datetime.combine(sorted_dates[-1] + timedelta(days=1), datetime.min.time())
            
            aggregated_schedule = await self.supply_repository.aggregate_supply_schedule(start_date, end_date)
            
            schedule_map = {}
            if aggregated_schedule:
                for item in aggregated_schedule:
                    sch_at = item.get("scheduled_at")
                    if isinstance(sch_at, datetime):
                        date_key = sch_at.date()
                    else:
                        continue
                    
                    time_block = item.get("time_block")
                    
                    if date_key not in schedule_map:
                        schedule_map[date_key] = {}
                    
                    schedule_map[date_key][time_block] = {
                        "central": item.get("central_schedule", 0),
                        "state": item.get("state_schedule", 0)
                    }
            return schedule_map
        except Exception as e:
            logger.error(f"Error fetching supply schedule map: {e}")
            return {}

    async def process_raw_data_to_serve(
        self, 
        raw_data: List[Dict[str, Any]], 
        metadata: Dict[str, Any],
        provider_config: Dict[str, Any]
    ) -> bool:
        """
        Process raw data and convert to demand serve entities for storage using Bulk Operations.
        """
        try:
            if not raw_data:
                logger.warning("No raw data provided for processing")
                return True

            count = len(raw_data)
            logger.info(f"Processing {count} demand records (Bulk Mode).")
            
            # Process demand calculations conditionally (in memory)
            from libs.helpers.demand_calculations import process_demand_calculations_conditional, get_calculation_summary
            processed_data = process_demand_calculations_conditional(raw_data)
            
            logger.info(f"Demand calculation summary: {get_calculation_summary(raw_data)}")
            
            # Fetch supply schedule map (Bulk)
            supply_map = await self._get_supply_schedule_map(raw_data)
            
            demand_entities = []
            historical_entities = []
            serve_ok = True
            hist_ok = True
            
            # Initialize lists for bulk processing
            valid_items = []
            keys_for_supply = []
            keys_for_demand = []

            # Fetch all plants and calculate demand multiplier
            all_plants = await self.master_plants_repository.get_all_plants()
            demand_multiplier = self._calculate_demand_multiplier(all_plants)
            logger.info(f"Calculated Demand Multiplier: {demand_multiplier}")

            # Create entities for all records
            for raw_record in processed_data:
                if not isinstance(raw_record, dict): continue

                scheduled_at = raw_record.get("scheduled_at") or raw_record.get("timestamp") or raw_record.get("date_time")
                if isinstance(scheduled_at, str):
                     try: scheduled_at = datetime.fromisoformat(scheduled_at.replace('Z', '+00:00'))
                     except: continue
                
                if not scheduled_at or not isinstance(scheduled_at, datetime): continue
                
                time_block = raw_record.get("time_block")
                type_val = raw_record.get("type", "IDF")
                
                raw_record["_parsed_scheduled_at"] = scheduled_at
                
                valid_items.append(raw_record)
                
                # key for supply lookup (Plant=PPCLGPP, DC_SG=SG hardcoded in original)
                keys_for_supply.append({
                    "scheduled_at": scheduled_at,
                    "time_block": time_block,
                    "dc_sg": "SG",
                    "plant_id": "PPCLGPP"
                })
                
                # key for demand lookup
                keys_for_demand.append({
                    "scheduled_at": scheduled_at,
                    "time_block": time_block,
                    "type": type_val
                })
                # For DAF records, also fetch the corresponding IDF to sync actuals
                if type_val == "DAF":
                    keys_for_demand.append({
                        "scheduled_at": scheduled_at,
                        "time_block": time_block,
                        "type": "IDF"
                    })

            # 1. Bulk Fetch Supply Data (for Actual calculation)
            supply_data_map = {}
            if keys_for_supply:
                 # existing supply repo might not have bulk find yet? 
                 # We need to rely on generic find or the one we implemented in BaseServe?
                 # Actually BaseServe doesn't have "find_by_unique_keys" yet, DcSgRepo does.
                 # Let's perform a chunked find manually here or add to BaseServe.
                 # Since I can't easily edit BaseServe again without context switch cost, I'll do chunked query here.
                 chunk_size = 1000
                 for i in range(0, len(keys_for_supply), chunk_size):
                     chunk = keys_for_supply[i:i+chunk_size]
                     # SupplyRepo might have different collection or logic? 
                     # use self.supply_repository.serve_collection
                     query = {"$or": chunk}
                     try:
                         cursor = self.supply_repository.serve_collection.find(query)
                         try:
                             found = await cursor.to_list(length=10000)
                         finally:
                             cursor.close()
                         for f in found:
                             k = (f.get("scheduled_at"), f.get("time_block"))
                             supply_data_map[k] = f.get("volume", 0)
                     except Exception as e:
                         logger.error(f"Error bulk fetching supply: {e}")

            # 2. Bulk Fetch Existing Demand Data
            existing_demand_map = {}
            if keys_for_demand:
                 for i in range(0, len(keys_for_demand), chunk_size):
                     chunk = keys_for_demand[i:i+chunk_size]
                     query = {"$or": chunk}
                     try:
                         cursor = self.demand_repository.serve_collection.find(query)
                         try:
                             found = await cursor.to_list(length=10000)
                         finally:
                             cursor.close()
                         for f in found:
                             k = (f.get("scheduled_at"), f.get("time_block"), f.get("type"))
                             existing_demand_map[k] = f
                     except Exception as e:
                         logger.error(f"Error bulk fetching demand: {e}")

            # 3. Process Entities
            demand_entities = []
            historical_entities = []
            csv_forecast_records: List[Dict[str, Any]] = []
            today_date = datetime.now().date()
            
            for raw_record in valid_items:
                try:
                    sch_at = raw_record["_parsed_scheduled_at"]
                    tb = raw_record.get("time_block")
                    typ = raw_record.get("type", "IDF")

                    # Condition 1: Skip DAF records from upload providers if raw_data.created_at > 11:00 AM
                    if typ == "DAF":
                        ingestion_type = str(provider_config.get("ingestion_type", "")).lower()
                        raw_doc_created_at = metadata.get("raw_data", {}).get("created_at")
                        if ingestion_type == "upload" and isinstance(raw_doc_created_at, datetime):
                            if raw_doc_created_at.time() > time(11, 0):
                                logger.info(
                                    f"Skipping DAF record (upload provider, created_at={raw_doc_created_at.time()}) "
                                    f"for scheduled_at={sch_at}, block={tb}"
                                )
                                continue

                    # Condition 2: For DAF, do not update forecast for tomorrow after 11:00 AM IST
                    if typ == "DAF":
                        _tz = ZoneInfo("Asia/Kolkata")
                        _now_local = datetime.now(_tz)
                        _tomorrow_ist = _now_local.date() + timedelta(days=1)
                        if sch_at.date() == _tomorrow_ist and (_now_local.hour, _now_local.minute) >= (11, 0):
                            existing_daf = existing_demand_map.get((sch_at, tb, typ))
                            if existing_daf and existing_daf.get("forecast") is not None:
                                raw_record["forecast"] = existing_daf["forecast"]
                                logger.info(
                                    f"DAF forecast preserved for tomorrow after 11 AM IST: "
                                    f"scheduled_at={sch_at}, block={tb}, forecast={existing_daf['forecast']}"
                                )
                            else:
                                raw_record["forecast"] = None

                    # IDF CSV forecast -> forecast_serve (all rows in upload; revision per delivery date)
                    if typ == "IDF":
                        if tb is None:
                            continue

                        csv_forecast = self._parse_idf_forecast_from_raw(raw_record)
                        csv_forecast_records.append(
                            {
                                "scheduled_at": sch_at,
                                "time_block": tb,
                                "forecast": csv_forecast,
                            }
                        )
                        raw_record.pop("forecast", None)

                    # For DAF records: sync actual, drawal, rostering from existing IDF in system
                    # (IDF holds the real-time actuals; DAF and IDF share the same actual
                    #  for the same scheduled_at + time_block)
                    if typ == "DAF":
                        existing_idf = existing_demand_map.get((sch_at, tb, "IDF"))
                        if existing_idf and existing_idf.get("drawal") is not None:
                            for field in ["actual", "drawal", "rostering"]:
                                if existing_idf.get(field) is not None:
                                    raw_record[field] = existing_idf[field]

                    # Calculate Actual
                    # actual = drawal + supply_sg (plant_id=PPCLGPP, dc_sg=SG)
                    # if drawal == 0: actual = 0
                    # if drawal is None: leave actual unchanged (no drawal data received yet)
                    drawal_val = raw_record.get('drawal')
                    if drawal_val is not None:
                        if drawal_val == 0:
                            raw_record["actual"] = 0
                        else:
                            supp_vol = supply_data_map.get((sch_at, tb), 0)
                            raw_record["actual"] = drawal_val + supp_vol
                    
                    # Calculate drawal_before_loss
                    raw_record["demand_multiplier"] = demand_multiplier
                    if demand_multiplier > 0 and raw_record.get("drawal") is not None:
                        raw_record["drawal_before_loss"] = raw_record.get("drawal") / demand_multiplier
                    else:
                        raw_record["drawal_before_loss"] = 0.0
                    
                    existing_record = existing_demand_map.get((sch_at, tb, typ))
                    
                    demand_entity = None
                    if existing_record:
                        if self._has_demand_data_changed(existing_record, raw_record):
                            demand_entity = await self._create_demand_entity_with_preserved_timestamps(
                                raw_record, metadata, provider_config, existing_record, supply_map
                            )
                            if demand_entity: demand_entity.updated_flag = UpdatedFlag.UPDATED
                        else:
                            if existing_record.get("updated_flag") == UpdatedFlag.UPDATED:
                                demand_entity = await self._create_demand_entity_with_preserved_timestamps(
                                    raw_record, metadata, provider_config, existing_record, supply_map
                                )
                                if demand_entity: demand_entity.updated_flag = UpdatedFlag.OLD
                    else:
                        demand_entity = await self._create_demand_entity(raw_record, metadata, provider_config, supply_map)
                        if demand_entity: demand_entity.updated_flag = UpdatedFlag.UPDATED

                    # Routing
                    record_date = sch_at.date()
                    day_diff = (today_date - record_date).days
                    
                    if demand_entity:
                         if day_diff >= 3:
                             historical_entities.append(DemandServeHistoricalEntity(**demand_entity.model_dump(by_alias=True)))
                         else:
                             demand_entities.append(demand_entity)
                             if day_diff == 1:
                                 historical_entities.append(DemandServeHistoricalEntity(**demand_entity.model_dump(by_alias=True)))
                    
                    # Sync IDF to DAF 
                    if typ == "IDF":
                        daf_entity = await self._sync_idf_to_daf(raw_record, metadata, provider_config, supply_map)
                        if daf_entity:
                            if day_diff >= 3:
                                historical_entities.append(DemandServeHistoricalEntity(**daf_entity.model_dump(by_alias=True)))
                            else:
                                demand_entities.append(daf_entity)
                                if day_diff == 1:
                                    historical_entities.append(DemandServeHistoricalEntity(**daf_entity.model_dump(by_alias=True)))
                
                except Exception as entity_error:
                    logger.error(f"Error processing demand record in loop: {entity_error}")
                    continue

            if csv_forecast_records:
                try:
                    by_scheduled_at: Dict[datetime, List[Dict[str, Any]]] = defaultdict(list)
                    for rec in csv_forecast_records:
                        by_scheduled_at[rec["scheduled_at"]].append(rec)

                    total_upserted = total_modified = 0
                    for scheduled_at, group in by_scheduled_at.items():
                        revision = await self.forecast_repository.next_forecast_revision(
                            scheduled_at, FORECAST_SOURCE_CSV
                        )
                        for rec in group:
                            rec["forecast_revision"] = revision
                        fs_result = await self.forecast_repository.bulk_insert_forecasts(
                            group,
                            source=FORECAST_SOURCE_CSV,
                        )
                        inserted = fs_result.get("inserted", 0) or fs_result.get(
                            "upserted", 0
                        )
                        total_upserted += inserted
                        total_modified += fs_result.get("modified", 0)
                        logger.info(
                            "Forecast Serve CSV for %s: revision=%d upserted=%s modified=%s",
                            scheduled_at.strftime("%Y-%m-%d"),
                            revision,
                            inserted,
                            fs_result.get("modified"),
                        )
                    logger.info(
                        "Forecast Serve CSV bulk total: upserted=%s modified=%s",
                        total_upserted,
                        total_modified,
                    )
                except Exception as fs_err:
                    logger.error("Error bulk upserting forecast_serve (csv): %s", fs_err)

            if demand_entities:
                # Use delete-insert strategy via optimized bulk write
                # Wrap in to_thread to avoid blocking async loop with synchronous pymongo call
                res = await self.demand_repository.bulk_insert_demand_serve_with_deduplication(demand_entities)
                if res.get("error_count", 0) > 0:
                    logger.warning(f"Demand Serve Bulk Insert had errors: {res.get('errors')}")
                logger.info(f"Demand Serve Bulk Result: {res}")
            
            if historical_entities:
                # Use delete-insert strategy via optimized bulk write
                # Wrap in to_thread to avoid blocking async loop
                res = await self.demand_repository.bulk_insert_demand_historical_with_deduplication(historical_entities)
                if res.get("error_count", 0) > 0:
                    logger.warning(f"Demand Historical Bulk Insert had errors: {res.get('errors')}")
                    hist_ok = False
                logger.info(f"Demand Historical Bulk Result: {res}")

            return serve_ok and hist_ok

        except Exception as e:
            logger.error("Error processing raw data to serve: %s", str(e))
            return False
    
    async def _create_demand_entity(
        self, 
        raw_item: Dict[str, Any], 
        metadata: Dict[str, Any], 
        provider_config: Dict[str, Any],
        supply_map: Dict[Any, Dict[int, Dict[str, float]]] = None
    ) -> Optional[DemandServeEntity]:
        """
        Create a DemandServeEntity from raw data item.
        
        Args:
            raw_item: Raw data item
            metadata: Processing metadata
            provider_config: Provider configuration
            
        Returns:
            DemandServeEntity or None if creation fails
        """
        try:
            provider_metadata = provider_config.get("metadata", {})
            # Extract scheduled_at from raw data
            scheduled_at = raw_item.get("scheduled_at")
            if not scheduled_at:
                # Try alternative field names
                scheduled_at = raw_item.get("timestamp") or raw_item.get("date_time")
            
            if not scheduled_at:
                logger.warning("No scheduled_at field found in raw data")
                return None
            
            # Ensure scheduled_at is datetime
            if isinstance(scheduled_at, str):
                scheduled_at = datetime.fromisoformat(scheduled_at.replace('Z', '+00:00'))
            elif not isinstance(scheduled_at, datetime):
                logger.warning("Invalid scheduled_at format")
                return None
            
            # Extract values for calculations
            actual = raw_item.get("actual")
            forecast = raw_item.get("forecast")
            rostering = raw_item.get("rostering")
            drawal = raw_item.get("drawal")
            frequency = raw_item.get("frequency")
            forecasted_frequency = raw_item.get("forecasted_frequency")
            
            # Use safest conversion
            actual = self._safe_float(actual) if actual is not None else None
            forecast = self._safe_float(forecast) if forecast is not None else 0.0
            rostering = self._safe_float(rostering) if rostering is not None else 0.0
            drawal = self._safe_float(drawal) if drawal is not None else None
            frequency = self._safe_float(frequency) if frequency is not None else None
            forecasted_frequency = self._safe_float(forecasted_frequency) if forecasted_frequency is not None else 0.0
            time_block = self._safe_int(raw_item.get("time_block", 0))

            # Calculate schedule based on supply data if available
            schedule = self._safe_float(raw_item.get("schedule", 0.0))
            if supply_map and scheduled_at:
                date_key = scheduled_at.date()
                tb = time_block
                if date_key in supply_map and tb in supply_map[date_key]:
                    supply_data = supply_map[date_key][tb]
                    schedule = supply_data["central"] + supply_data["state"]
            

            exchange_data = await self._fetch_exchange_data_for_dsm_for_one_time_block(scheduled_at, self._safe_int(raw_item.get("time_block")))
            rtm_data = exchange_data.get("rtm_data", {})
            dam_data = exchange_data.get("dam_data", {})
            acp_values = exchange_data.get("acp_values", [100.0])[0] if exchange_data.get("acp_values") else 100.0
            
            # Calculate demand metrics using libs helper function
            from libs.helpers.demand_calculations import calculate_demand_metrics
            
            # Extract numeric values from exchange data dictionaries
            rtm_value = rtm_data.get("W1", 0.0) if isinstance(rtm_data, dict) else float(rtm_data) if rtm_data else 0.0
            dam_value = dam_data.get("W1", 0.0) if isinstance(dam_data, dict) else float(dam_data) if dam_data else 0.0
            
            calculated_metrics = calculate_demand_metrics(actual, forecast, frequency, time_block, rostering, rtm_value, dam_value, acp_values, drawal, schedule)  # pyright: ignore[reportArgumentType]
            
            # Extract source revision number from raw data
            source_revision = raw_item.get("revision_number") or raw_item.get("version") or raw_item.get("rev_no")
            # If not received in raw data, source revision = system revision
            if source_revision is None:
                source_revision = 1
            
            # Create demand entity with mapped fields and calculated metrics
            demand_entity = DemandServeEntity(
                scheduled_at=scheduled_at,
                time_block=self._safe_int(raw_item.get("time_block")),
                type=raw_item.get("type", "IDF"),
                forecast=float(forecast),
                forecasted_frequency=float(forecasted_frequency),
                actual=float(actual) if actual is not None else None,
                schedule=self._safe_float(raw_item.get("schedule")),
                drawal=self._safe_float(drawal) if drawal is not None else None,
                frequency=self._safe_float(frequency) if frequency is not None else None,
                rostering=float(rostering),
                mape=float(calculated_metrics.get("mape", 0.0)),
                mpe=float(calculated_metrics.get("mpe", 0.0)),
                source_actual=str(raw_item.get("source_actual", "")),
                source_forecast=str(raw_item.get("source_forecast", "")),
                source_rostering=str(raw_item.get("source_rostering", "")),
                zone=raw_item.get("zone"),
                source=provider_metadata.get("source"),
                comp_act_demand=float(calculated_metrics.get("comp_act_demand", 0.0)),
                udm=self._safe_float(raw_item.get("udm", 0.0)),
                comp_udm=float(calculated_metrics.get("comp_udm", 0.0)),
                updated_flag=UpdatedFlag.UPDATED,
                # Revision tracking
                source_revision_number=source_revision,
                system_revision_number=1  # New record starts at 1
            )
            
            return demand_entity
            
        except Exception as e:
            logger.error("Error creating demand entity: %s", str(e))
            return None
    
    async def _create_demand_entity_with_preserved_timestamps(
        self, 
        raw_item: Dict[str, Any], 
        metadata: Dict[str, Any], 
        provider_config: Dict[str, Any],
        existing_record: Dict[str, Any],
        supply_map: Dict[Any, Dict[int, Dict[str, float]]] = None
    ) -> Optional[DemandServeEntity]:
        """
        Create a DemandServeEntity from raw data item with preserved timestamps from existing record.
        This ensures that updated records maintain their original inserted_at timestamp for proper deduplication.
        
        Args:
            raw_item: Raw data item
            metadata: Processing metadata
            provider_config: Provider configuration
            existing_record: Existing record to preserve timestamps from
            
        Returns:
            DemandServeEntity or None if creation fails
        """
        try:
            provider_metadata = provider_config.get("metadata", {})
            # Extract scheduled_at from raw data
            scheduled_at = raw_item.get("scheduled_at")
            if not scheduled_at:
                # Try alternative field names
                scheduled_at = raw_item.get("timestamp") or raw_item.get("date_time")
            
            if not scheduled_at:
                logger.warning("No scheduled_at field found in raw data")
                return None
            
            # Ensure scheduled_at is datetime
            if isinstance(scheduled_at, str):
                scheduled_at = datetime.fromisoformat(scheduled_at.replace('Z', '+00:00'))
            elif not isinstance(scheduled_at, datetime):
                logger.warning("Invalid scheduled_at format")
                return None
            
            # Extract values for calculations
            # Helper functions to merge data
            def get_val(key, default=0.0):
                if key in raw_item and raw_item[key] is not None:
                    return self._safe_float(raw_item[key])
                val = existing_record.get(key)
                return self._safe_float(val) if val is not None else default

            def get_str_val(key, default=""):
                if key in raw_item:
                    return str(raw_item[key]) if raw_item[key] is not None else default
                return str(existing_record.get(key) or default)

            # Extract values for calculations
            actual = get_val("actual", None)
            forecast = get_val("forecast")
            rostering = get_val("rostering")
            drawal = get_val("drawal", None)
            frequency = get_val("frequency", None)
            forecasted_frequency = get_val("forecasted_frequency")
            udm = get_val("udm")
            data_type = raw_item.get("type", existing_record.get("type", "IDF"))
            
            # Zone handling
            zone = None
            if "zone" in raw_item:
                zone = raw_item["zone"]
            elif "zone" in existing_record:
                zone = existing_record.get("zone")

            # Source fields
            source_actual = get_str_val("source_actual")
            source_forecast = get_str_val("source_forecast")
            source_rostering = get_str_val("source_rostering")
            
            # Calculate schedule based on supply data if available
            if "schedule" in raw_item:
                schedule = self._safe_float(raw_item["schedule"])
            else:
                schedule = float(existing_record.get("schedule") or 0.0)

            if supply_map and scheduled_at:
                date_key = scheduled_at.date()
                tb = self._safe_int(raw_item.get("time_block", 0))
                if date_key in supply_map and tb in supply_map[date_key]:
                    supply_data = supply_map[date_key][tb]
                    schedule = supply_data["central"] + supply_data["state"]
            
            time_block = self._safe_int(raw_item.get("time_block", 0))

            # get exchange data for calculating dsm rate and net dsm amount
            exchange_data = await self._fetch_exchange_data_for_dsm_for_one_time_block(scheduled_at, self._safe_int(raw_item.get("time_block")))
            rtm_data = exchange_data.get("rtm_data", {})
            dam_data = exchange_data.get("dam_data", {})
            acp_values = exchange_data.get("acp_values", [100.0])[0] if exchange_data.get("acp_values") else 100.0
            # Calculate demand metrics using libs helper function
            from libs.helpers.demand_calculations import calculate_demand_metrics
            
            # Extract numeric values from exchange data dictionaries
            rtm_value = rtm_data.get("W1", 0.0) if isinstance(rtm_data, dict) else float(rtm_data) if rtm_data else 0.0
            dam_value = dam_data.get("W1", 0.0) if isinstance(dam_data, dict) else float(dam_data) if dam_data else 0.0
            
            calculated_metrics = calculate_demand_metrics(actual, forecast, frequency, time_block, rostering, rtm_value, dam_value, acp_values, drawal, schedule)  # pyright: ignore[reportArgumentType]
            
            # Preserve timestamps from existing record
            current_time = datetime.now()
            inserted_at = existing_record.get("inserted_at") or current_time
            created_at = existing_record.get("created_at") or current_time
            
            # Extract source revision number from raw data
            source_revision = raw_item.get("revision_number") or raw_item.get("version") or raw_item.get("rev_no")
            
            # Increment system revision number (handles migration: 0 -> 1 for old records)
            existing_system_revision = existing_record.get("system_revision_number", 0)
            new_system_revision = existing_system_revision + 1
            
            # If not received in raw data, source revision = system revision
            if source_revision is None:
                # Check if existing record has source revision, otherwise use new system revision
                existing_source_revision = existing_record.get("source_revision_number")
                if existing_source_revision is not None:
                    source_revision = existing_source_revision
                else:
                    source_revision = new_system_revision

            # Create demand entity with mapped fields, calculated metrics, and preserved timestamps
            demand_entity = DemandServeEntity(
                scheduled_at=scheduled_at,
                time_block=time_block,
                type=data_type,
                forecast=float(forecast),
                forecasted_frequency=float(forecasted_frequency),
                actual=float(actual) if actual is not None else None,
                schedule=float(schedule),
                drawal=float(drawal) if drawal is not None else None,
                frequency=float(frequency) if frequency is not None else None,
                rostering=float(rostering),
                mape=float(calculated_metrics.get("mape", 0.0)),
                mpe=float(calculated_metrics.get("mpe", 0.0)),
                source_actual=source_actual,
                source_forecast=source_forecast,
                source_rostering=source_rostering,
                zone=zone,
                source=provider_metadata.get("source"),
                comp_act_demand=float(calculated_metrics.get("comp_act_demand", 0.0)),
                udm=float(udm),
                comp_udm=float(calculated_metrics.get("comp_udm", 0.0)),
                dsm_rate=float(existing_record.get("dsm_rate") or 0.0),
                net_dsm_amount=float(existing_record.get("net_dsm_amount") or 0.0),
                updated_flag=UpdatedFlag.UPDATED,
                # Preserve original timestamps
                inserted_at=inserted_at,
                created_at=created_at,
                # Update these timestamps
                updated_at=current_time,
                timestamp=current_time,
                # Revision tracking
                source_revision_number=source_revision,
                system_revision_number=new_system_revision  # Increment on update
            )
            
            return demand_entity
            
        except Exception as e:
            logger.error("Error creating demand entity with preserved timestamps: %s", str(e))
            return None
    
    def _has_demand_data_changed(self, existing_record: Dict[str, Any], new_record: Dict[str, Any]) -> bool:
        """
        Check if demand data has changed by comparing key fields that exist in raw data
        
        Args:
            existing_record: Existing demand record from database
            new_record: New demand record from raw data
            
        Returns:
            bool: True if data has changed
        """
        # List of fields to compare for changes - only fields that exist in raw data
        # Exclude calculated fields like mape, mpe, comp_act_demand, comp_udm as they don't exist in raw data
        compare_fields = [
            "forecast", "actual", "schedule", "drawal", "frequency", "rostering", "udm", "zone", 
            "source_actual", "source_forecast", "source_rostering", "forecasted_frequency"
        ]
        
        for field in compare_fields:
            existing_value = existing_record.get(field)
            new_value = new_record.get(field)
            
            # Skip comparison if new_value is None (field doesn't exist in raw data)
            if new_value is None:
                continue
                
            # Handle None values and type conversions
            if existing_value != new_value:
                # Handle float comparison with tolerance
                if isinstance(existing_value, (int, float)) and isinstance(new_value, (int, float)):
                    if abs(float(existing_value) - float(new_value)) > 0.001:  # Small tolerance for float comparison
                        logger.debug(f"Data changed in field '{field}': existing={existing_value}, new={new_value}")
                        return True
                else:
                    logger.debug(f"Data changed in field '{field}': existing={existing_value}, new={new_value}")
                    return True
        
        return False
        
    async def _sync_idf_to_daf(
        self, 
        idf_raw_item: Dict[str, Any], 
        metadata: Dict[str, Any], 
        provider_config: Dict[str, Any],
        supply_map: Dict[Any, Dict[int, Dict[str, float]]] = None
    ) -> Optional[DemandServeEntity]:
        """
        Sync IDF data to DAF by populating actual, drawal, and rostering fields.
        Other DAF fields like forecast are preserved from the existing DAF record if it exists.
        
        Args:
            idf_raw_item: IDF raw data item
            metadata: Processing metadata
            provider_config: Provider configuration
            supply_map: Optional supply schedule map
            
        Returns:
            DAF DemandServeEntity or None if sync failed
        """
        try:
            # Extract basic info
            scheduled_at = idf_raw_item.get("scheduled_at")
            if not scheduled_at:
                return None
                
            if isinstance(scheduled_at, str):
                scheduled_at = datetime.fromisoformat(scheduled_at.replace('Z', '+00:00'))
            
            time_block = self._safe_int(idf_raw_item.get("time_block"))
            
            # Find existing DAF record to preserve its fields (like forecast)
            existing_daf = await self.demand_repository.find_by_unique_key(
                scheduled_at=scheduled_at,
                time_block=time_block,
                type="DAF"
            )
            
            # Prepare data for DAF creation/update
            # We want to use IDF's actual, drawal, rostering but DAF's forecast
            daf_raw_data = idf_raw_item.copy()
            daf_raw_data["type"] = "DAF"
            
            if existing_daf:
                # Prioritize existing DAF fields for non-actuals/drawal/rostering
                # These fields are what we bring from IDF as per request
                sync_fields = ["actual", "drawal", "rostering"] # including frequency as it's part of real-time state
                
                for key, value in existing_daf.items():
                    if key not in sync_fields and key not in ["_id", "type", "inserted_at", "created_at", "updated_at", "timestamp"]:
                        # Keep DAF value for fields like forecast, zone, frequency, source, etc.
                        if value is not None:
                            daf_raw_data[key] = value
                
                # Ensure fields from IDF are updated in the merged data
                for field in sync_fields:
                    if field in idf_raw_item:
                        daf_raw_data[field] = idf_raw_item[field]
                
                # Create entity with preserved timestamps from existing DAF
                # This call will re-calculate metrics (mape, mpe, etc.) using merged data
                return await self._create_demand_entity_with_preserved_timestamps(
                    daf_raw_data, metadata, provider_config, existing_daf, supply_map
                )
            else:
                # If DAF doesn't exist, create it from IDF data but labeled as DAF
                # This will populate it with the actuals we have
                return await self._create_demand_entity(
                    daf_raw_data, metadata, provider_config, supply_map
                )
                
        except Exception as e:
            logger.error(f"Error syncing IDF to DAF: {str(e)}")
            return None


    @staticmethod
    def _safe_float(value: Any) -> float:
        """Safely convert value to float"""
        if value is None:
            return 0.0
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0
    
    @staticmethod
    def _safe_int(value: Any) -> Optional[int]:
        """Safely convert value to int"""
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None
    

