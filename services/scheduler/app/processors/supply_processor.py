"""
Supply data processor for handling supply data processing logic with a two-stage flow.
1. Raw data is validated and stored in a staging collection (dc_sg).
2. Staged data is then processed into the final time-series collection (supply_serve).
"""

import logging
import asyncio
from datetime import datetime, timedelta, time
from typing import Dict, List, Any, Optional

from .base_processor import BaseProcessor
from libs.models.supply_serve_entity import SupplyServeEntity, SupplyServeHistoricalEntity
from libs.models.demand_serve_entity import DemandServeEntity
from libs.models.dc_sg_entity import DCSgEntity
from libs.enum.updated_flag import UpdatedFlag
from libs.enum.raw_data_status import RawDataStatus
from libs.core.job_entities import JobDetail
from bson import ObjectId
from ..repository.supply_serve_repository import supply_serve_repository
from ..repository.plant_repository import plant_repository
from ..repository.dc_sg_repository import dc_sg_repository
from ..repository.demand_serve_repository import demand_serve_repository
from ..core.config import settings

logger = logging.getLogger(__name__)


class SupplyProcessor(BaseProcessor):
    """
    Supply data processor that handles supply data processing and storage with a two-stage flow:
    1. raw_data -> dc_sg (staging collection)
    2. dc_sg -> supply_serve (time-series collection with 4-day rolling window)
    """

    def __init__(self):
        super().__init__()

        self.supply_repository = supply_serve_repository
        self.plant_repository = plant_repository
        self.dc_sg_repository = dc_sg_repository
        self.demand_repository = demand_serve_repository

        from libs.core.job_factory import create_job_data_repository, create_job_detail_repository

        self.job_data_repository = create_job_data_repository(
            settings.AUDIT_DB_URI,
            settings.AUDIT_DB_NAME,
            settings.JOB_DATA_COLLECTION
        )
        self.job_detail_repository = create_job_detail_repository(
            settings.AUDIT_DB_URI,
            settings.AUDIT_DB_NAME,
            settings.JOB_DETAIL_COLLECTION
        )

    async def execute_cron_job(self) -> Dict[str, Any]:
        """
        Execute supply data processing as a cron job with correlation ID
        """
        job_type = "supply_data_processing"
        job_name = "Supply Data Processing"
        schedule = "*/15 * * * *"  # Default schedule
        start_time = datetime.now()

        execution_id = await self._record_job_start(job_type, job_name, schedule, start_time)
        if not execution_id:
            logger.error("Failed to record job start for %s", job_type)
            return {
                "status": "error",
                "message": "Failed to record job start",
                "execution_time": 0.0,
                "data_type": "supply"
            }

        self.set_correlation_id(execution_id)

        logger.info("=== EXECUTING SUPPLY DATA PROCESSING JOB ===")

        try:
            metadata = {
                "data_type": "supply",
                "scraped_at": start_time,
                "source": "supply_processor_cron",
                "triggered_by": "cron_job",
                "execution_id": execution_id,
                "correlation_id": execution_id
            }

            # New flow: process_metadata_trigger returns a detailed dict
            trigger_res = await self.process_metadata_trigger(metadata)
            success = trigger_res.get("success", False)

            # New flow: Check for missing supply data
            missing_data_success = await self._check_and_process_missing_supply_data(metadata)
            
            # New flow: Calculate Multiplier and Update DC
            multiplier_success = await self._calculate_and_apply_sg_multiplier(metadata)
            
            final_success = success or missing_data_success or multiplier_success

            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()

            result = {
                "status": "success" if final_success else "error",
                "message": "Supply data processed successfully" if final_success else "Supply data processing failed",
                "execution_time": execution_time,
                "data_type": "supply",
                "correlation_id": execution_id,
                "cron_job":"supply",
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
                success=final_success,
                result=result
            )

            return result

        except Exception as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            error_result = {
                "status": "error",
                "message": f"Supply data processing error: {str(e)}",
                "execution_time": execution_time,
                "data_type": "supply",
                "correlation_id": execution_id,
                "cron_job":"supply"
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

            logger.error("Error executing supply data processing job: %s", str(e))
            return error_result
        finally:
            self.clear_correlation_id()

    async def _check_and_process_missing_supply_data(self, metadata: Dict[str, Any]) -> bool:
        """
        Check if supply data is missing for today and fill it from historical data if needed.
        Performs a row-by-row check to ensure all blocks are present.
        """
        try:
            today = datetime.now().date()
            
            # 1. Fetch existing records for today from supply_serve and dc_sg
            # We want to skip filling if data exists in either (to avoid overwriting real data or duplicating)
            
            today_supply_records = await self.supply_repository.find_supply_serve_by_date(
                datetime.combine(today, datetime.min.time())
            )
            today_dc_sg_records = await self.dc_sg_repository.get_records_by_date(today)
            
            existing_keys = set()
            
            # Helper to create key tuple
            def create_key(r):
                return (
                    self._safe_int(r.get("time_block")),
                    str(r.get("plant_id")),
                    str(r.get("dc_sg"))
                )
            
            for r in today_supply_records:
                existing_keys.add(create_key(r))
                
            for r in today_dc_sg_records:
                existing_keys.add(create_key(r))
                
            logger.info(f"Found {len(existing_keys)} existing unique keys (blocks) for today in supply_serve/dc_sg.")

            # 2. Fill from plant master and last available data
            plants = await self.plant_repository.collection.find({}).to_list(length=10000)
            
            new_dc_sg_entities = []
            
            for plant in plants:
                plant_id = plant.get("plant_id")
                if not plant_id: continue
                
                # Fetch the latest record for each time block (1-96) for this plant
                records = await self.dc_sg_repository.get_latest_records_per_timeblock(plant_id)
                
                if not records:
                    logger.debug(f"No historical data found for plant {plant_id}")
                    continue
                
                for record in records:
                    # Check if this block already exists for today
                    key = (
                        self._safe_int(record.get("time_block")),
                        str(record.get("plant_id")),
                        str(record.get("dc_sg"))
                    )
                    
                    if key in existing_keys:
                        continue
                    
                    # Create new entity for today
                    old_scheduled_at = record["scheduled_at"]
                    # Use today's date but keep the time
                    new_scheduled_at = datetime.combine(today, old_scheduled_at.time())
                    # Calculate TM values for DC records (55% of volume)
                    volume_val = self._safe_float(record.get("dc_afterloss", 0.0))
                    volume = self._safe_float(record.get("volume", 0.0))
                    dc_sg_type = str(record.get("dc_sg")).upper()
                    if dc_sg_type == "DC":
                        dam_tm_val = volume_val * 0.55
                    else:
                        dam_tm_val = 0.0
                    
                    entity = DCSgEntity(
                        scheduled_at=new_scheduled_at,
                        time_block=self._safe_int(record.get("time_block")),
                        dc_sg=str(record.get("dc_sg")),
                        plant_id=str(record.get("plant_id")),
                        volume=volume,
                        fc_price=self._safe_float(record.get("fc_price")),
                        px_price=self._safe_float(record.get("px_price")),
                        vc_price=self._safe_float(record.get("vc_price")),
                        price_group=record.get("price_group"),
                        dam_tm=dam_tm_val,
                        rtm_tm=0.0,  # Auto-fill historical data: rtm_tm always 0
                        multiplier=self._safe_float(record.get("multiplier", 0.0)),
                        ramping=str(record.get("ramping", "")),
                        source="dc_sg_rollover"
                    )
                    new_dc_sg_entities.append(entity)
            
            if not new_dc_sg_entities:
                logger.info("No missing data found to fill for today. Proceeding to sync check.")
                # Continue to ensure dc_sg -> supply_serve sync (Stage 2) happens 
                # even if we didn't fill from history.

            # Insert new entities
            processed_count = 0
            for entity in new_dc_sg_entities:
                res = await self.dc_sg_repository.update_or_insert_record(entity)
                if res.upserted_id or res.modified_count > 0:
                    processed_count += 1
            
            if processed_count > 0:
                logger.info(f"Filled {processed_count} missing records into dc_sg for today.")
            
            # Now process to supply_serve
            provider_configs = await self._get_provider_configurations("supply")
            if provider_configs:
                 # Ensure we process both Today and Tomorrow (for whatever exists in dc_sg)
                 return await self._process_dc_sg_to_supply_serve(metadata, provider_configs[0], [today, today + timedelta(days=1)])
            
            return True

        except Exception as e:
            logger.error(f"Error in missing supply data check: {str(e)}")
            return False

    def _get_pending_raw_data(self, provider_id: str, data_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get pending raw data for a provider, including FAILED status for retry.
        Overridden for SupplyProcessor to retry failed records.
        """
        try:
            from libs.repository.raw_data_processing_status_repository import RawDataProcessingStatusRepository
            from ..core.config import settings
            
            # Initialize processing status repository
            processing_status_repo = RawDataProcessingStatusRepository(
                mongodb_uri=settings.MONGODB_URI,
                database_name=settings.MONGODB_DATABASE,
                data_type=data_type
            )
            
            # Step 1: Get pending processing status records for this provider
            # Include both PENDING and FAILED statuses
            pending_status_query = {
                "provider_id": provider_id,
                "status": {"$in": [RawDataStatus.PENDING.value]}
            }
            
            # Use the repository's collection directly for more control
            pending_status_records = list(
                processing_status_repo.collection.find(pending_status_query)
                .sort("created_at", 1)  # Process oldest first
                .limit(3)  # Limit to 3 records per batch to prevent OOM
            )
            
            logger.info(f"Found {len(pending_status_records)} pending/failed status records for provider {provider_id}")
            
            if not pending_status_records:
                logger.info(f"No pending processing status found for provider: {provider_id}")
                return []
            
            # Step 2: Extract raw_data_ids from pending status records
            pending_raw_data_ids = [record["raw_data_id"] for record in pending_status_records]
            
            # Step 3: Fetch the actual raw data records using the IDs
            if not pending_raw_data_ids:
                return []
            
            # Convert string IDs to ObjectId for MongoDB query
            object_ids = []
            for raw_data_id in pending_raw_data_ids:
                try:
                    if isinstance(raw_data_id, str):
                        object_ids.append(ObjectId(raw_data_id))
                    else:
                        object_ids.append(raw_data_id)
                except Exception as id_error:
                    logger.warning(f"Invalid raw_data_id format: {raw_data_id}, error: {id_error}")
                    continue
            
            if not object_ids:
                return []
            
            # Query raw data collection with the pending IDs
            raw_data_query = {"_id": {"$in": object_ids}}
            pending_raw_data = self.raw_data_repository.find_many(raw_data_query)
            
            logger.info(f"Successfully fetched {len(pending_raw_data)} raw data records for provider {provider_id}")
            
            # Sort by creation time to maintain processing order
            if pending_raw_data:
                pending_raw_data.sort(key=lambda x: x.get("created_at", datetime.min))
            
            return pending_raw_data
            
        except Exception as e:
            logger.error(f"Error getting pending raw data for provider {provider_id}: {str(e)}")
            return []

    @staticmethod
    def _resolve_source(provider_config: Dict[str, Any]) -> str:
        """
        Derive source label from provider config ingestion_type.
        Returns 'upload', 'api', or the raw value as fallback.
        """
        ingestion_type = (
            provider_config.get("ingestion_type")
            or (provider_config.get("metadata") or {}).get("ingestion_type")
            or ""
        )
        if hasattr(ingestion_type, "value"):
            ingestion_type = ingestion_type.value
        ingestion_type = str(ingestion_type).lower()
        if ingestion_type == "upload":
            return "upload"
        if ingestion_type == "api":
            return "api"
        return ingestion_type or "unknown"

    async def process_raw_data_to_serve(
        self,
        raw_data: List[Dict[str, Any]],
        metadata: Dict[str, Any],
        provider_config: Dict[str, Any]
    ) -> bool:
        """
        Process raw data through a two-stage flow:
        1. Raw data is staged in the 'dc_sg' collection.
        2. Staged data is processed into the 'supply_serve' collection.
        """
        try:
            if not raw_data:
                logger.warning("No raw data provided for processing.")
                return True

            logger.info(f"Starting two-stage supply processing for {len(raw_data)} raw records.")

            # Extract unique scheduled dates from raw data
            unique_dates = set()
            for item in raw_data:
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
            
            target_dates = list(unique_dates)
            logger.info(f"Identified {len(target_dates)} unique dates for processing: {target_dates}")

            today = datetime.now().date()
            tomorrow = today + timedelta(days=1)
            
            recent_data = []
            historical_data = []

            for item in raw_data:
                scheduled_at = item.get("scheduled_at") or item.get("timestamp") or item.get("date_time")
                if scheduled_at:
                    if isinstance(scheduled_at, str):
                        try:
                            dt = datetime.fromisoformat(scheduled_at.replace('Z', '+00:00'))
                            sch_date = dt.date()
                        except ValueError:
                            continue
                    elif isinstance(scheduled_at, datetime):
                        sch_date = scheduled_at.date()
                    else:
                        continue
                    
                    if sch_date in [today, tomorrow]:
                        recent_data.append(item)
                    elif sch_date < today:
                        historical_data.append(item)
            
            logger.info(f"Data split: Recent (Today/Tomorrow): {len(recent_data)}, Historical: {len(historical_data)}")

            if recent_data:
                stage1_success, staged_entities = await self._process_raw_data_to_dc_sg(recent_data, metadata, provider_config)
                if not stage1_success:
                    logger.error("Stage 1 (raw_data -> dc_sg) failed for recent data.")
                    # Continue to try processing historical even if recent fails? Better to return False for consistency
                    return False
                logger.info("Stage 1 (raw_data -> dc_sg) completed successfully.")
                
                # Extract dates for recent data to trigger Stage 2 only for those dates
                # Actually Stage 2 should just process what's in dc_sg, but we can optimize by dates if needed.
                # The original code filtered by dates found in raw_data.
                # let's derive target_dates from recent_data for stage 2
                recent_dates = set()
                for item in recent_data:
                    # reusing parsing logic or just rely on what we filtered
                     # simplified: we know they are today/tomorrow
                     pass 
                
                # Convert Pydantic entities to dicts for Stage 2 consumption
                staged_dicts = [entity.model_dump(by_alias=True) for entity in staged_entities] if staged_entities else []
                
                # We can just pass target_dates=[today, tomorrow] or let it process all in dc_sg
                # Pass staged_dicts explicitly to ensure data availability even if DB read misses it
                stage2_success = await self._process_dc_sg_to_supply_serve(
                    metadata, 
                    provider_config, 
                    [today, tomorrow],
                    staged_data_override=staged_dicts
                )
                if not stage2_success:
                     logger.error("Stage 2 (dc_sg -> supply_serve) failed.")
                     return False
            
            if historical_data:
                 hist_success = await self._process_direct_to_supply_serve_historical(historical_data, metadata, provider_config)
                 if not hist_success:
                      logger.error("Historical data processing failed.")
                      return False

            # Update demand schedule based on the processed supply data
            # Recalculate unique dates for demand update
            all_dates = set()
            if recent_data: all_dates.update([today, tomorrow])
            for item in historical_data:
                 # Extract date again roughly
                 pass # We can just collect them from the initial loop if we want optimization
            
            # For simplicity, pass the original target_dates logic or just update relevant range
            if target_dates:
                demand_update_success = await self._update_demand_schedule(target_dates)
                if not demand_update_success:
                    logger.warning("Failed to update demand schedule after supply processing.")
            
            return True

        except Exception as e:
            logger.error(f"Error in the main supply processing pipeline: {str(e)}")
            return False

    async def _process_direct_to_supply_serve_historical(
        self,
        raw_data: List[Dict[str, Any]],
        metadata: Dict[str, Any],
        provider_config: Dict[str, Any]
    ) -> bool:
        """
        Process historical data directly to supply_serve/historical collection.
        Bypasses dc_sg staging.
        """
        logger.info(f"Processing {len(raw_data)} historical records directly to supply serve.")
        try:
            supply_entities = []
            historical_entities = []
            
            for raw_record in raw_data:
                # Basic validation similar to _create_dc_sg_entity_from_raw but direct to SupplyServeEntity
                # We need to create a temporary DC_SG entity logic or direct mapping.
                # To maximize code reuse and consistency, we can use a helper or map manually.
                
                # Reuse the raw parsing logic from dc_sg but mapped to supply directly?
                # A better approach: Create DCSgEntity to validate, then map to SupplyServeEntity immediately.
                
                dc_sg_entity = await self._create_dc_sg_entity_from_raw(raw_record, metadata, provider_config, check_updates=False)
                if not dc_sg_entity:
                    continue
                
                # Map to SupplyServeEntity
                supply_entity = SupplyServeEntity(
                    scheduled_at=dc_sg_entity.scheduled_at,
                    time_block=dc_sg_entity.time_block,
                    dc_sg=dc_sg_entity.dc_sg,
                    plant_id=dc_sg_entity.plant_id,
                    volume=dc_sg_entity.volume,
                    sg_beforelosses=dc_sg_entity.sg_beforelosses,
                    fc_price=dc_sg_entity.fc_price,
                    px_price=dc_sg_entity.px_price,
                    vc_price=dc_sg_entity.vc_price,
                    price_group=dc_sg_entity.price_group,
                    dam_tm=dc_sg_entity.dam_tm,
                    rtm_tm=dc_sg_entity.rtm_tm,
                    multiplier=dc_sg_entity.multiplier,
                    ramping=dc_sg_entity.ramping,
                    updated_flag=UpdatedFlag.UPDATED, # Default to updated for historical? Or calculated?
                    source=dc_sg_entity.source,
                    revision_no=dc_sg_entity.revision_no,
                    system_revision_number=1,
                    inserted_at=datetime.now(),
                    updated_at=datetime.now(),
                    timestamp=datetime.now()
                )
                
                # Use bulk process logic which handles archival/history splitting
                # But since it is historical < Today, it likely goes straight to HistoricalEntity or SupplyServeEntity?
                # SupplyServeEntity usually holds active window (T-2 to T+1), but can hold others.
                # However, the user said "supply serve or supply serve historical wherever it fits".
                # _bulk_process_supply_entities puts into supply_serve.
                # We can reuse the logic in Stage 2 loop roughly.
                
                # Let's check date offset
                today = datetime.now().date()
                record_date = supply_entity.scheduled_at.date()
                diff = (today - record_date).days
                
                if diff >= 3:
                     historical_entities.append(SupplyServeHistoricalEntity(**supply_entity.model_dump(by_alias=True)))
                else:
                     supply_entities.append(supply_entity)
                     if diff >= 1:
                         historical_entities.append(SupplyServeHistoricalEntity(**supply_entity.model_dump(by_alias=True)))

            serve_success = self._bulk_process_supply_entities(supply_entities, len(raw_data), len(supply_entities), 0)
            hist_success = self._bulk_process_historical_entities(historical_entities)
            
            return serve_success and hist_success

        except Exception as e:
            logger.error(f"Error processing historical data: {str(e)}")
            return False

    async def _process_raw_data_to_dc_sg(
        self,
        raw_data: List[Dict[str, Any]],
        metadata: Dict[str, Any],
        provider_config: Dict[str, Any]
    ) -> (bool, List[Any]):
        """
        Validates raw data and upserts it into the 'dc_sg' staging collection using bulk operations.
        Optimization: Fetches all necessary Plant and existing DC/SG data in bulk batches.
        """
        count = len(raw_data)
        logger.info(f"Stage 1: Processing {count} raw records into dc_sg collection (Bulk Mode).")
        
        if count == 0:
            return True, []

        try:
            # 1. Bulk Fetch Plants
            # Extract distinctive keys for lookup with whitespace stripping
            approval_nos = {str(item.get("approval_no")).strip() for item in raw_data if item.get("approval_no")}
            plant_ids = {str(item.get("plant_id")).strip() for item in raw_data if item.get("plant_id")}
            
            plant_map_by_approval = {}
            plant_map_by_id = {}
            
            # Fetch by approval_no
            if approval_nos:
                plants_by_app = await self.plant_repository.collection.find({"approval_no": {"$in": list(approval_nos)}}).to_list(length=10000)
                for p in plants_by_app:
                    plant_map_by_approval[str(p.get("approval_no")).strip()] = p
            
            # Fetch by plant_id
            if plant_ids:
                plants_by_id_list = await self.plant_repository.collection.find({"plant_id": {"$in": list(plant_ids)}}).to_list(length=10000)
                for p in plants_by_id_list:
                    plant_map_by_id[str(p.get("plant_id")).strip()] = p
            
            # 2. Prepare keys for existing DC/SG lookup
            # We need to look up existing records to determine changes/updated_flag
            lookup_keys = []
            valid_raw_items = []
            
            dropped_date_count = 0
            dropped_plant_count = 0
            dropped_mandatory_fields_count = 0
            # Parse raw_data document-level created_at for rtm_tm window check (15:25-15:45)
            # created_at is stored at metadata["raw_data"]["created_at"] by base_processor
            raw_data_created_at = None
            raw_data_meta = metadata.get("raw_data")
            if isinstance(raw_data_meta, dict):
                raw_data_created_at = raw_data_meta.get("created_at")
            # Fallback: check top-level metadata (for backward compatibility)
            if not raw_data_created_at:
                raw_data_created_at = metadata.get("created_at")
            
            if raw_data_created_at:
                if isinstance(raw_data_created_at, str):
                    try:
                        raw_data_created_at = datetime.fromisoformat(raw_data_created_at.replace('Z', '+00:00'))
                    except ValueError:
                        raw_data_created_at = None
            
            logger.info(f"RTM TM check: raw_data_created_at={raw_data_created_at}")
            # rtm_tm window flag: True if created_at falls between 15:25-15:45
            rtm_tm_window_active = False
            if raw_data_created_at and isinstance(raw_data_created_at, datetime):
                if time(15, 25) <= raw_data_created_at.time() <= time(15, 45):
                    rtm_tm_window_active = True


            for item in raw_data:
                # Initialize variables to avoid NameError
                final_plant_id = None
                dc_sg_val = None
                time_block = None
                
                # Pre-validate minimal requirements to form a key
                scheduled_at = item.get("scheduled_at") or item.get("timestamp") or item.get("date_time")
                if not scheduled_at: 
                    dropped_date_count += 1
                    continue
                
                # Convert date
                if isinstance(scheduled_at, str):
                    try:
                        scheduled_at = datetime.fromisoformat(scheduled_at.replace('Z', '+00:00'))
                    except: 
                        dropped_date_count += 1
                        continue
                elif not isinstance(scheduled_at, datetime):
                    dropped_date_count += 1
                    continue
                
                # Resolve Plant ID for the key
                # Replicate the logic: Approval -> Plant ID
                p_entity = None
                app_no = item.get("approval_no")
                if app_no:
                    p_entity = plant_map_by_approval.get(str(app_no).strip())
                
                if not p_entity:
                    pid = item.get("plant_id")
                    if pid:
                        p_entity = plant_map_by_id.get(str(pid).strip())
                
                if not p_entity: 
                    dropped_plant_count += 1
                    if dropped_plant_count <= 5: # Log first 5 missing plants for debug
                         logger.warning(f"Plant lookup failed for raw item. Approval: {app_no}, PlantID: {item.get('plant_id')}")
                    continue
                
                final_plant_id = p_entity.get("plant_id")
                dc_sg_val = item.get("dc_sg")
                time_block = item.get("time_block")
                
                now_time = datetime.now().time()
                # Using dt_time to avoid any potential shadowing of top-level from datetime import time
                from datetime import time as dt_time
                if (dt_time(7, 45) > now_time or now_time > dt_time(10, 10)) and dc_sg_val == "DC":
                    continue
                
                if final_plant_id and dc_sg_val and time_block:
                    item["_parsed_scheduled_at"] = scheduled_at # Cache parsed date
                    item["_resolved_plant_id"] = final_plant_id
                    
                    lookup_keys.append({
                        "scheduled_at": scheduled_at,
                        "time_block": self._safe_int(time_block),
                        "dc_sg": str(dc_sg_val),
                        "plant_id": str(final_plant_id)
                    })
                    valid_raw_items.append(item)
                else:
                    dropped_mandatory_fields_count += 1
                    if dropped_mandatory_fields_count <= 5:
                        logger.warning(f"Missing mandatory fields for item. Plant: {final_plant_id}, DC/SG: {dc_sg_val}, Block: {time_block}")

            if dropped_date_count > 0 or dropped_plant_count > 0 or dropped_mandatory_fields_count > 0:
                 logger.warning(f"Stage 1 Dropped Records: Date Errors={dropped_date_count}, Plant Lookup Failed={dropped_plant_count}, Missing Fields={dropped_mandatory_fields_count}")
            
            logger.info(f"Stage 1: Proceeding with {len(valid_raw_items)} valid records out of {count} total.")

            # 3. Bulk Fetch Existing DC/SG Records
            existing_records_map = {}
            
            # Optimization: Fetch by date range and plant_ids (much faster than 8000+ OR clauses)
            unique_dates = {dt.date() for dt in {k["scheduled_at"] for k in lookup_keys}}
            unique_plant_ids = list(plant_ids) if plant_ids else None
            
            if unique_dates:
                found_records = []
                for d in unique_dates:
                    # Fetch full day records for these dates, filtered by plants
                    day_records = await self.dc_sg_repository.get_records_by_date(d, unique_plant_ids)
                    if day_records:
                        found_records.extend(day_records)
                
                logger.info(f"Bulk fetched {len(found_records)} existing records for comparison.")
                
                for rec in found_records:
                    # Create a tuple key for fast lookup in memory
                    rec_scheduled_at = rec.get("scheduled_at")
                    # Ensure datetime type
                    if isinstance(rec_scheduled_at, str):
                         try:
                             rec_scheduled_at = datetime.fromisoformat(rec_scheduled_at.replace('Z', '+00:00'))
                         except: pass

                    key = (
                        rec_scheduled_at, 
                        rec.get("time_block"),
                        rec.get("dc_sg"),
                        rec.get("plant_id")
                    )
                    existing_records_map[key] = rec
            
            # 4. Construct Entities in Memory
            entities_to_upsert = []
            provider_metadata = provider_config.get("metadata") or {}
            
            today = datetime.now().date()
            
            for item in valid_raw_items:
                try:
                    sch_at = item["_parsed_scheduled_at"]
                    expected_key = (
                        sch_at,
                        self._safe_int(item.get("time_block")),
                        str(item.get("dc_sg")),
                        str(item["_resolved_plant_id"])
                    )
                    
                    existing_record = existing_records_map.get(expected_key)
                    
                    # Determine updated_flag
                    updated_flag = UpdatedFlag.UPDATED
                    # Logic: If existing record found, compare volume
                    if existing_record:
                        new_vol = self._safe_float(item.get("volume"))
                        old_vol = self._safe_float(existing_record.get("volume"))
                        
                        if new_vol == old_vol:
                            if provider_metadata.get("updateType") == "R":
                                updated_flag = UpdatedFlag.REPLACED
                            else:
                                updated_flag = UpdatedFlag.OLD # Data unchanged
                        else:
                            if provider_metadata.get("updateType") == "R":
                                updated_flag = UpdatedFlag.REPLACED
                            else:
                                updated_flag = UpdatedFlag.UPDATED
                    else:
                        # New record
                        if provider_metadata.get("updateType") == "R":
                            updated_flag = UpdatedFlag.REPLACED
                        else:
                            updated_flag = UpdatedFlag.UPDATED

                    # Determine process_type
                    sch_date = sch_at.date()
                    process_type = None
                    if sch_date == today:
                        process_type = "today"
                    elif sch_date == today + timedelta(days=1):
                        process_type = "tomorrow"

                    # ================================================================
                    # Max MW DC Logic: For SG records on tomorrow's data at 08:00-08:14
                    # ================================================================
                    max_mw_dc_value = None
                    eightThirtyValue = None
                    
                    # Get the document-level created_at timestamp (when this raw_data document was scraped)
                    # This is passed from base_processor via metadata["raw_data"]["created_at"]
                    raw_data_info = metadata.get("raw_data", {})
                    raw_timestamp = raw_data_info.get("created_at")
                    
                    if raw_timestamp:
                        if isinstance(raw_timestamp, str):
                            try:
                                raw_timestamp = datetime.fromisoformat(raw_timestamp.replace('Z', '+00:00'))
                            except ValueError:
                                raw_timestamp = None
                        elif not isinstance(raw_timestamp, datetime):
                            raw_timestamp = None
                    
                    # Check conditions: SG + time window
                    if (process_type == "tomorrow" and str(item.get("dc_sg")).upper() == "SG"
                        and raw_timestamp is not None):
                        
                        raw_time = raw_timestamp.time()
                        from datetime import time as dt_time
                        window_start = dt_time(8, 30, 0)   # 08:30:00
                        window_end = dt_time(8, 44, 59)   # 08:44:59
                        
                        if window_start <= raw_time <= window_end:
                            # Copy volume to max_mw_dc
                            max_mw_dc_value = self._safe_float(item.get("volume"))
                            logger.info(f"Setting max_mw_dc={max_mw_dc_value} for plant {item['_resolved_plant_id']} block {item.get('time_block')} (tomorrow SG in 08:30-08:44 window)")
                    
                    if process_type == "today" and str(item.get("dc_sg")).upper() == "SG" and raw_timestamp is not None:
                        raw_time = raw_timestamp.time()
                        from datetime import time as dt_time
                        window_start = dt_time(8, 50, 0)   # 08:50:00
                        window_end = dt_time(9, 0, 59)   # 09:00:59
                        if window_start <= raw_time <= window_end:
                            eightThirtyValue = self._safe_float(item.get("volume"))
                            logger.info(f"Setting eightThirtyValue={eightThirtyValue} for plant {item['_resolved_plant_id']} block {item.get('time_block')} (today SG)")
                    # Preserve existing max_mw_dc if not setting a new one
                    if max_mw_dc_value is None and existing_record:
                        max_mw_dc_value = existing_record.get("max_mw_dc")

                    # Calculate TM values for DC records (55% of volume)
                    volume_value = self._safe_float(item.get("dc_afterloss", 0.0))
                    volume = self._safe_float(item.get("volume", 0.0))
                    dc_sg_type = str(item.get("dc_sg")).upper()
                    
                    dam_tm_value = 0.0
                    rtm_tm_value = 0.0
                    
                    if dc_sg_type == "DC":
                        dam_tm_value = volume_value * 0.55
                    else:
                        dam_tm_value = 0.0  
                    
                    # Per-item rtm_tm calculation:
                    # If raw_data created_at is in 15:25-15:45 window AND item's scheduled_at > created_at
                    # then set rtm_tm = volume. Otherwise preserve existing non-zero rtm_tm or default to 0.
                    rtm_tm_value = 0.0
                    calc_rtm_tm_for_item = False
                    if rtm_tm_window_active and raw_data_created_at and isinstance(raw_data_created_at, datetime):
                        if sch_at > raw_data_created_at:
                            calc_rtm_tm_for_item = True
                            rtm_tm_value = volume
                            logger.info(f"Setting rtm_tm={rtm_tm_value} for plant {item['_resolved_plant_id']} block {item.get('time_block')} (15:25-15:45 window, scheduled_at > created_at)")
                    
                    if not calc_rtm_tm_for_item:
                        # Preserve existing rtm_tm if already set (should not be overwritten once set)
                        existing_rtm_tm = self._safe_float(existing_record.get("rtm_tm", 0.0)) if existing_record else 0.0
                        if existing_rtm_tm > 0:
                            rtm_tm_value = existing_rtm_tm
                        else:
                            rtm_tm_value = 0.0
                    
                    entity = DCSgEntity(
                        scheduled_at=sch_at,
                        time_block=self._safe_int(item.get("time_block")),
                        dc_sg=str(item.get("dc_sg")),
                        plant_id=str(item["_resolved_plant_id"]),
                        volume=volume,
                        sg_beforelosses=self._safe_float(item.get("sg_beforelosses")),
                        fc_price=self._safe_float(item.get("fc_price")),
                        px_price=self._safe_float(item.get("px_price")),
                        vc_price=self._safe_float(item.get("vc_price")),
                        price_group=item.get("price_group"),
                        dam_tm=dam_tm_value,
                        rtm_tm=rtm_tm_value,
                        multiplier=self._safe_float(item.get("multiplier", 0.0)),
                        ramping=str(item.get("ramping", "")),
                        source=self._resolve_source(provider_config),
                        revision_no=self._safe_int(item.get("revision_no")),
                        process_type=process_type,
                        updated_flag=updated_flag,
                        max_mw_dc=max_mw_dc_value,
                        eightThirtyValue=eightThirtyValue
                    )
                    entities_to_upsert.append(entity)
                    
                except Exception as entity_err:
                    logger.error(f"Error creating entity in bulk loop: {entity_err}")
                    continue
            
            # 5. Bulk Upsert
            if entities_to_upsert:
                result = await self.dc_sg_repository.bulk_upsert_dc_sg(entities_to_upsert)
                logger.info(f"Stage 1 Bulk Result: {result}")
            else:
                logger.info("Stage 1: No valid entities to upsert after processing.")
                
            return True, entities_to_upsert

        except Exception as e:
            logger.error(f"Error in bulk process_raw_data_to_dc_sg: {str(e)}")
            return False, []

    # Kept for compatibility / single record usage if needed, but not used in bulk flow
    def _create_dc_sg_entity_from_raw(self, *args, **kwargs):
        # Implementation moved inside bulk method for performance
        # Or we can delegate back if needed? Currently kept unused/legacy or refactor fully?
        # The user's prompt implies we refactor, so we can override or leave it for other small calls.
        # But wait, `_process_direct_to_supply_serve_historical` calls this.
        # So I MUST keep it or refactor that too. I will restore it below or keep strict reference.
        # Actually, let's keep it but mark it as less efficient.
        # The request was to move to batch.
        return super()._create_dc_sg_entity_from_raw(*args, **kwargs) if hasattr(super(), "_create_dc_sg_entity_from_raw") else None

    # Re-implementing _create_dc_sg_entity_from_raw for historical path usage
    async def _create_dc_sg_entity_from_raw(
        self,
        raw_item: Dict[str, Any],
        metadata: Dict[str, Any],
        provider_config: Dict[str, Any],
        check_updates: bool = True
    ) -> Optional[DCSgEntity]:
        # This is the old single-item implementation for historical/fallback
        # ... (restored logic from original file)
        try:
             # Just simplified copy of the original logic for fallback
            provider_metadata = provider_config.get("metadata") or {}
            scheduled_at = raw_item.get("scheduled_at") or raw_item.get("timestamp") or raw_item.get("date_time")
            if not scheduled_at: return None
            if isinstance(scheduled_at, str): scheduled_at = datetime.fromisoformat(scheduled_at.replace('Z', '+00:00'))
            
            plant_entity = None
            if raw_item.get("approval_no"):
                plant_entity = await self.plant_repository.find_one({"approval_no": str(raw_item.get("approval_no"))})
            if not plant_entity and raw_item.get("plant_id"):
                plant_entity = await self.plant_repository.find_one({"plant_id": str(raw_item.get("plant_id"))})
            if not plant_entity: return None
            
            plant_id = plant_entity.get("plant_id")
            dc_sg = raw_item.get("dc_sg")
            if not dc_sg: return None

            updated_flag = UpdatedFlag.UPDATED
            # For historical single-item, skipping complex check for now or strict copy?
            # Keeping it simple as historical often skips this check or is handled differently.
            rtm_tm_value = 0.0  # Historical data: rtm_tm always 0
            # Calculate TM values for DC records (55% of volume)
            volume_value = self._safe_float(raw_item.get("dc_afterloss"))
            volume = self._safe_float(raw_item.get("volume", 0.0))
            dc_sg_type = str(dc_sg).upper()
            if dc_sg_type == "DC":
                dam_tm_value = volume_value * 0.55
            else:
                dam_tm_value = 0.0
            
            return DCSgEntity(
                scheduled_at=scheduled_at,
                time_block=self._safe_int(raw_item.get("time_block")),
                dc_sg=str(dc_sg),
                plant_id=str(plant_id),
                volume=volume,
                sg_beforelosses=self._safe_float(raw_item.get("sg_beforelosses")),
                fc_price=self._safe_float(raw_item.get("fc_price")),
                px_price=self._safe_float(raw_item.get("px_price")),
                vc_price=self._safe_float(raw_item.get("vc_price")),
                price_group=raw_item.get("price_group"),
                dam_tm=dam_tm_value,
                rtm_tm=rtm_tm_value,
                multiplier=self._safe_float(raw_item.get("multiplier", 0.0)),
                ramping=str(raw_item.get("ramping", "")),
                source=self._resolve_source(provider_config),
                revision_no=self._safe_int(raw_item.get("revision_no")),
                process_type="today", # Dummy for historical
                updated_flag=updated_flag
            )
        except Exception as e:
            logger.error(f"Error creating DCSgEntity for {raw_item.get('plant_id')} / {raw_item.get('approval_no')}: {str(e)}", exc_info=True)
            return None

    async def _process_dc_sg_to_supply_serve(
        self,
        metadata: Dict[str, Any],
        provider_config: Dict[str, Any],
        target_dates: Optional[List[Any]] = None,
        staged_data_override: Optional[List[Dict[str, Any]]] = None
    ) -> bool:
        """
        Processes data from the 'dc_sg' staging collection into the 'supply_serve' collection using Bulk Ops.
        """
        logger.info("Stage 2 (Bulk): Processing staged data from dc_sg to supply_serve.")
        
        staged_records = await self.dc_sg_repository.get_all_records()
            
        count = len(staged_records)
        if count == 0:
            logger.info("No staged records in dc_sg to process into supply_serve.")
            return True
        
        logger.info(f"Found {count} records in dc_sg to process.")

        try:
            today = datetime.now().date()
            supply_window_dates = [
                today - timedelta(days=2),
                today - timedelta(days=1),
                today,
                today + timedelta(days=1)
            ]
            three_days_ago = today - timedelta(days=3)
            
            # Simple cleanup of really old data
            await self._cleanup_old_data(three_days_ago)

            # Filtering records for current window in memory
            valid_staged_records = []
            lookup_keys = []
            
            # Use target_dates if provided, otherwise default to window
            allowed_dates = set(target_dates) if target_dates else set(supply_window_dates)
            logger.info(f"DEBUG: Allowed dates for sync: {allowed_dates}")
            
            rejected_count = 0
            for rec in staged_records:
                scheduled_at = rec.get("scheduled_at")
                if not scheduled_at: 
                    continue
                record_date = scheduled_at.date()
                
                # If target_dates is specifically passed (like [today, tomorrow]), strict filter
                if target_dates:
                     if record_date not in allowed_dates:
                         rejected_count += 1
                         if rejected_count <= 5:
                             logger.info(f"DEBUG: Rejected record date {record_date} (Not in target_dates {allowed_dates})")
                         continue
                else:
                    # Default window check
                    day_diff = (today - record_date).days
                    if record_date not in supply_window_dates and day_diff >= 3:
                         rejected_count += 1
                         if rejected_count <= 5:
                             logger.info(f"DEBUG: Rejected record date {record_date} (Not in window, day_diff={day_diff})")
                         continue

                key = {
                    "scheduled_at": scheduled_at,
                    "time_block": rec.get("time_block"),
                    "dc_sg": str(rec.get("dc_sg")),
                    "plant_id": str(rec.get("plant_id"))
                }
                lookup_keys.append(key)
                valid_staged_records.append(rec)
            
            logger.info(f"DEBUG: Filtering complete. Valid: {len(valid_staged_records)}, Rejected: {rejected_count}")

            if not valid_staged_records:
                logger.info(f"No valid records found in staged data after filtering. Allowed dates: {allowed_dates}")
                return True
            
            logger.info(f"Processing {len(valid_staged_records)} valid records from staged data.")

            # 1. Bulk Fetch Existing Supply Records
            existing_supply_map = {} # Key -> Record
            
            chunk_size = 1000
            for i in range(0, len(lookup_keys), chunk_size):
                chunk = lookup_keys[i:i+chunk_size]
                if not chunk: continue
                
                # Manually run find query in thread
                filter_query = {"$or": chunk}
                found = await self.supply_repository.serve_collection.find(filter_query).to_list(length=10000)
                for frec in found:
                    k = (
                        frec.get("scheduled_at"),
                        frec.get("time_block"),
                        frec.get("dc_sg"),
                        frec.get("plant_id")
                    )
                    existing_supply_map[k] = frec
            
            logger.info(f"DEBUG: Found {len(existing_supply_map)} existing records in supply_serve matching pending keys.")

            # 2. Process Entities In Memory
            logger.info(f"Processing {len(valid_staged_records)} valid records from staged data.")
            supply_entities = [] # For valid window
            historical_entities = [] # For older
            
            provider_metadata = provider_config.get("metadata") or {}
            
            processed_logs = 0
            for staged_record in valid_staged_records:
                scheduled_at = staged_record["scheduled_at"]
                record_date = scheduled_at.date()
                time_block = staged_record.get("time_block")
                dc_sg = staged_record.get("dc_sg")
                plant_id = staged_record.get("plant_id")
                
                key_tuple = (scheduled_at, time_block, str(dc_sg), str(plant_id))
                existing_supply_record = existing_supply_map.get(key_tuple)
                
                supply_entity = None
                
                # Logic to create/update entity
                if existing_supply_record:
                     flag_from_staged = staged_record.get("updated_flag")
                     # logger.info(f"Processing existing supply record: {existing_supply_record}") # Too spammy
                     supply_entity = self._create_supply_entity_with_preserved_timestamps(staged_record, metadata, provider_config, existing_supply_record)
                     
                     if supply_entity:
                         if flag_from_staged:
                             try:
                                 supply_entity.updated_flag = UpdatedFlag(flag_from_staged) if isinstance(flag_from_staged, str) else flag_from_staged
                             except ValueError:
                                 supply_entity.updated_flag = UpdatedFlag.UPDATED
                         else:
                             # Fallback logic
                             if self._has_supply_data_changed(existing_supply_record, staged_record):
                                 supply_entity.updated_flag = UpdatedFlag.REPLACED if provider_metadata.get("updateType") == "R" else UpdatedFlag.UPDATED
                             else:
                                 supply_entity.updated_flag = UpdatedFlag.REPLACED if provider_metadata.get("updateType") == "R" else UpdatedFlag.OLD
                else:
                    if processed_logs < 5:
                        logger.info(f"DEBUG: Creating NEW supply record for {plant_id} at {scheduled_at} ({dc_sg})")
                        processed_logs += 1
                        
                    supply_entity = self._create_supply_entity_from_staged(staged_record, metadata, provider_config)
                    if supply_entity:
                         flag_from_staged = staged_record.get("updated_flag")
                         if flag_from_staged:
                             try:
                                 supply_entity.updated_flag = UpdatedFlag(flag_from_staged) if isinstance(flag_from_staged, str) else flag_from_staged
                             except ValueError:
                                 supply_entity.updated_flag = UpdatedFlag.UPDATED
                         else:
                             supply_entity.updated_flag = UpdatedFlag.REPLACED if provider_metadata.get("updateType") == "R" else UpdatedFlag.UPDATED

                if not supply_entity: 
                    # logger.warning(f"Failed to create supply entity for {staged_record}")
                    continue

                # Routing to Serve vs Historical
                day_diff = (today - record_date).days
                
                if day_diff >= 3:
                     # Historical Only
                     historical_entities.append(SupplyServeHistoricalEntity(**supply_entity.model_dump(by_alias=True)))
                else:
                     # Serve
                     supply_entities.append(supply_entity)
                     # If exactly T-1 (Yesterday), also push to historical
                     if day_diff == 1:
                         historical_entities.append(SupplyServeHistoricalEntity(**supply_entity.model_dump(by_alias=True)))

            # 3. Bulk Write
            serve_success = True
            hist_success = True
            
            if supply_entities:
                logger.info(f"Writing {len(supply_entities)} items to supply_serve.")
                # Supply Serve is a Normal Collection, so we can use Upsert (UpdateOne).
                # bulk_insert_supply_serve_with_deduplication uses bulk_upsert_optimized internally.
                # This prevents duplicate key errors during concurrent cron runs.
                # Wrap synchronously blocking DB call in thread
                res = await self.supply_repository.bulk_insert_supply_serve_with_deduplication(
                    supply_entities
                )
                logger.info(f"DEBUG: Supply Serve Write Result: {res}")
                # Result is a dict with counts
                if res.get("error_count", 0) > 0 and res.get("inserted_count", 0) == 0:
                     serve_success = False
                     logger.error(f"Stage 2 Serve Insert Errors: {res.get('errors')}")
            
            if historical_entities:
                 # Historical is also time-series, use deduplication insert (Delete-Insert)
                 # Wrap synchronously blocking DB call in thread
                 res_hist = await self.supply_repository.bulk_insert_supply_historical_with_deduplication(
                     historical_entities
                 )
                 logger.info(f"DEBUG: Historical Write Result: {res_hist}")
                 if res_hist.get("error_count", 0) > 0 and res_hist.get("inserted_count", 0) == 0:
                     hist_success = False
                     logger.error(f"Stage 2 Historical Insert Errors: {res_hist.get('errors')}")

            return serve_success and hist_success

        except Exception as e:
            logger.error(f"Error in stage 2 bulk processing: {str(e)}")
            return False

    def _bulk_process_supply_entities(self, entities: List[SupplyServeEntity], total_raw: int, processed: int, skipped: int) -> bool:
        if not entities:
            logger.info("No new or updated supply serve records to process.")
            return True
        
        result = self.supply_repository.bulk_insert_supply_serve_with_deduplication(entities)
        logger.info(
            "Processed supply entities - Total: %d, Processed: %d, Skipped: %d, Inserted: %d, Duplicates Removed: %d, Errors: %d",
            total_raw, processed, skipped, result.get("inserted_count", 0),
            result.get("duplicates_removed", 0), result.get("error_count", 0)
        )
        return result.get("error_count", 0) == 0

    def _bulk_process_historical_entities(self, entities: List[SupplyServeHistoricalEntity]) -> bool:
        if not entities:
            logger.info("No historical supply records to process.")
            return True
        
        try:
            result = self.supply_repository.bulk_insert_supply_historical_with_deduplication(entities)
            logger.info("Processed supply historical entities - Total: %d, Inserted: %d, Duplicates Removed: %d, Errors: %d",
                       len(entities), result["inserted_count"], result.get("duplicates_removed", 0), result["error_count"])
            if result["errors"]:
                logger.warning("Encountered %d errors during historical processing: %s", len(result["errors"]), result["errors"])
            
            # Resilience: Return True even if there are some errors (e.g. duplicates or non-fatal write errors)
            # This ensures that Today/Tomorrow data finalize even if history has minor issues.
            if result["error_count"] > 0:
                 logger.warning(f"Historical processing had {result['error_count']} errors, but continuing as non-fatal.")
            
            return True
        except Exception as hist_error:
            logger.error(f"Error inserting historical supply entities: {str(hist_error)}")
            return False

    def _create_supply_entity_from_staged(
        self,
        staged_item: Dict[str, Any],
        metadata: Dict[str, Any],
        provider_config: Dict[str, Any]
    ) -> Optional[SupplyServeEntity]:
        try:
            provider_metadata = provider_config.get("metadata") or {}
            # Extract source revision number from staged data
            source_revision = staged_item.get("revision_number") or staged_item.get("version") or staged_item.get("rev_no") or staged_item.get("revision_no")
            # If not received in raw data, source revision = system revision
            if source_revision is None:
                source_revision = 1
            
            return SupplyServeEntity(
                scheduled_at=staged_item["scheduled_at"],
                time_block=staged_item.get("time_block"),
                dc_sg=staged_item.get("dc_sg"),
                volume=staged_item.get("volume"),
                sg_beforelosses=staged_item.get("sg_beforelosses"),
                fc_price=staged_item.get("fc_price"),
                px_price=staged_item.get("px_price"),
                vc_price=staged_item.get("vc_price"),
                price_group=staged_item.get("price_group"),
                dam_tm=staged_item.get("dam_tm", 0.0),
                rtm_tm=staged_item.get("rtm_tm", 0.0),
                multiplier=self._safe_float(staged_item.get("multiplier", 0.0)),
                ramping=staged_item.get("ramping", ""),
                updated_flag=UpdatedFlag.UPDATED,
                plant_id=staged_item.get("plant_id"),
                source=staged_item.get("source"),
                # Revision tracking
                source_revision_number=source_revision,
                system_revision_number=1  # New record starts at 1
            )
        except Exception as e:
            logger.error(f"Error creating new SupplyServeEntity from staged data: {str(e)}")
            return None

    def _create_supply_entity_with_preserved_timestamps(
        self,
        staged_item: Dict[str, Any],
        metadata: Dict[str, Any],
        provider_config: Dict[str, Any],
        existing_record: Dict[str, Any]
    ) -> Optional[SupplyServeEntity]:
        try:
            provider_metadata = provider_config.get("metadata") or {}
            # Extract source revision number from staged data
            source_revision = staged_item.get("revision_number") or staged_item.get("version") or staged_item.get("rev_no") or staged_item.get("revision_no")
            
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
            
            return SupplyServeEntity(
                scheduled_at=staged_item["scheduled_at"],
                time_block=staged_item.get("time_block"),
                dc_sg=staged_item.get("dc_sg"),
                plant_id=staged_item.get("plant_id"),
                volume=staged_item.get("volume"),
                sg_beforelosses=staged_item.get("sg_beforelosses"),
                fc_price=staged_item.get("fc_price"),
                px_price=staged_item.get("px_price"),
                vc_price=staged_item.get("vc_price"),
                price_group=staged_item.get("price_group", ""),
                dam_tm=staged_item.get("dam_tm", 0.0),
                # Preserve existing rtm_tm if already set in supply_serve (double safety)
                rtm_tm=staged_item.get("rtm_tm", 0.0) if staged_item.get("rtm_tm", 0.0) else self._safe_float(existing_record.get("rtm_tm", 0.0)),
                multiplier=self._safe_float(staged_item.get("multiplier", 0.0)),
                ramping=staged_item.get("ramping", ""),
                inserted_at=existing_record.get("inserted_at", datetime.now()),
                created_at=existing_record.get("created_at", datetime.now()),
                updated_at=datetime.now(),
                timestamp=datetime.now(),
                source=staged_item.get("source"),
                # Revision tracking
                source_revision_number=source_revision,
                system_revision_number=new_system_revision  # Increment on update
            )
        except Exception as e:
            logger.error(f"Error creating updated SupplyServeEntity from staged data: {str(e)}")
            return None

    async def _calculate_and_apply_sg_multiplier(self, metadata: Dict[str, Any]) -> bool:
        """
        Calculate multiplier based on SG data per Time Block, and adjust DC values.
        Steps:
        1. Fetch all SG records for the target window (Today & Tomorrow).
        2. For each SG record:
           - Calculate Multiplier = (SG Before Loss - Volume) / SG Before Loss * 100.
           - Store multiplier in a map keyed by (plant_id, date, time_block).
           - Update the SG record itself with the calculated multiplier (Delete-Insert).
        3. Fetch all DC records for the target window.
        4. For each DC record:
           - Lookup multiplier from the map.
           - Update dc_afterloss = Base DC Volume * (1 - Multiplier/100).
           - Update ramp values.
           - Update the DC record (Delete-Insert).
        """
        logger.info("Executing SG Multiplier Calculation (Per Block) and DC Adjustment...")
        try:
            today = datetime.now().date()
            target_dates = [today, today + timedelta(days=1)]
            start_range = datetime.combine(target_dates[0], datetime.min.time())
            end_range = datetime.combine(target_dates[-1] + timedelta(days=1), datetime.min.time())

            # 1. Fetch ALL SG Records for the range
            sg_filter_all = {
                "scheduled_at": {"$gte": start_range, "$lt": end_range},
                "dc_sg": "SG"
            }
            sg_records = await self.supply_repository.collection.find(
                sg_filter_all
            ).to_list(length=10000)
            
            # Group SG records by Plant to process plant-wise (cleaner for logs and batching)
            # Map: plant_id -> list of records
            sg_by_plant = {}
            for rec in sg_records:
                p_id = rec.get("plant_id")
                if p_id:
                    if p_id not in sg_by_plant: sg_by_plant[p_id] = []
                    sg_by_plant[p_id].append(rec)

            # Map to store multipliers for DC lookup: match_key -> multiplier
            # match_key = (plant_id, date_str, time_block)
            multiplier_map = {}

            processor_success = True

            # Process SG Records by Plant - Calculate plant-level multiplier
            # New formula: sum(sg_beforelosses for all 96 blocks) / sum(volume for all 96 blocks)
            plant_multiplier_map = {}  # plant_id -> multiplier (single value per plant)
            
            for plant_id, records in sg_by_plant.items():
                try:
                    # Calculate sum of sg_beforelosses and volume across all blocks for this plant
                    total_sg_beforeloss = 0.0
                    total_volume = 0.0
                    
                    for sg_rec in records:
                        vol = self._safe_float(sg_rec.get("volume", 0))
                        sgl = self._safe_float(sg_rec.get("sg_beforelosses", 0))
                        total_sg_beforeloss += sgl
                        total_volume += vol
                        # Debug: Log individual record values
                        logger.debug(f"Plant {plant_id} - Block {sg_rec.get('time_block')}: volume={vol}, sg_beforelosses={sgl}, raw_vol={sg_rec.get('volume')}, raw_sgl={sg_rec.get('sg_beforelosses')}")
                    
                    # Debug: Log totals before calculation
                    logger.info(f"Plant {plant_id}: total_sg_beforeloss={total_sg_beforeloss}, total_volume={total_volume}, record_count={len(records)}")
                    
                    # Calculate plant-level multiplier: sum(sg_beforelosses) / sum(volume)
                    multiplier = 0.0
                    if total_sg_beforeloss > 0:
                        multiplier = total_volume / total_sg_beforeloss
                        logger.info(f"Plant {plant_id}: Calculated multiplier = {total_volume} / {total_sg_beforeloss} = {multiplier}")
                    else:
                        multiplier = 0.0
                        logger.warning(f"Plant {plant_id}: total_sg_beforeloss is 0, setting multiplier to 0")
                    
                    # Store plant-level multiplier
                    plant_multiplier_map[plant_id] = multiplier
                    
                    # Also populate the block-level map for DC lookup (same multiplier for all blocks)
                    for sg_rec in records:
                        sch_at = sg_rec.get("scheduled_at")
                        if isinstance(sch_at, datetime):
                            d_str = sch_at.date().isoformat()
                        else:
                            d_str = str(sch_at)[:10]
                        tb = sg_rec.get("time_block")
                        multiplier_map[(plant_id, d_str, tb)] = multiplier
                    
                    # Update SG records with the plant-level multiplier
                    updated_sg_entities = []
                    for sg_rec in records:
                        tb = sg_rec.get("time_block")
                        vol = self._safe_float(sg_rec.get("volume", 0))
                        sgl = self._safe_float(sg_rec.get("sg_beforelosses", 0))
                        
                        # Re-create Entity for Update
                        try:
                            # We are updating the 'multiplier' field with plant-level multiplier
                            updated_sg_entity = SupplyServeEntity(
                                scheduled_at=sg_rec["scheduled_at"],
                                time_block=tb,
                                dc_sg="SG",
                                plant_id=plant_id,
                                volume=vol,
                                sg_beforelosses=sgl,
                                fc_price=sg_rec.get("fc_price"),
                                px_price=sg_rec.get("px_price"),
                                vc_price=sg_rec.get("vc_price"),
                                price_group=sg_rec.get("price_group"),
                                dam_tm=sg_rec.get("dam_tm"),
                                rtm_tm=sg_rec.get("rtm_tm"),
                                multiplier=multiplier,  # Plant-level multiplier
                                ramping=sg_rec.get("ramping", ""),
                                ramp_up=sg_rec.get("ramp_up"),
                                ramp_down=sg_rec.get("ramp_down"),
                                dc_afterloss=sg_rec.get("dc_afterloss"),
                                source=sg_rec.get("source"),
                                inserted_at=sg_rec.get("inserted_at", datetime.now()),
                                updated_at=datetime.now(),
                                created_at=sg_rec.get("created_at", datetime.now()),
                                processed_at=datetime.now(),
                                timestamp=sg_rec.get("timestamp", datetime.now()),
                                updated_flag=UpdatedFlag.UPDATED,
                                source_revision_number=sg_rec.get("source_revision_number"),
                                system_revision_number=sg_rec.get("system_revision_number", 0) + 1
                            )
                            updated_sg_entities.append(updated_sg_entity)
                        except Exception as entity_err:
                            logger.error(f"Failed to create updated SG entity for {plant_id}: {entity_err}")
                    
                    # Perform Delete-Insert for SG (Batch per plant)
                    if updated_sg_entities:
                        plant_sg_filter = {
                            "scheduled_at": {"$gte": start_range, "$lt": end_range},
                            "dc_sg": "SG",
                            "plant_id": plant_id
                        }
                        await self.supply_repository.bulk_insert_supply_serve_with_deduplication(
                            updated_sg_entities
                        )
                        logger.info(f"Updated {len(updated_sg_entities)} SG records for {plant_id} with plant-level multiplier: {multiplier}")

                except Exception as e:
                    logger.error(f"Error processing SG updates for plant {plant_id}: {e}")
                    processor_success = False

            # 2. Update DC Records (using the calculated multipliers)
            # Fetch ALL DC records for the range
            dc_filter_all = {
                "scheduled_at": {"$gte": start_range, "$lt": end_range},
                "dc_sg": "DC"
            }
            dc_records = await self.supply_repository.collection.find(
                dc_filter_all
            ).to_list(length=10000)
            
            # Group DC records by Plant
            dc_by_plant = {}
            for rec in dc_records:
                p_id = rec.get("plant_id")
                if p_id:
                    if p_id not in dc_by_plant: dc_by_plant[p_id] = []
                    dc_by_plant[p_id].append(rec)
            
            # Process DC Records by Plant
            for plant_id, records in dc_by_plant.items():
                try:
                    updated_dc_entities = []
                    
                    for dc_rec in records:
                        sch_at = dc_rec.get("scheduled_at")
                        if isinstance(sch_at, datetime):
                            d_str = sch_at.date().isoformat()
                        else:
                            d_str = str(sch_at)[:10]
                        tb = dc_rec.get("time_block")
                        
                        # Lookup unique block multiplier
                        multiplier = multiplier_map.get((plant_id, d_str, tb), 0.0)
                        
                        # Apply multiplier to modify DC volume
                        current_vol = self._safe_float(dc_rec.get("volume", 0))
                        # dc_afterloss = volume * multiplier (where multiplier = sum(volume)/sum(sg_beforeloss))
                        dc_afterloss = current_vol * multiplier
                        ramp_val = dc_afterloss * 0.10
                        if dc_rec.get("dc_sg") == "DC":
                            dam_tm_value = dc_afterloss * 0.55
                        else:
                            dam_tm_value = 0.0
                        try:
                            updated_dc_entity = SupplyServeEntity(
                                scheduled_at=dc_rec["scheduled_at"],
                                time_block=tb,
                                dc_sg="DC",
                                plant_id=plant_id,
                                volume=current_vol,
                                sg_beforelosses=dc_rec.get("sg_beforelosses"),
                                fc_price=dc_rec.get("fc_price"),
                                px_price=dc_rec.get("px_price"),
                                vc_price=dc_rec.get("vc_price"),
                                price_group=dc_rec.get("price_group"),
                                dam_tm= dam_tm_value,
                                rtm_tm= dc_rec.get("rtm_tm"),
                                multiplier=multiplier, # UPDATED from lookup
                                ramping=dc_rec.get("ramping", ""),
                                ramp_up=ramp_val, # UPDATED
                                ramp_down=ramp_val, # UPDATED
                                dc_afterloss=dc_afterloss, # UPDATED
                                source=dc_rec.get("source"),
                                inserted_at=dc_rec.get("inserted_at", datetime.now()),
                                updated_at=datetime.now(),
                                created_at=dc_rec.get("created_at", datetime.now()),
                                processed_at=datetime.now(),
                                timestamp=dc_rec.get("timestamp", datetime.now()),
                                updated_flag=UpdatedFlag.UPDATED,
                                source_revision_number=dc_rec.get("source_revision_number"),
                                system_revision_number=dc_rec.get("system_revision_number", 0) + 1
                            )
                            updated_dc_entities.append(updated_dc_entity)
                        except Exception as entity_err:
                             logger.error(f"Failed to create updated DC entity for {plant_id}: {entity_err}")
                    
                    # Perform Delete-Insert for DC (Batch per plant)
                    if updated_dc_entities:
                        plant_dc_filter = {
                            "scheduled_at": {"$gte": start_range, "$lt": end_range},
                            "dc_sg": "DC",
                            "plant_id": plant_id
                        }
                        await self.supply_repository.bulk_insert_supply_serve_with_deduplication(
                            updated_dc_entities
                        )
                        
                        # Also Update DC SG (staging) - optional but good for consistency
                        # This is usually standard collection so update_many is fine, or one-by-one
                        # Keeping it simple: We are focusing on supply_serve fix.
                        
                        logger.info(f"Updated {len(updated_dc_entities)} DC records for {plant_id} with block-wise multipliers.")
                        
                except Exception as e:
                    logger.error(f"Error processing DC updates for plant {plant_id}: {e}")
                    processor_success = False

            return processor_success

        except Exception as e:
            logger.error(f"Error in _calculate_and_apply_sg_multiplier: {str(e)}")
            return False

    async def _update_demand_schedule(self, target_dates: List[Any]) -> bool:
        """
        Update the schedule field in demand_serve based on aggregated supply volume.
        """
        try:
            if not target_dates:
                return True
            
            # Calculate date range for aggregation
            sorted_dates = sorted(target_dates)
            start_date = datetime.combine(sorted_dates[0], datetime.min.time())
            # Use start of next day for exclusive upper bound
            end_date = datetime.combine(sorted_dates[-1] + timedelta(days=1), datetime.min.time())
            
            # Aggregate supply schedule
            aggregated_schedule = await self.supply_repository.aggregate_supply_schedule(
                start_date, 
                end_date
            )
            
            if not aggregated_schedule:
                logger.info("No aggregated supply schedule found to update demand.")
                return True
            
            # Group aggregated schedule by date and time_block
            schedule_map = {}
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
                    "central_schedule": item.get("central_schedule", 0),
                    "state_schedule": item.get("state_schedule", 0),
                    "total_schedule": item.get("total_schedule", 0)
                }
            
            updated_entities = []
            
            # Process each date
            for date_key in schedule_map:
                # Get existing demand records for this date
                demand_records = await self.demand_repository.find_demand_serve_by_date(
                    datetime.combine(date_key, datetime.min.time())
                )
                
                for record in demand_records:
                    # Only update IDF records
                    if record.get("type") != "IDF":
                        continue

                    time_block = record.get("time_block")
                    if time_block in schedule_map[date_key]:
                        schedule_data = schedule_map[date_key][time_block]
                        
                        # Logic:
                        # If Drawal is available (and not 0/None) -> Schedule = Central Plants
                        # Else -> Schedule = Central + State Plants
                        
                        drawal = record.get("drawal")
                        # Always use total schedule (Central + State) for OD/UD calculation
                        # Previously, this incorrectly used only Central schedule when Drawal was present,
                        # which caused the OD/UD formula (Actual - (Schedule - IntraState)) to use the wrong baseline.
                        new_schedule = schedule_data["central_schedule"] + schedule_data["state_schedule"]
                        
                        # Create entity with updated schedule
                        # We need to preserve all other fields
                        try:
                            # Convert record to entity to validate and ensure structure
                            entity = DemandServeEntity(**record)
                            entity.schedule = new_schedule
                            entity.updated_at = datetime.now()
                            entity.updated_flag = UpdatedFlag.UPDATED
                            updated_entities.append(entity)
                        except Exception as e:
                            logger.warning(f"Failed to create DemandServeEntity from record: {e}")
                            continue
            
            if updated_entities:
                result = await self.demand_repository.bulk_insert_demand_serve_with_deduplication(updated_entities)
                logger.info(f"Updated demand schedule: {result}")
            return True
            
        except Exception as e:
            logger.error(f"Error updating demand schedule: {str(e)}")
            return False

    async def _cleanup_old_data(self, supply_cutoff_date):
        """
        Clean up old data from supply_serve collection by moving to historical
        """
        try:
            await self._move_old_supply_data_to_historical(supply_cutoff_date)
        except Exception as e:
            logger.error("Error cleaning up old data: %s", str(e))

    async def _move_old_supply_data_to_historical(self, cutoff_date):
        """
        Move data older than cutoff_date from supply_serve to supply_serve_historical
        """
        try:
            cutoff_datetime = datetime.combine(cutoff_date, datetime.min.time())
            
            old_records = await self.supply_repository.find_supply_serve_by_date_range(
                start_date=datetime.min,
                end_date=cutoff_datetime
            )
            
            if not old_records:
                return

            logger.info(f"Found {len(old_records)} old supply records to move to historical (before {cutoff_date})")
            
            historical_entities = [SupplyServeHistoricalEntity(**record, archived_at=datetime.now()) for record in old_records]
            
            if historical_entities:
                self.supply_repository.bulk_insert_supply_historical_with_deduplication(historical_entities)
                self.supply_repository.delete_supply_serve_by_date(cutoff_datetime)
            
        except Exception as e:
            logger.error("Error moving old supply data to historical: %s", str(e))

    def _has_supply_data_changed(self, existing_record: Dict[str, Any], new_record: Dict[str, Any]) -> bool:
        """
        Check if supply data has changed by comparing key fields
        """
        compare_fields = [
            "volume", "fc_price", "px_price", "vc_price", "price_group", 
            "tm", "multiplier", "ramping"
        ]
        
        for field in compare_fields:
            existing_value = existing_record.get(field)
            new_value = new_record.get(field)
            
            if new_value is None:
                continue
            
            if isinstance(existing_value, (int, float)) and isinstance(new_value, (int, float)):
                if abs(float(existing_value) - float(new_value)) > 1e-9:
                    return True
            elif str(existing_value) != str(new_value):
                return True
        
        return False

    @staticmethod
    def _safe_float(value: Any) -> float:
        if value is None: return 0.0
        try: return float(value)
        except (ValueError, TypeError): return 0.0
    
    @staticmethod
    def _safe_int(value: Any) -> Optional[int]:
        if value is None: return None
        try: return int(value)
        except (ValueError, TypeError): return None
    


    async def execute_archival_job(self) -> Dict[str, Any]:
        """
        Execute supply data archival as a separate cron job.
        """
        job_type = "supply_serve_archival"
        job_name = "Supply Data Archival"
        schedule = "0 2 * * *"
        start_time = datetime.now()
        
        execution_id = await self._record_job_start(job_type, job_name, schedule, start_time)
        if not execution_id:
            return {"status": "error", "message": "Failed to record job start"}
        
        self.set_correlation_id(execution_id)
        
        logger.info("=== EXECUTING SUPPLY DATA ARCHIVAL JOB ===")
        
        try:
            archive_result = self.supply_repository.archive_old_supply_data()
            
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            
            if archive_result.get("error"):
                raise Exception(archive_result["error"])

            success_result = {
                "status": "success",
                "message": f"Archived {archive_result.get('archived_count', 0)} supply records",
                "execution_time": execution_time,
                **archive_result
            }
            
            await self._record_job_completion(
                job_type, job_name, schedule, start_time, end_time,
                execution_id, "success", True, success_result
            )
            return success_result
                
        except Exception as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            error_result = {
                "status": "error",
                "message": f"Supply data archival error: {str(e)}",
                "execution_time": execution_time,
            }
            
            await self._record_job_completion(
                job_type, job_name, schedule, start_time, end_time,
                execution_id, "error", False, error_result, str(e)
            )
            return error_result
        finally:
            self.clear_correlation_id()
