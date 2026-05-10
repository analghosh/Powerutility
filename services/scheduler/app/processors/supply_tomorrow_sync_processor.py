"""
Synchronizes supply data from the staging collection (dc_sg) to the final collection (supply_serve) for "Tomorrow".
Designed to run independently of the main supply processor.
"""

import logging
import asyncio
from datetime import datetime, timedelta, time
from typing import Dict, Any, List, Optional

from .base_processor import BaseProcessor
from libs.models.dc_sg_entity import DCSgEntity
from libs.models.supply_serve_entity import SupplyServeEntity
from libs.enum.updated_flag import UpdatedFlag
from ..repository.supply_serve_repository import supply_serve_repository
from ..repository.dc_sg_repository import dc_sg_repository
from ..repository.plant_repository import plant_repository
from ..core.config import settings

logger = logging.getLogger(__name__)

class SupplyTomorrowSyncProcessor(BaseProcessor):
    """
    Dedicated processor to force-sync 'Tomorrow's' data from dc_sg to supply_serve.
    """

    def __init__(self):
        super().__init__()
        self.supply_repository = supply_serve_repository
        self.dc_sg_repository = dc_sg_repository
        self.plant_repository = plant_repository

    async def execute_cron_job(self) -> Dict[str, Any]:
        """
        Execute tomorrow sync job.
        """
        job_type = "supply_tomorrow_sync"
        job_name = "Supply Tomorrow Sync"
        schedule = "manual/trigger"
        start_time = datetime.now()

        execution_id = await self._record_job_start(job_type, job_name, schedule, start_time)
        self.set_correlation_id(execution_id)

        logger.info("=== EXECUTING SUPPLY TOMORROW SYNC JOB ===")

        try:
            success = await self.sync_tomorrow_data()
            
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            
            result = {
                "status": "success" if success else "error",
                "message": "Tomorrow sync completed successfully" if success else "Tomorrow sync failed or incomplete",
                "execution_time": execution_time,
                "data_type": "supply_sync"
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
            logger.error(f"Error in tomorrow sync job: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}
        finally:
            self.clear_correlation_id()

    def _records_to_dc_sg_entities(self, records: list, target_date, source_label: str) -> list:
        """
        Clone dc_sg records, shifting scheduled_at to target_date.
        Returns a list of DCSgEntity objects ready for bulk upsert.
        """
        entities = []
        for rec in records:
            src_scheduled_at = rec.get("scheduled_at")
            if not isinstance(src_scheduled_at, datetime):
                continue
            # Preserve the time component (time_block slot), just move the date
            new_scheduled_at = src_scheduled_at.replace(
                year=target_date.year,
                month=target_date.month,
                day=target_date.day
            )
            entities.append(DCSgEntity(
                scheduled_at=new_scheduled_at,
                time_block=rec.get("time_block"),
                plant_id=rec.get("plant_id"),
                dc_sg=rec.get("dc_sg"),
                volume=rec.get("volume"),
                sg_beforelosses=rec.get("sg_beforelosses"),
                fc_price=rec.get("fc_price"),
                px_price=rec.get("px_price"),
                vc_price=rec.get("vc_price"),
                price_group=rec.get("price_group"),
                dam_tm=rec.get("dam_tm", 0.0),
                rtm_tm=rec.get("rtm_tm", 0.0),
                multiplier=rec.get("multiplier", 0.0),
                ramping=rec.get("ramping", ""),
                ramp_up=rec.get("ramp_up"),
                ramp_down=rec.get("ramp_down"),
                dc_afterloss=rec.get("dc_afterloss"),
                process_type=rec.get("process_type"),
                updated_flag=rec.get("updated_flag"),
                max_mw_dc=rec.get("max_mw_dc"),
                eightThirtyValue=rec.get("eightThirtyValue"),
                source=source_label,
                revision_no=rec.get("revision_no"),
            ))
        return entities

    async def sync_tomorrow_data(self) -> bool:
        """
        Fetches all records for Tomorrow from dc_sg and upserts them into supply_serve.

        Fallback: For any plant missing from tomorrow's dc_sg data, find the date
        (among yesterday / today / tomorrow) that has the most distinct plants and
        copy the missing plant's records — with scheduled_at shifted to tomorrow —
        into dc_sg before proceeding.
        """
        try:
            today = datetime.now().date()
            yesterday = today - timedelta(days=1)
            tomorrow = today + timedelta(days=1)

            logger.info(f"Targeting Tomorrow: {tomorrow}")

            # ── 1. Fetch dc_sg records for all three dates (no plant filter) ──────
            yesterday_records, today_records, tomorrow_records = await asyncio.gather(
                self.dc_sg_repository.get_records_by_date(yesterday),
                self.dc_sg_repository.get_records_by_date(today),
                self.dc_sg_repository.get_records_by_date(tomorrow),
            )

            # ── 2. Distinct plant sets per date ───────────────────────────────────
            def plant_set(records):
                return {r.get("plant_id") for r in records if r.get("plant_id")}

            plants_yesterday = plant_set(yesterday_records)
            plants_today     = plant_set(today_records)
            plants_tomorrow  = plant_set(tomorrow_records)

            logger.info(
                f"Distinct plants — yesterday: {len(plants_yesterday)}, "
                f"today: {len(plants_today)}, tomorrow: {len(plants_tomorrow)}"
            )

            # ── 3. Find the date with the most plants (best source) ───────────────
            date_plant_counts = {
                yesterday: (len(plants_yesterday), yesterday_records, plants_yesterday),
                today:     (len(plants_today),     today_records,     plants_today),
                tomorrow:  (len(plants_tomorrow),  tomorrow_records,  plants_tomorrow),
            }
            best_date = max(date_plant_counts, key=lambda d: date_plant_counts[d][0])
            _, best_records, best_plants = date_plant_counts[best_date]
            logger.info(f"Best source date: {best_date} with {len(best_plants)} plants")

            # ── 4. Fill missing plants into today and tomorrow ────────────────────
            source_label = "dc_sg_rollover"

            for target_date, target_plants, label in [
                (today,    plants_today,    "today"),
                (tomorrow, plants_tomorrow, "tomorrow"),
            ]:
                missing_plants = best_plants - target_plants
                if not missing_plants:
                    logger.info(f"No missing plants for {label}.")
                    continue

                logger.info(
                    f"{len(missing_plants)} plant(s) missing from {label}, "
                    f"copying from {best_date}: {missing_plants}"
                )
                missing_records = [r for r in best_records if r.get("plant_id") in missing_plants]
                entities_to_fill = self._records_to_dc_sg_entities(missing_records, target_date, source_label)

                if entities_to_fill:
                    fill_result = await self.dc_sg_repository.bulk_upsert_dc_sg(entities_to_fill)
                    logger.info(f"Filled missing plants into dc_sg for {label}: {fill_result}")

            # Re-fetch tomorrow after fills
            tomorrow_records = await self.dc_sg_repository.get_records_by_date(tomorrow)

            if not tomorrow_records:
                logger.warning("No records found for tomorrow even after fallback. Nothing to sync.")
                return True

            logger.info(f"Found {len(tomorrow_records)} records in dc_sg for tomorrow.")

            # 2. Convert to SupplyServeEntities
            entities_to_upsert = []
            
            # Helper to safely float
            def safe_float(val):
                try: return float(val) if val is not None else 0.0
                except: return 0.0
            def safe_int(val):
                try: return int(val) if val is not None else 0
                except: return 0

            for rec in tomorrow_records:
                try:
                    # Basic mapping
                    # Note: We are creating a functional duplicate of logic in SupplyProcessor.
                    # This is intentional to isolate the fix.
                    
                    # Ensure date time combined
                    scheduled_at = rec.get("scheduled_at")
                    if isinstance(scheduled_at, str):
                        try: scheduled_at = datetime.fromisoformat(scheduled_at.replace('Z', '+00:00'))
                        except: pass
                    
                    if not scheduled_at: 
                        continue
                        
                    # Recalculate TMs if needed or perform direct copy
                    vol = safe_float(rec.get("volume"))
                    
                    entity = SupplyServeEntity(
                        scheduled_at=scheduled_at,
                        time_block=safe_int(rec.get("time_block")),
                        dc_sg=str(rec.get("dc_sg")),
                        plant_id=str(rec.get("plant_id")),
                        volume=vol,
                        sg_beforelosses=safe_float(rec.get("sg_beforelosses")),
                        fc_price=safe_float(rec.get("fc_price")),
                        px_price=safe_float(rec.get("px_price")),
                        vc_price=safe_float(rec.get("vc_price")),
                        price_group=rec.get("price_group"),
                        # Use existing calculated TMs if present
                        dam_tm=safe_float(rec.get("dam_tm")),
                        rtm_tm=safe_float(rec.get("rtm_tm")),
                        multiplier=safe_float(rec.get("multiplier")),
                        ramping=str(rec.get("ramping", "")),
                        updated_flag=UpdatedFlag.UPDATED,
                        source=rec.get("source"),
                        revision_no=safe_int(rec.get("revision_no")),
                        system_revision_number=safe_int(rec.get("system_revision_number", 1)),
                        created_at=datetime.now(),
                        updated_at=datetime.now(),
                        processed_at=datetime.now(),
                        timestamp=datetime.now()
                    )
                    entities_to_upsert.append(entity)
                except Exception as inner_e:
                    logger.warning(f"Skipping record due to mapping error: {inner_e}")
                    continue

            if not entities_to_upsert:
                logger.info("No valid entities converted.")
                return True

            # 3. Bulk Upsert into Supply Serve
            logger.info(f"Upserting {len(entities_to_upsert)} entities into supply_serve...")

            res = await self.supply_repository.bulk_insert_supply_serve_with_deduplication(entities_to_upsert)
            
            logger.info(f"Sync complete. Result: {res}")
            return True

        except Exception as e:
            logger.error(f"Critical error in sync_tomorrow_data: {e}", exc_info=True)
            return False

    async def process_raw_data_to_serve(self, raw_data: List[Dict[str, Any]], metadata: Dict[str, Any], provider_config: Dict[str, Any]) -> bool:
        """
        No-op implementation to satisfy BaseProcessor abstract method.
        This processor is not triggered by standard raw data ingestion.
        """
        return True
