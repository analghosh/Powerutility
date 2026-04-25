"""
Processes the current time block's data from the temp demand collection (temp_demand_serve)
and writes it into demand_serve using the same formulas as the manual demand service.
Designed to run every 15 minutes via cron.
"""

import logging
from datetime import datetime, timedelta, time
from typing import Dict, Any, List, Optional

from .base_processor import BaseProcessor
from libs.helpers.demand_calculations import calculate_demand_metrics
from libs.enum.updated_flag import UpdatedFlag
from ..repository.demand_serve_repository import demand_serve_repository
from ..repository.supply_serve_repository import supply_serve_repository
from ..repository.exchange_repository import ExchangeRepository
from ..core.config import settings

logger = logging.getLogger(__name__)


class DemandTempSyncProcessor(BaseProcessor):
    """
    Every 15 minutes, reads the current time block's record from temp_demand_serve
    and applies full demand calculations before saving to demand_serve.
    """

    def __init__(self):
        super().__init__()
        self.demand_repository = demand_serve_repository
        self.supply_repository = supply_serve_repository
        self.exchange_repository = ExchangeRepository()

        from ..repository.master_plants_repository import MasterPlantsRepository
        self.master_plants_repository = MasterPlantsRepository()

        temp_collection_name = (
            getattr(settings, "MONGODB_TEMP_DEMAND_SERVE_COLLECTION", None)
            or "temp_demand_serve"
        )
        self._temp_collection_name = temp_collection_name

    # ------------------------------------------------------------------
    # Cron entry point
    # ------------------------------------------------------------------

    async def execute_cron_job(self) -> Dict[str, Any]:
        job_type = "demand_temp_sync"
        job_name = "Demand Temp Sync"
        schedule = "*/15 * * * *"
        start_time = datetime.now()

        execution_id = self._record_job_start(job_type, job_name, schedule, start_time)
        self.set_correlation_id(execution_id or "")

        logger.info("=== EXECUTING DEMAND TEMP SYNC JOB ===")

        try:
            synced, skipped, failed = await self.sync_all_blocks_up_to_current()

            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()

            result = {
                "status": "success" if failed == 0 else "partial",
                "message": f"Demand temp sync completed — synced={synced}, skipped={skipped}, failed={failed}",
                "synced": synced,
                "skipped": skipped,
                "failed": failed,
                "execution_time": execution_time,
                "data_type": "demand_temp_sync",
            }

            self._record_job_completion(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=end_time,
                execution_id=execution_id or "",
                status=result["status"],
                success=failed == 0,
                result=result,
            )
            return result

        except Exception as e:
            logger.error(f"Error in demand temp sync job: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}
        finally:
            self.clear_correlation_id()

    # ------------------------------------------------------------------
    # Core sync logic
    # ------------------------------------------------------------------

    async def sync_all_blocks_up_to_current(self):
        """
        Sync every temp demand block from 1 through the current time block.
        Demand multiplier is fetched once and reused across all blocks.

        Returns:
            Tuple[int, int, int]: (synced_count, skipped_count, failed_count)
        """
        current_block = self._get_current_time_block()
        today = datetime.now().date()
        default_scheduled_at = datetime.combine(today, time(0, 0, 0))

        logger.info(f"Syncing all temp demand blocks 1–{current_block} for {today}")

        # Fetch demand multiplier once for the entire run
        all_plants = await self.master_plants_repository.get_all_plants()
        demand_multiplier = self._calculate_demand_multiplier(all_plants)
        logger.info(f"Demand multiplier (shared): {demand_multiplier}")

        synced = skipped = failed = 0

        for block_num in range(1, current_block + 1):
            try:
                did_sync = await self._sync_single_block(
                    block_num=block_num,
                    default_scheduled_at=default_scheduled_at,
                    demand_multiplier=demand_multiplier,
                )
                if did_sync:
                    synced += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.error(f"Error syncing block {block_num}: {e}", exc_info=True)
                failed += 1

        logger.info(f"Sync complete — synced={synced}, skipped={skipped}, failed={failed}")
        return synced, skipped, failed

    async def _sync_single_block(
        self,
        block_num: int,
        default_scheduled_at: datetime,
        demand_multiplier: float,
    ) -> bool:
        """
        Sync one temp demand block into demand_serve.

        Args:
            block_num: Time block (1–96).
            default_scheduled_at: Fallback scheduled_at (today midnight) if not in temp record.
            demand_multiplier: Pre-calculated multiplier (shared across the batch).

        Returns:
            True if the block was synced, False if skipped (no temp record).
        """
        temp_record = await self.demand_repository.db[self._temp_collection_name].find_one(
            {"time_block": block_num}
        )

        if not temp_record:
            return False

        drawal = float(temp_record.get("drawal") or 0)
        rostering = float(temp_record.get("rostering") or 0)
        frequency = float(temp_record.get("frequency") or 0)

        scheduled_at = default_scheduled_at
        if temp_record.get("scheduled_at"):
            raw_sa = temp_record["scheduled_at"]
            if isinstance(raw_sa, str):
                try:
                    scheduled_at = datetime.fromisoformat(raw_sa.replace("Z", "+00:00"))
                except Exception:
                    pass
            elif isinstance(raw_sa, datetime):
                scheduled_at = raw_sa

        drawal_before_loss = (drawal / demand_multiplier) if demand_multiplier > 0 else 0.0

        if drawal == 0:
            actual = 0.0
        else:
            supply_volume = await self._get_supply_volume(scheduled_at, block_num)
            actual = drawal + supply_volume

        exchange_data = await self._get_exchange_data(scheduled_at, block_num)
        acp = exchange_data["acp"]
        rtm_data_val = exchange_data["rtm_data"]
        dam_data_val = exchange_data["dam_data"]

        idf_existing = await self.demand_repository.serve_collection.find_one(
            {"scheduled_at": scheduled_at, "time_block": block_num, "type": "IDF"}
        )
        forecast = float(idf_existing.get("forecast") or 0) if idf_existing else 0.0
        schedule_val = float(idf_existing.get("schedule") or 0) if idf_existing else 0.0

        metrics = calculate_demand_metrics(
            actual=actual,
            forecast=forecast,
            frequency=frequency,
            time_block=block_num,
            rostering=rostering,
            rtm_data=rtm_data_val,
            dam_data=dam_data_val,
            acp=acp,
            drawal=drawal,
            schedule=schedule_val,
        )

        await self._save_demand_record(
            scheduled_at=scheduled_at,
            time_block=block_num,
            type_val="IDF",
            drawal=drawal,
            actual=actual,
            rostering=rostering,
            frequency=frequency,
            forecast=forecast,
            schedule=schedule_val,
            metrics=metrics,
            demand_multiplier=demand_multiplier,
            drawal_before_loss=drawal_before_loss,
            existing_record=idf_existing,
        )

        daf_existing = await self.demand_repository.serve_collection.find_one(
            {"scheduled_at": scheduled_at, "time_block": block_num, "type": "DAF"}
        )
        if daf_existing:
            daf_forecast = float(daf_existing.get("forecast") or 0)
            daf_schedule = float(daf_existing.get("schedule") or 0)

            daf_metrics = calculate_demand_metrics(
                actual=actual,
                forecast=daf_forecast,
                frequency=frequency,
                time_block=block_num,
                rostering=rostering,
                rtm_data=rtm_data_val,
                dam_data=dam_data_val,
                acp=acp,
                drawal=drawal,
                schedule=daf_schedule,
            )

            await self._save_demand_record(
                scheduled_at=scheduled_at,
                time_block=block_num,
                type_val="DAF",
                drawal=drawal,
                actual=actual,
                rostering=rostering,
                frequency=frequency,
                forecast=daf_forecast,
                schedule=daf_schedule,
                metrics=daf_metrics,
                demand_multiplier=demand_multiplier,
                drawal_before_loss=drawal_before_loss,
                existing_record=daf_existing,
            )

        logger.info(f"Block {block_num} synced (scheduled_at={scheduled_at})")
        return True

    async def sync_current_block(self) -> bool:
        try:
            current_block = self._get_current_time_block()
            today = datetime.now().date()
            scheduled_at = datetime.combine(today, time(0, 0, 0))

            logger.info(f"Syncing temp demand for block {current_block}, date {today}")

            temp_record = await self.demand_repository.db[self._temp_collection_name].find_one(
                {"time_block": current_block}
            )

            if not temp_record:
                logger.info(f"No temp demand record found for block {current_block} — skipping.")
                return True

            drawal = float(temp_record.get("drawal") or 0)
            rostering = float(temp_record.get("rostering") or 0)
            frequency = float(temp_record.get("frequency") or 0)

            # Use scheduled_at from the temp record if present
            if temp_record.get("scheduled_at"):
                raw_sa = temp_record["scheduled_at"]
                if isinstance(raw_sa, str):
                    try:
                        scheduled_at = datetime.fromisoformat(raw_sa.replace("Z", "+00:00"))
                    except Exception:
                        pass
                elif isinstance(raw_sa, datetime):
                    scheduled_at = raw_sa

            # 1. Demand multiplier
            all_plants = await self.master_plants_repository.get_all_plants()
            demand_multiplier = self._calculate_demand_multiplier(all_plants)
            logger.info(f"Demand multiplier: {demand_multiplier}")

            # 2. drawal_before_loss
            drawal_before_loss = (drawal / demand_multiplier) if demand_multiplier > 0 else 0.0

            # 3. actual = drawal + supply_volume (0 if drawal == 0)
            if drawal == 0:
                actual = 0.0
            else:
                supply_volume = await self._get_supply_volume(scheduled_at, current_block)
                actual = drawal + supply_volume

            # 4. Exchange data
            exchange_data = await self._get_exchange_data(scheduled_at, current_block)
            acp = exchange_data["acp"]
            rtm_data_val = exchange_data["rtm_data"]
            dam_data_val = exchange_data["dam_data"]

            # 5. IDF record
            idf_existing = await self.demand_repository.serve_collection.find_one(
                {"scheduled_at": scheduled_at, "time_block": current_block, "type": "IDF"}
            )
            forecast = float(idf_existing.get("forecast") or 0) if idf_existing else 0.0
            schedule_val = float(idf_existing.get("schedule") or 0) if idf_existing else 0.0

            metrics = calculate_demand_metrics(
                actual=actual,
                forecast=forecast,
                frequency=frequency,
                time_block=current_block,
                rostering=rostering,
                rtm_data=rtm_data_val,
                dam_data=dam_data_val,
                acp=acp,
                drawal=drawal,
                schedule=schedule_val,
            )

            await self._save_demand_record(
                scheduled_at=scheduled_at,
                time_block=current_block,
                type_val="IDF",
                drawal=drawal,
                actual=actual,
                rostering=rostering,
                frequency=frequency,
                forecast=forecast,
                schedule=schedule_val,
                metrics=metrics,
                demand_multiplier=demand_multiplier,
                drawal_before_loss=drawal_before_loss,
                existing_record=idf_existing,
            )

            # 6. Sync DAF if it exists
            daf_existing = await self.demand_repository.serve_collection.find_one(
                {"scheduled_at": scheduled_at, "time_block": current_block, "type": "DAF"}
            )
            if daf_existing:
                daf_forecast = float(daf_existing.get("forecast") or 0)
                daf_schedule = float(daf_existing.get("schedule") or 0)

                daf_metrics = calculate_demand_metrics(
                    actual=actual,
                    forecast=daf_forecast,
                    frequency=frequency,
                    time_block=current_block,
                    rostering=rostering,
                    rtm_data=rtm_data_val,
                    dam_data=dam_data_val,
                    acp=acp,
                    drawal=drawal,
                    schedule=daf_schedule,
                )

                await self._save_demand_record(
                    scheduled_at=scheduled_at,
                    time_block=current_block,
                    type_val="DAF",
                    drawal=drawal,
                    actual=actual,
                    rostering=rostering,
                    frequency=frequency,
                    forecast=daf_forecast,
                    schedule=daf_schedule,
                    metrics=daf_metrics,
                    demand_multiplier=demand_multiplier,
                    drawal_before_loss=drawal_before_loss,
                    existing_record=daf_existing,
                )

            logger.info(f"Demand temp sync complete for block {current_block}.")
            return True

        except Exception as e:
            logger.error(f"Critical error in sync_current_block: {e}", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_current_time_block(self) -> int:
        """Return current 15-min time block (1–96)."""
        now = datetime.now()
        return (now.hour * 4) + (now.minute // 15) + 1

    def _calculate_demand_multiplier(self, plants: List[Dict[str, Any]]) -> float:
        excluded_ids = {"OA_PX", "OA_GNA", "OA_TGNA"}
        valid = [
            p["multiplier"]
            for p in plants
            if p.get("plant_id") not in excluded_ids
            and str(p.get("c_s") or "").lower() != "state"
            and p.get("multiplier") and p["multiplier"] > 0
        ]
        return sum(valid) / len(valid) if valid else 0.0

    async def _get_supply_volume(self, scheduled_at: datetime, time_block: int) -> float:
        try:
            record = await self.supply_repository.serve_collection.find_one(
                {
                    "scheduled_at": scheduled_at,
                    "time_block": time_block,
                    "dc_sg": "SG",
                    "plant_id": "PPCLGPP",
                }
            )
            return float(record.get("volume") or 0) if record else 0.0
        except Exception as e:
            logger.error(f"Error fetching supply volume: {e}")
            return 0.0

    async def _get_exchange_data(self, scheduled_at: datetime, time_block: int) -> Dict[str, float]:
        try:
            rtm_record = await self.exchange_repository.find_one(
                {"scheduled_at": scheduled_at, "time_block": time_block, "market_type": "RTM"}
            )
            dam_record = await self.exchange_repository.find_one(
                {"scheduled_at": scheduled_at, "time_block": time_block, "market_type": "DAM"}
            )

            rtm_val = float(rtm_record.get("acp") or rtm_record.get("mcp") or 0) if rtm_record else 0.0
            dam_val = float(dam_record.get("acp") or dam_record.get("mcp") or 0) if dam_record else 0.0

            if rtm_record and (rtm_record.get("acp") or 0) > 0:
                acp = float(rtm_record["acp"])
            elif dam_record and (dam_record.get("acp") or 0) > 0:
                acp = float(dam_record["acp"])
            elif rtm_record and (rtm_record.get("mcp") or 0) > 0:
                acp = float(rtm_record["mcp"])
            elif dam_record and (dam_record.get("mcp") or 0) > 0:
                acp = float(dam_record["mcp"])
            else:
                acp = 100.0

            return {"acp": acp, "rtm_data": rtm_val, "dam_data": dam_val}
        except Exception as e:
            logger.error(f"Error fetching exchange data: {e}")
            return {"acp": 100.0, "rtm_data": 0.0, "dam_data": 0.0}

    async def _save_demand_record(
        self,
        scheduled_at: datetime,
        time_block: int,
        type_val: str,
        drawal: float,
        actual: float,
        rostering: float,
        frequency: float,
        forecast: float,
        schedule: float,
        metrics: Dict[str, Any],
        demand_multiplier: float,
        drawal_before_loss: float,
        existing_record: Optional[Dict[str, Any]],
    ) -> None:
        filter_query = {"scheduled_at": scheduled_at, "time_block": time_block, "type": type_val}
        now = datetime.now()

        full_document = {
            "scheduled_at": scheduled_at,
            "time_block": time_block,
            "type": type_val,
            "drawal": drawal,
            "rostering": rostering,
            "frequency": frequency,
            "actual": actual,
            "forecast": forecast,
            "schedule": schedule,
            "demand_multiplier": demand_multiplier,
            "drawal_before_loss": drawal_before_loss,
            "mpe": metrics.get("mpe", 0),
            "mape": metrics.get("mape", 0),
            "comp_act_demand": metrics.get("comp_act_demand", 0),
            "comp_udm": metrics.get("comp_udm", 0),
            "net_dsm_amount": metrics.get("net_dsm_amount", 0),
            "dsm_rate": metrics.get("dsm_rate", 0),
            "updated_flag": UpdatedFlag.UPDATED.value,
            "updated_at": now,
            "timestamp": scheduled_at,
            "created_at": existing_record.get("created_at", now) if existing_record else now,
            "inserted_at": existing_record.get("inserted_at", now) if existing_record else now,
        }

        await self.demand_repository.serve_collection.delete_many(filter_query)
        await self.demand_repository.serve_collection.insert_one(full_document)

        full_document_hist = {**full_document, "archived_at": now, "archived_timestamp": now}
        await self.demand_repository.historical_collection.delete_many(filter_query)
        await self.demand_repository.historical_collection.insert_one(full_document_hist)

    async def process_raw_data_to_serve(
        self,
        raw_data: List[Dict[str, Any]],
        metadata: Dict[str, Any],
        provider_config: Dict[str, Any],
    ) -> bool:
        return True
