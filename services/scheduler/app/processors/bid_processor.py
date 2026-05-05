"""
Bid data processor for handling bid trade data processing logic.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

from .base_processor import BaseProcessor
from ..repository.bid_trade_serve_repository import BidTradeServeRepository
from libs.models.bid_trade_serve_entity import BidTradeServeEntity, BidTradeServeHistoricalEntity
from libs.enum.updated_flag import UpdatedFlag
from libs.database.base_serve_repository import DataInsertionError
from libs.core.job_entities import JobDetail

logger = logging.getLogger(__name__)


class BidProcessor(BaseProcessor):
    """
    Bid data processor that handles bid trade data processing and storage.
    """

    def __init__(self):
        super().__init__()
        self.repository = BidTradeServeRepository()

        # Preload timeblock mappings for better performance using base processor's caching
        self.preload_timeblock_mappings()

    async def execute_cron_job(self) -> Dict[str, Any]:
        """
        Execute bid data processing as a cron job with correlation ID
        """
        job_type = "bid_data_processing"
        job_name = "Bid Data Processing"
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
                "data_type": "bid"
            }

        # Set execution_id as correlation ID using base processor method
        self.set_correlation_id(execution_id)

        logger.info("=== EXECUTING BID DATA PROCESSING JOB ===")

        try:
            # Create metadata for bid data processing with correlation ID
            metadata = {
                "data_type": "bid",
                "scraped_at": start_time,
                "source": "bid_processor_cron",
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
                "message": "Bid data processed successfully" if success else "Bid data processing failed",
                "execution_time": execution_time,
                "data_type": "bid",
                "correlation_id": execution_id,
                "cron_job":"bid",
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
                logger.info("Bid data processing completed successfully in %.2fs", execution_time)
            else:
                logger.error("Bid data processing failed after %.2fs", execution_time)

            return result

        except Exception as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            error_result = {
                "status": "error",
                "message": f"Bid data processing error: {str(e)}",
                "execution_time": execution_time,
                "data_type": "bid",
                "correlation_id": execution_id,
                "cron_job":"bid"
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

            logger.error("Error executing bid data processing job: %s", str(e))
            return error_result
        finally:
            # Clear correlation ID after job completion
            self.clear_correlation_id()

    async def process_raw_data_to_serve(
    self,
    raw_data: List[Dict[str, Any]],
    metadata: Dict[str, Any],
    provider_config: Dict[str, Any]
    ) -> bool:
        try:
            if not raw_data:
                logger.warning("No raw data provided for processing")
                return False
            logger.info(f"Processing {len(raw_data)} bid trade records with updated flag logic")

            bid_entities = []
            historical_buffer: List[Dict[str, Any]] = []
            processed_items: List[Dict[str, Any]] = []

            # --- FIX: Define a clear 4-day window for the serve collection ---
            today = datetime.now().date()
            serve_dates = {
                today,
                today + timedelta(days=1),   # Tomorrow
                today - timedelta(days=1),   # Yesterday
                today - timedelta(days=2)    # Day before yesterday
            }
            historical_cutoff_date = today - timedelta(days=3) # Anything older is historical

            for item in raw_data:
                if not isinstance(item, dict):
                    logger.warning("Skipping non-dict item in raw data")
                    continue
                try:
                    # (Normalization logic remains the same...)
                    if "scheduled_at" not in item and "Scheduled_at" in item:
                        sch_val = item.get("Scheduled_at")
                        if isinstance(sch_val, datetime): 
                            item["scheduled_at"] = sch_val
                        else:
                            # Only accept YYYY-MM-DD format from trading parser
                            try: 
                                parsed = datetime.strptime(str(sch_val), "%Y-%m-%d")
                                item["scheduled_at"] = parsed
                            except ValueError:
                                logger.warning(f"Invalid date format in Scheduled_at field: {sch_val}. Expected YYYY-MM-DD format.")
                                item["scheduled_at"] = datetime.now()
                    if "type" in item and isinstance(item["type"], str): item["type"] = item["type"].upper()
                    if "bidded" not in item and "Bidded" in item: item["bidded"] = item.get("Bidded")
                    if "traded" not in item and "Traded" in item: item["traded"] = item.get("Traded")
                    if "time_block" not in item:
                        ts = item.get("Time_Start") or item.get("time_start")
                        te = item.get("Time_End") or item.get("time_end")
                        if ts and te:
                            timeblock_data = await self.get_timeblock_by_time_range(str(ts).strip(), str(te).strip())
                            if timeblock_data:
                                item["time_block"] = timeblock_data.get("timeblock")
                            else:
                                time_range = f"{str(ts).strip()}-{str(te).strip()}"
                                timeblock_num = await self._get_timeblock_number(time_range)
                                if timeblock_num > 0:
                                    item["time_block"] = timeblock_num

                    scheduled_at = item.get("scheduled_at")
                    if isinstance(scheduled_at, str):
                        # Only accept YYYY-MM-DD format from trading parser
                        try: 
                            parsed_dt = datetime.strptime(scheduled_at, "%Y-%m-%d")
                            scheduled_at = parsed_dt
                            item["scheduled_at"] = scheduled_at
                        except ValueError:
                            logger.warning(f"Invalid date format in scheduled_at field: {scheduled_at}. Expected YYYY-MM-DD format.")
                            scheduled_at = datetime.now()
                            item["scheduled_at"] = scheduled_at

                    if not isinstance(scheduled_at, datetime):
                        logger.warning(f"Skipping record with non-datetime scheduled_at: {scheduled_at}")
                        continue
                    
                    sch_date = scheduled_at.date()
                    time_block = item.get("time_block")
                    bid_type = item.get("type") or item.get("market_type")

                    # Validate timeblock is a valid integer
                    if time_block is not None:
                        try:
                            time_block = int(time_block)
                            if not (1 <= time_block <= 96):
                                logger.warning(f"Invalid timeblock {time_block} in bid record, must be 1-96")
                                continue
                        except (ValueError, TypeError):
                            logger.warning(f"Invalid timeblock value {time_block} in bid record")
                            continue

                    if not all([scheduled_at, time_block, bid_type]):
                        logger.error(f"Missing required fields in bid record: scheduled_at={scheduled_at}, time_block={time_block}, type={bid_type}")
                        continue

                    # Buffer processed items for bulk handling
                    # Store tuple: (sch_date, time_block_int, bid_type_str, item, calculated_value, source_revision)
                    if sch_date in serve_dates:
                        time_block_int = int(time_block)
                        bid_type_str = str(bid_type).upper()
                        traded = float(item.get("traded") or 0)
                        calculated_value = await self._calculate_value_from_exchange(scheduled_at, time_block_int, bid_type_str, traded)
                        
                        source_revision = item.get("revision_number") or item.get("version") or item.get("rev_no")
                        
                        processed_items.append({
                            "scheduled_at": scheduled_at,
                            "time_block": time_block_int,
                            "type": bid_type_str,
                            "item": item,
                            "calculated_value": calculated_value,
                            "source_revision": source_revision
                        })
                    
                    elif sch_date <= historical_cutoff_date:
                        historical_buffer.append(item.copy())

                except Exception as entity_error:
                    logger.error(f"Error processing bid record {item}: {entity_error}")
                    continue
            
            # --- Bulk Processing for Serve Data ---
            if processed_items:
                # 1. Bulk Fetch Existing Records
                unique_keys = [{
                    "scheduled_at": p["scheduled_at"],
                    "time_block": p["time_block"],
                    "type": p["type"]
                } for p in processed_items]
                
                existing_records_list = await self.repository.find_by_unique_keys(unique_keys)
                
                # Create map for fast lookup: (date_str, time_block, type) -> record
                existing_map = {}
                for rec in existing_records_list:
                    # Normalize key
                    r_date = rec["scheduled_at"].date() if isinstance(rec["scheduled_at"], datetime) else rec["scheduled_at"]
                    key = (r_date, rec["time_block"], rec["type"])
                    existing_map[key] = rec
                
                # 2. Process Items and Prepare Bulk Ops
                bulk_ops = []
                
                for p in processed_items:
                    scheduled_at = p["scheduled_at"]
                    sch_date = scheduled_at.date()
                    time_block = p["time_block"]
                    bid_type = p["type"]
                    item = p["item"]
                    calculated_value = p["calculated_value"]
                    source_revision = p["source_revision"]
                    
                    key = (sch_date, time_block, bid_type)
                    existing_record = existing_map.get(key)
                    
                    op = None
                    
                    if existing_record:
                        # Logic matches original: increment system revision
                        existing_system_revision = existing_record.get("system_revision_number", 0)
                        system_revision = existing_system_revision + 1
                        
                        if source_revision is None:
                            existing_source_revision = existing_record.get("source_revision_number")
                            source_revision = existing_source_revision if existing_source_revision is not None else system_revision
                        
                        if await self._has_bid_data_changed(existing_record, item, calculated_value):
                            # Changed: Need update (Delete + Insert)
                            bid_entity = BidTradeServeEntity.from_raw_data(item, metadata, provider_config, calculated_value)
                            bid_entity.updated_flag = UpdatedFlag.UPDATED
                            bid_entity.source_revision_number = source_revision
                            bid_entity.system_revision_number = system_revision
                            
                            op = {
                                "filter": {"_id": existing_record["_id"]}, # Delete specific existing doc
                                "document": bid_entity
                            }
                            
                        elif existing_record.get("updated_flag") == UpdatedFlag.UPDATED:
                            # Not changed, but currently UPDATED -> reset to OLD (Delete + Insert)
                            bid_entity = BidTradeServeEntity.from_raw_data(item, metadata, provider_config, calculated_value)
                            bid_entity.updated_flag = UpdatedFlag.OLD
                            bid_entity.source_revision_number = source_revision
                            bid_entity.system_revision_number = system_revision
                            
                            op = {
                                "filter": {"_id": existing_record["_id"]},
                                "document": bid_entity
                            }
                        # Else: No change and flag is OLD -> No Op
                    else:
                        # New record
                        if source_revision is None:
                            source_revision = 1
                        
                        bid_entity = BidTradeServeEntity.from_raw_data(item, metadata, provider_config, calculated_value)
                        bid_entity.updated_flag = UpdatedFlag.UPDATED
                        bid_entity.source_revision_number = source_revision
                        bid_entity.system_revision_number = 1
                        
                        op = {
                            # No filter means pure insert? 
                            # But wait, bulk_upsert_custom takes 'filter' for DeleteOne.
                            # If we want insert without delete, we check duplicates?
                            # unique key is (scheduled_at, time_block, type).
                            # We can provide filter to ensure we delete any phantom duplicates, or just None.
                            # Given we checked existing_map, we know there's no match found by find_by_unique_keys.
                            # But let's be safe and provide filter by unique key to DeleteOne just in case?
                            # Or just insert.
                            # bulk_upsert_custom adds DeleteOne if 'filter' present, InsertOne if 'document' present.
                            # For new record, we only need InsertOne.
                            "document": bid_entity
                        }

                    if op:
                        bulk_ops.append(op)

                # 3. Execute Bulk Ops
                if bulk_ops:
                    try:
                        res = await self.repository.bulk_upsert_custom(bulk_ops)
                        logger.info(f"Bulk processed bid trade serve entities: {res}")
                    except Exception as e:
                         # Re-raise as DataInsertionError for consistent handling
                         raise DataInsertionError(f"Bulk upsert error: {str(e)}") from e
                else:
                    logger.info("No bid entities to store (all records unchanged)")
            else:
                logger.info("No serve data items processed")

            if historical_buffer:
                logger.info(f"Processing {len(historical_buffer)} historical records.")
                latest_entry = await self.repository.historical_collection.find_one(sort=[("scheduled_at", -1)])
                latest_date_in_db = latest_entry['scheduled_at'].date() if latest_entry else None
                logger.info(f"Latest date in historical DB is: {latest_date_in_db}")
                to_insert_new = []
                to_replace_latest_day = []
                for rec in historical_buffer:
                    rec_date = rec['scheduled_at'].date()
                    if latest_date_in_db is None: to_insert_new.append(rec)
                    elif rec_date > latest_date_in_db: to_insert_new.append(rec)
                    elif rec_date == latest_date_in_db: to_replace_latest_day.append(rec)
                if to_replace_latest_day:
                    logger.info(f"Replacing {len(to_replace_latest_day)} records for date {latest_date_in_db}")
                    start_of_day = datetime.combine(latest_date_in_db, datetime.min.time())
                    end_of_day = datetime.combine(latest_date_in_db, datetime.max.time())
                    await self.repository.historical_collection.delete_many({"scheduled_at": {"$gte": start_of_day, "$lte": end_of_day}})
                    to_insert_new.extend(to_replace_latest_day)
                if to_insert_new:
                    historical_entities_to_insert = []
                    for rec_dict in to_insert_new:
                        if "time_block" not in rec_dict:
                            ts = rec_dict.get("Time_Start") or rec_dict.get("time_start")
                            te = rec_dict.get("Time_End") or rec_dict.get("time_end")
                            if ts and te:
                                timeblock_data = await self.get_timeblock_by_time_range(str(ts).strip(), str(te).strip())
                                if timeblock_data:
                                    rec_dict["time_block"] = timeblock_data.get("timeblock")
                                else:
                                    time_range = f"{str(ts).strip()}-{str(te).strip()}"
                                    timeblock_num = await self._get_timeblock_number(time_range)
                                    rec_dict["time_block"] = timeblock_num if timeblock_num > 0 else 0
                        calc_val = await self._calculate_value_from_exchange(rec_dict["scheduled_at"], rec_dict["time_block"], rec_dict["type"], rec_dict.get("traded", 0))
                        serve_ent = BidTradeServeEntity.from_raw_data(rec_dict, metadata, provider_config, calc_val)
                        
                        # Add revision tracking for historical records
                        source_revision = rec_dict.get("revision_number") or rec_dict.get("version") or rec_dict.get("rev_no")
                        # If not received in raw data, source revision = system revision
                        if source_revision is None:
                            source_revision = 1
                        serve_ent.source_revision_number = source_revision
                        serve_ent.system_revision_number = 1  # Historical records start at 1
                        
                        historical_entities_to_insert.append(BidTradeServeHistoricalEntity(**serve_ent.dict()))
                    if historical_entities_to_insert:
                        await self.repository.bulk_insert_bid_trade_historical_with_deduplication(historical_entities_to_insert)
                        logger.info(f"Inserted/Updated {len(historical_entities_to_insert)} records in historical collection.")
                else:
                    logger.info("No new or updated historical records to process.")

            return True

        except Exception as e:
            logger.error(f"Error in process_raw_data_to_serve: {e}", exc_info=True)
            return False
            
    async def _calculate_value_from_exchange(self, scheduled_at: datetime, time_block: int,
                                         bid_type: str, traded: float) -> float:
        """
        Calculate value by looking up ACP from exchange collection
        Formula: (acp_value * traded) / 4

        Args:
            scheduled_at: Scheduled datetime
            time_block: Time block number
            bid_type: Bid type (used to determine market type for exchange lookup)
            traded: Traded amount

        Returns:
            Calculated value
        """
        try:
            # Map bid type to exchange market type (sub_type)
            # Now we look for exchange providers with matching sub_type
            exchange_market_type = bid_type.upper()  # Use bid_type directly as it matches exchange sub_type

            # Get exchange repository to lookup ACP value
            from ..repository.exchange_repository import ExchangeRepository
            exchange_repo = ExchangeRepository()

            # Find exchange record with matching criteria
            exchange_record = exchange_repo.find_by_schedule_timeblock_market(
                scheduled_at, time_block, exchange_market_type
            )

            if exchange_record:
                acp_value = exchange_record.get("acp", 0.0)
                calculated_value = (float(acp_value) * float(traded)) / 4.0
                logger.debug(f"Calculated value: ACP({acp_value}) * Traded({traded}) / 4 = {calculated_value}")
                return calculated_value
            else:
                logger.warning(f"No exchange record found for {scheduled_at} TB:{time_block} Market:{exchange_market_type}")
                return 0.0

        except Exception as e:
            logger.error(f"Error calculating value from exchange: {str(e)}")
            return 0.0

    async def _has_bid_data_changed(self, existing_record: Dict[str, Any], new_record: Dict[str, Any],
                              new_calculated_value: float) -> bool:
        """
        Check if bid data has changed by comparing key fields

        Args:
            existing_record: Existing bid record from database
            new_record: New bid record from raw data
            new_calculated_value: Newly calculated value

        Returns:
            bool: True if data has changed
        """
        # Compare key fields
        existing_bidded = existing_record.get("bidded")
        new_bidded = BidTradeServeEntity._safe_float(new_record.get("bidded"))

        existing_traded = existing_record.get("traded", 0)
        new_traded = BidTradeServeEntity._safe_float(new_record.get("traded", 0)) or 0

        existing_value = existing_record.get("value", 0)

        # Check for changes with tolerance for float comparison
        if existing_bidded != new_bidded:
            return True

        if abs(float(existing_traded) - float(new_traded)) > 0.001:
            return True

        if abs(float(existing_value) - float(new_calculated_value)) > 0.001:
            return True

        return False

    async def execute_archival_job(self) -> Dict[str, Any]:
        """
        Execute bid trade data archival as a separate cron job with its own correlation ID
        Moves data older than 3 days to historical collection
        """
        job_type = "bid_serve_archival"
        job_name = "Bid Data Archival"
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
                "data_type": "bid"
            }

        # Set execution_id as correlation ID using base processor method
        self.set_correlation_id(execution_id)

        logger.info("=== EXECUTING BID TRADE DATA ARCHIVAL JOB ===")

        try:
            # Archive old bid trade data (older than 4 days)
            archive_result = await self.repository.archive_old_bid_data(days_to_keep=4)

            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()

            if archive_result.get("error"):
                error_result = {
                    "status": "error",
                    "message": f"Bid trade data archival error: {archive_result['error']}",
                    "execution_time": execution_time,
                    "data_type": "bid",
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

                logger.error("Bid trade data archival failed: %s", archive_result["error"])
                return error_result
            else:
                archived_count = archive_result.get("archived_count", 0)
                success_result = {
                    "status": "success",
                    "message": f"Archived {archived_count} bid trade records",
                    "execution_time": execution_time,
                    "archived_count": archived_count,
                    "cutoff_date": archive_result.get("cutoff_date"),
                    "data_type": "bid",
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

                logger.info("Bid trade data archival completed: %d records archived in %.2fs",
                            archived_count, execution_time)
                return success_result

        except Exception as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            error_result = {
                "status": "error",
                "message": f"Bid trade data archival error: {str(e)}",
                "execution_time": execution_time,
                "data_type": "bid",
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

            logger.error("Error executing bid trade data archival job: %s", str(e))
            return error_result
        finally:
            # Clear correlation ID after job completion
            self.clear_correlation_id()




    


    async def _get_or_lookup_timeblock(self, record: Dict[str, Any]) -> Optional[int]:
        """
        Get timeblock from record or lookup based on time range using base processor methods

        Args:
            record: Raw record containing time information

        Returns:
            Optional[int]: Timeblock number or None if not found
        """
        # Priority 1: Check if timeblock already exists in record
        if 'time_block' in record and record['time_block'] is not None:
            try:
                timeblock = int(record['time_block'])
                logger.debug("Using existing timeblock: %d", timeblock)
                return timeblock
            except (ValueError, TypeError):
                logger.warning("Invalid timeblock value in record: %s", record.get('time_block'))

        # Priority 2: Lookup timeblock based on Time_Start and Time_End using base processor methods
        time_start = record.get('Time_Start')
        time_end = record.get('Time_End')

        if time_start and time_end:
            timeblock_data = await self.get_timeblock_by_time_range(str(time_start).strip(), str(time_end).strip())
            if timeblock_data:
                return timeblock_data.get("timeblock")
            
            time_range = f"{str(time_start).strip()}-{str(time_end).strip()}"
            timeblock_num = await self._get_timeblock_number(time_range)
            if timeblock_num > 0:
                return timeblock_num

        # Priority 3: Log error if neither method works
        logger.error("No timeblock or time range found in bid record: %s", record)
        return None