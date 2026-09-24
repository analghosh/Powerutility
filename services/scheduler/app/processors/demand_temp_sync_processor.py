"""
Processes the current time block's data from the temp demand collection (temp_demand_serve)
and writes it into demand_serve using the same formulas as the manual demand service.
Designed to run every 15 minutes via cron.
"""

import asyncio
import logging
from datetime import datetime, timedelta, time
from typing import Dict, Any, List, Optional

from pymongo import DeleteOne, InsertOne, ReplaceOne

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

        execution_id = await self._record_job_start(job_type, job_name, schedule, start_time)
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

            await self._record_job_completion(
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

        Optimised: all data is fetched in 6 parallel batch queries, blocks are
        processed in-memory, and results are written with two bulk_write calls.
        This reduces ~10N sequential DB round trips to O(1) parallel round trips.

        Returns:
            Tuple[int, int, int]: (synced_count, skipped_count, failed_count)
        """
        current_block = self._get_current_time_block()
        today = datetime.now().date()
        default_scheduled_at = datetime.combine(today, time(0, 0, 0))
        blocks = list(range(1, current_block + 1))

        logger.info(f"Syncing all temp demand blocks 1–{current_block} for {today}")

        # ------------------------------------------------------------------ #
        # 1. Fetch all required data in parallel (6 queries, 1 round trip)   #
        # ------------------------------------------------------------------ #
        (
            all_plants,
            temp_records_list,
            idf_records_list,
            daf_records_list,
            supply_records_list,
            exchange_records_list,
        ) = await asyncio.gather(
            self.master_plants_repository.get_all_plants(),
            self.demand_repository.db[self._temp_collection_name]
                .find({"time_block": {"$in": blocks}})
                .to_list(length=current_block),
            self.demand_repository.serve_collection
                .find({"scheduled_at": default_scheduled_at, "time_block": {"$in": blocks}, "type": "IDF"})
                .to_list(length=current_block),
            self.demand_repository.serve_collection
                .find({"scheduled_at": default_scheduled_at, "time_block": {"$in": blocks}, "type": "DAF"})
                .to_list(length=current_block),
            self.supply_repository.serve_collection
                .find({"scheduled_at": default_scheduled_at, "time_block": {"$in": blocks}, "dc_sg": "SG", "plant_id": "PPCLGPP"})
                .to_list(length=current_block),
            self.exchange_repository.serve_collection
                .find({"scheduled_at": default_scheduled_at, "time_block": {"$in": blocks}, "market_type": {"$in": ["RTM", "DAM"]}})
                .to_list(length=current_block * 2),
        )

        # ------------------------------------------------------------------ #
        # 2. Build in-memory lookup dicts                                     #
        # ------------------------------------------------------------------ #
        demand_multiplier = self._calculate_demand_multiplier(all_plants)
        logger.info(f"Demand multiplier (shared): {demand_multiplier}")

        temp_by_block = {r["time_block"]: r for r in temp_records_list}
        idf_by_block = {r["time_block"]: r for r in idf_records_list}
        daf_by_block = {r["time_block"]: r for r in daf_records_list}
        supply_by_block = {r["time_block"]: r for r in supply_records_list}
        exchange_by_block_type = {
            (r["time_block"], r["market_type"]): r for r in exchange_records_list
        }

        synced = skipped = failed = 0
        now = datetime.now()
        serve_ops: List = []
        hist_ops: List = []

        # ------------------------------------------------------------------ #
        # 3. Process every block in-memory — no DB calls inside the loop     #
        # ------------------------------------------------------------------ #
        for block_num in blocks:
            try:
                temp_record = temp_by_block.get(block_num)
                if not temp_record:
                    skipped += 1
                    continue

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

                # The lookups above are all for today, so a temp row for any
                # other day would be written under that day with today's
                # forecast and inserted_at. temp_demand_serve is keyed on
                # time_block alone and can already hold tomorrow's rows.
                if scheduled_at.date() != today:
                    logger.warning(
                        f"Skipping temp block {block_num}: dated {scheduled_at.date()}, not {today}"
                    )
                    skipped += 1
                    continue

                drawal_before_loss = (drawal / demand_multiplier) if demand_multiplier > 0 else 0.0

                if drawal == 0:
                    actual = 0.0
                else:
                    supply_rec = supply_by_block.get(block_num)
                    supply_volume = float(supply_rec.get("volume") or 0) if supply_rec else 0.0
                    actual = drawal + supply_volume

                rtm_record = exchange_by_block_type.get((block_num, "RTM"))
                dam_record = exchange_by_block_type.get((block_num, "DAM"))
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

                idf_existing = idf_by_block.get(block_num)
                forecast = float(idf_existing.get("forecast") or 0) if idf_existing else 0.0
                schedule_val = float(idf_existing.get("schedule") or 0) if idf_existing else 0.0

                metrics = calculate_demand_metrics(
                    actual=actual, forecast=forecast, frequency=frequency,
                    time_block=block_num, rostering=rostering,
                    rtm_data=rtm_val, dam_data=dam_val, acp=acp,
                    drawal=drawal, schedule=schedule_val,
                )
                self._collect_demand_ops(
                    serve_ops=serve_ops, hist_ops=hist_ops,
                    scheduled_at=scheduled_at, time_block=block_num, type_val="IDF",
                    drawal=drawal, actual=actual, rostering=rostering, frequency=frequency,
                    forecast=forecast, schedule=schedule_val, metrics=metrics,
                    demand_multiplier=demand_multiplier, drawal_before_loss=drawal_before_loss,
                    existing_record=idf_existing, now=now,
                )

                daf_existing = daf_by_block.get(block_num)
                if daf_existing:
                    daf_forecast = float(daf_existing.get("forecast") or 0)
                    daf_schedule = float(daf_existing.get("schedule") or 0)
                    daf_metrics = calculate_demand_metrics(
                        actual=actual, forecast=daf_forecast, frequency=frequency,
                        time_block=block_num, rostering=rostering,
                        rtm_data=rtm_val, dam_data=dam_val, acp=acp,
                        drawal=drawal, schedule=daf_schedule,
                    )
                    self._collect_demand_ops(
                        serve_ops=serve_ops, hist_ops=hist_ops,
                        scheduled_at=scheduled_at, time_block=block_num, type_val="DAF",
                        drawal=drawal, actual=actual, rostering=rostering, frequency=frequency,
                        forecast=daf_forecast, schedule=daf_schedule, metrics=daf_metrics,
                        demand_multiplier=demand_multiplier, drawal_before_loss=drawal_before_loss,
                        existing_record=daf_existing, now=now,
                    )

                synced += 1

            except Exception as e:
                logger.error(f"Error processing block {block_num}: {e}", exc_info=True)
                failed += 1

        # ------------------------------------------------------------------ #
        # 4. Flush all writes in two bulk operations                         #
        # ------------------------------------------------------------------ #
        if serve_ops:
            await self.demand_repository.serve_collection.bulk_write(serve_ops, ordered=False)
        if hist_ops:
            # Delete-then-insert pattern: all deletes first, then all inserts
            await self.demand_repository.historical_collection.bulk_write(
                hist_ops, ordered=True
            )

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
        Parallel fetches are used for independent DB queries within the block.

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

        # Fetch supply, exchange data, and existing demand records in parallel
        supply_coro = self._get_supply_volume(scheduled_at, block_num) if drawal != 0 else asyncio.sleep(0, result=0.0)
        exchange_coro = self._get_exchange_data(scheduled_at, block_num)
        idf_coro = self.demand_repository.serve_collection.find_one(
            {"scheduled_at": scheduled_at, "time_block": block_num, "type": "IDF"}
        )
        daf_coro = self.demand_repository.serve_collection.find_one(
            {"scheduled_at": scheduled_at, "time_block": block_num, "type": "DAF"}
        )

        supply_volume, exchange_data, idf_existing, daf_existing = await asyncio.gather(
            supply_coro, exchange_coro, idf_coro, daf_coro
        )

        actual = (drawal + supply_volume) if drawal != 0 else 0.0
        acp = exchange_data["acp"]
        rtm_data_val = exchange_data["rtm_data"]
        dam_data_val = exchange_data["dam_data"]

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

            # Fetch multiplier, supply, exchange, and existing records in parallel
            supply_coro = (
                self._get_supply_volume(scheduled_at, current_block)
                if drawal != 0 else asyncio.sleep(0, result=0.0)
            )
            all_plants, supply_volume, exchange_data, idf_existing, daf_existing = await asyncio.gather(
                self.master_plants_repository.get_all_plants(),
                supply_coro,
                self._get_exchange_data(scheduled_at, current_block),
                self.demand_repository.serve_collection.find_one(
                    {"scheduled_at": scheduled_at, "time_block": current_block, "type": "IDF"}
                ),
                self.demand_repository.serve_collection.find_one(
                    {"scheduled_at": scheduled_at, "time_block": current_block, "type": "DAF"}
                ),
            )

            demand_multiplier = self._calculate_demand_multiplier(all_plants)
            logger.info(f"Demand multiplier: {demand_multiplier}")

            drawal_before_loss = (drawal / demand_multiplier) if demand_multiplier > 0 else 0.0
            actual = (drawal + supply_volume) if drawal != 0 else 0.0

            acp = exchange_data["acp"]
            rtm_data_val = exchange_data["rtm_data"]
            dam_data_val = exchange_data["dam_data"]

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

    def _build_demand_document(
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
        now: datetime,
    ) -> Dict[str, Any]:
        return {
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
            # Preserve DSM fields — calculate_demand_metrics does not return them
            "net_dsm_amount": metrics.get(
                "net_dsm_amount", (existing_record or {}).get("net_dsm_amount", 0)
            ),
            "dsm_rate": metrics.get(
                "dsm_rate", (existing_record or {}).get("dsm_rate", 0)
            ),
            "updated_flag": UpdatedFlag.UPDATED.value,
            "updated_at": now,
            "timestamp": scheduled_at,
            "created_at": existing_record.get("created_at", now) if existing_record else now,
            "inserted_at": existing_record.get("inserted_at", now) if existing_record else now,
        }

    def _collect_demand_ops(
        self,
        serve_ops: List,
        hist_ops: List,
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
        now: datetime,
    ) -> None:
        """Append bulk-write operations for serve and historical collections (no I/O)."""
        filter_query = {"scheduled_at": scheduled_at, "time_block": time_block, "type": type_val}
        doc = self._build_demand_document(
            scheduled_at=scheduled_at, time_block=time_block, type_val=type_val,
            drawal=drawal, actual=actual, rostering=rostering, frequency=frequency,
            forecast=forecast, schedule=schedule, metrics=metrics,
            demand_multiplier=demand_multiplier, drawal_before_loss=drawal_before_loss,
            existing_record=existing_record, now=now,
        )
        serve_ops.append(ReplaceOne(filter_query, doc, upsert=True))

        hist_doc = {**doc, "archived_at": now, "archived_timestamp": now}
        # Delete then insert so historical time-series entries are replaced correctly.
        hist_ops.append(DeleteOne(filter_query))
        hist_ops.append(InsertOne(hist_doc))

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

        doc = self._build_demand_document(
            scheduled_at=scheduled_at, time_block=time_block, type_val=type_val,
            drawal=drawal, actual=actual, rostering=rostering, frequency=frequency,
            forecast=forecast, schedule=schedule, metrics=metrics,
            demand_multiplier=demand_multiplier, drawal_before_loss=drawal_before_loss,
            existing_record=existing_record, now=now,
        )

        # Use ReplaceOne(upsert) for serve — single atomic operation vs delete+insert
        await self.demand_repository.serve_collection.replace_one(filter_query, doc, upsert=True)

        hist_doc = {**doc, "archived_at": now, "archived_timestamp": now}
        await self.demand_repository.historical_collection.delete_many(filter_query)
        await self.demand_repository.historical_collection.insert_one(hist_doc)

    async def process_raw_data_to_serve(
        self,
        raw_data: List[Dict[str, Any]],
        metadata: Dict[str, Any],
        provider_config: Dict[str, Any],
    ) -> bool:
        return True
