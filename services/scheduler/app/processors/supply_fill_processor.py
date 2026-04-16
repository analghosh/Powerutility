"""
Supply Data Fill Processor
Handles midnight filling of missing supply data for today based on historical dc_sg data.
"""

import logging
from datetime import datetime
from typing import Dict, List, Any, Optional

from .base_processor import BaseProcessor
from libs.models.supply_serve_entity import SupplyServeEntity
from libs.models.dc_sg_entity import DCSgEntity
from libs.enum.updated_flag import UpdatedFlag
from libs.core.job_entities import JobDetail
from ..repository.supply_serve_repository import supply_serve_repository
from ..repository.plant_repository import plant_repository
from ..repository.dc_sg_repository import dc_sg_repository
from ..repository.demand_serve_repository import demand_serve_repository
from ..core.config import settings

logger = logging.getLogger(__name__)


class SupplyFillProcessor(BaseProcessor):
    """
    Processor to fill missing supply data for today by picking the latest
    available data for each plant, time block, and type (DC/SG) from dc_sg history.
    """

    def __init__(self):
        super().__init__()
        self.supply_repository = supply_serve_repository
        self.plant_repository = plant_repository
        self.dc_sg_repository = dc_sg_repository
        self.demand_repository = demand_serve_repository

    async def execute_cron_job(self) -> Dict[str, Any]:
        """
        Execute the supply data fill as a midnight cron job.
        """
        job_type = "supply_data_midnight_fill"
        job_name = "Supply Data Midnight Fill"
        schedule = "0 0 * * *"
        start_time = datetime.now()

        execution_id = await self._record_job_start(job_type, job_name, schedule, start_time)
        if not execution_id:
            logger.error("Failed to record job start for %s", job_type)
            return {"status": "error", "message": "Failed to record job start"}

        self.set_correlation_id(execution_id)
        logger.info("=== EXECUTING SUPPLY DATA MIDNIGHT FILL JOB ===")

        try:
            metadata = {
                "data_type": "supply_fill",
                "scraped_at": start_time,
                "source": "supply_fill_processor",
                "triggered_by": "cron_job",
                "execution_id": execution_id,
                "correlation_id": execution_id
            }

            success = await self._process_fill(metadata)

            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()

            result = {
                "status": "success" if success else "error",
                "message": "Supply data filled successfully" if success else "Supply data filling failed",
                "execution_time": execution_time,
                "data_type": "supply",
                "correlation_id": execution_id,
                "cron_job": "supply_fill"
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

            return result

        except Exception as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            error_result = {
                "status": "error",
                "message": f"Supply data fill error: {str(e)}",
                "execution_time": execution_time,
                "data_type": "supply",
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
            logger.error("Error in supply fill job: %s", str(e))
            return error_result
        finally:
            self.clear_correlation_id()

    async def _process_fill(self, metadata: Dict[str, Any]) -> bool:
        """
        Logic to fill missing dc_sg and supply_serve data for today.
        """
        try:
            today = datetime.now().date()
            today_dt = datetime.combine(today, datetime.min.time())
            
            # 1. Identify existing records for today
            today_dc_sg_records = self.dc_sg_repository.get_records_by_date(today)
            today_supply_records = self.supply_repository.find_supply_serve_by_date(today_dt)
            if today_supply_records.count() > 0:
                logger.info("Supply data already exists for today")
                return True
            existing_keys = set()
            def create_key(r):
                return (
                    self._safe_int(r.get("time_block")),
                    str(r.get("plant_id")),
                    str(r.get("dc_sg"))
                )
            
            for r in today_dc_sg_records: existing_keys.add(create_key(r))
            for r in today_supply_records: existing_keys.add(create_key(r))
            
            # 2. Get all plants
            cursor = self.plant_repository.collection.find({})
            try:
                plants = await cursor.to_list(None)
            finally:
                cursor.close()
            new_dc_sg_entities = []
            
            for plant in plants:
                plant_id = plant.get("plant_id")
                if not plant_id: continue
                
                # Fetch latest historical record for each block and type
                historical_records = self.dc_sg_repository.get_latest_records_per_timeblock(plant_id)
                if not historical_records:
                    logger.debug(f"No history for plant {plant_id}")
                    continue
                
                for record in historical_records:
                    key = create_key(record)
                    if key in existing_keys:
                        continue
                    
                    # Create new today's entity
                    old_scheduled_at = record["scheduled_at"]
                    new_scheduled_at = datetime.combine(today, old_scheduled_at.time())
                    
                    # Calculate TM values for DC records (55% of volume)
                    volume_value = self._safe_float(record.get("dc_afterloss", 0.0))
                    volume = self._safe_float(record.get("volume", 0.0))
                    dc_sg_type = str(record.get("dc_sg")).upper()
                    if dc_sg_type == "DC":
                        dam_tm_value = volume_value * 0.55
                        rtm_tm_value = volume_value * 0.55
                    else:
                        dam_tm_value = 0.0
                        rtm_tm_value = 0.0
                    
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
                        dam_tm=dam_tm_value,
                        rtm_tm=rtm_tm_value,
                        multiplier=str(record.get("multiplier", "")),
                        ramping=str(record.get("ramping", "")),
                        source="dc_sg_rollover"
                    )
                    new_dc_sg_entities.append(entity)
            
            if not new_dc_sg_entities:
                logger.info("Nothing to fill for today.")
                return True

            # 3. Insert into dc_sg
            processed_into_dc_sg = 0
            for entity in new_dc_sg_entities:
                res = self.dc_sg_repository.update_or_insert_record(entity)
                if res.upserted_id or res.modified_count > 0:
                    processed_into_dc_sg += 1
            
            logger.info(f"Inserted {processed_into_dc_sg} new records into dc_sg for today.")

            # 4. Map to supply_serve entities and insert
            supply_entities = []
            for entity in new_dc_sg_entities:
                supply_entity = SupplyServeEntity(
                    scheduled_at=entity.scheduled_at,
                    time_block=entity.time_block,
                    dc_sg=entity.dc_sg,
                    volume=entity.volume,
                    fc_price=entity.fc_price,
                    px_price=entity.px_price,
                    vc_price=entity.vc_price,
                    price_group=entity.price_group,
                    dam_tm=entity.dam_tm,
                    rtm_tm=entity.rtm_tm,
                    multiplier=entity.multiplier,
                    ramping=entity.ramping,
                    updated_flag=UpdatedFlag.UPDATED,
                    plant_id=entity.plant_id,
                    source="dc_sg_rollover",
                    source_revision_number=1,
                    system_revision_number=1
                )
                supply_entities.append(supply_entity)
            
            if supply_entities:
                res = self.supply_repository.bulk_insert_supply_serve_with_deduplication(supply_entities)
                logger.info(f"Bulk inserted {res.get('inserted_count', 0)} entities into supply_serve.")
            
            # 5. Trigger demand schedule update for today
            try:
                # We need to import SupplyProcessor or similar to call _update_demand_schedule
                # Or just call the repository methods directly if logic is shared.
                # Since supply_processor._update_demand_schedule is complex, let's trigger it.
                from .supply_processor import SupplyProcessor
                sp = SupplyProcessor()
                sp._update_demand_schedule([today])
            except Exception as e:
                logger.warning(f"Demand schedule update failed: {e}")

            return True

        except Exception as e:
            logger.error(f"Error in _process_fill: {str(e)}")
            return False

    async def process_raw_data_to_serve(
        self, 
        raw_data: List[Dict[str, Any]], 
        metadata: Dict[str, Any],
        provider_config: Dict[str, Any]
    ) -> bool:
        """
        Implementation of abstract method from BaseProcessor.
        Not used in this processor as it handles missing data fill,
        not raw data processing.
        """
        return True

    def _safe_float(self, value: Any) -> float:
        try: return float(value) if value is not None else 0.0
        except: return 0.0

    def _safe_int(self, value: Any) -> int:
        try: return int(value) if value is not None else 0
        except: return 0


