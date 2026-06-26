"""
Demand Manual Services
Handles logic for processing manual demand data entry.
"""

from typing import List, Dict, Any, Optional
import csv
import io
import logging
from datetime import datetime, timedelta
from pathlib import Path

from ...core.config import settings
from ..repository.demand_serve_repository import DemandServeRepository
from ..repository.supply_serve_repository import SupplyServeRepository
from ..repository.exchange_repository import ExchangeRepository
from libs.helpers.demand_calculations import calculate_demand_metrics
from libs.models.demand_serve_entity import DemandServeEntity, DemandServeHistoricalEntity
from libs.enum.updated_flag import UpdatedFlag
from libs.utils.csv_date_parse import parse_csv_scheduled_at, parse_ingestion_date

logger = logging.getLogger(__name__)

class DemandManualServices:
    """
    Service for handling manual demand data operations.
    """
    
    def __init__(self):
        self.demand_repository = DemandServeRepository()
        self.supply_repository = SupplyServeRepository()
        self.exchange_repository = ExchangeRepository()
        
        from ..repository.master_plants_repository import MasterPlantsRepository
        self.master_plants_repository = MasterPlantsRepository()

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
    
    async def process_manual_demand_data(self, manual_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Process manual demand data: calculate fields and update database.
        
        Args:
            manual_data: List of dictionaries containing:
                - scheduled_at (str or datetime)
                - time_block (int)
                - drawal (float)
                - rostering (float)
                - frequency (float)
                - type (str)
        
        Returns:
            Dict containing success status and processed count.
        """
        try:
            processed_count = 0
            errors = []
            
            # Fetch all plants and calculate demand multiplier
            # We do this once per batch for efficiency
            all_plants = await self.master_plants_repository.get_all_plants()
            demand_multiplier = self._calculate_demand_multiplier(all_plants)
            logger.info(f"Calculated Demand Multiplier for Manual Update: {demand_multiplier}")

            for item in manual_data:
                try:
                    # Validate and parse input
                    scheduled_at_raw = item.get("scheduled_at")
                    if isinstance(scheduled_at_raw, str):
                        scheduled_at = datetime.fromisoformat(scheduled_at_raw.replace('Z', '+00:00'))
                    else:
                        scheduled_at = scheduled_at_raw

                    time_block = item.get("time_block")
                    drawal = float(item.get("drawal") or 0)
                    rostering = float(item.get("rostering") or 0)
                    frequency = float(item.get("frequency") or 0)
                    type_val = item.get("type") or "IDF"

                    if not scheduled_at or not time_block:
                        logger.warning(f"Skipping item due to missing date or time block: {item}")
                        continue

                    # Calculate drawal_before_loss
                    drawal_before_loss = 0.0
                    if demand_multiplier > 0:
                        drawal_before_loss = drawal / demand_multiplier

                    # 1. Calculate actual
                    # actual = drawal + supply_sg (plant_id=PPCLGPP, dc_sg=SG)
                    # if drawal == 0: actual = 0 (no MAPE/MPE will be calculated)
                    if drawal == 0:
                        actual = 0.0
                    else:
                        supply_volume = await self._get_supply_volume(scheduled_at, time_block)
                        actual = drawal + supply_volume

                    # 2. Fetch Exchange Data for ACP (needed for DSM/Net DSM)
                    exchange_data = await self._get_exchange_data(scheduled_at, time_block)
                    acp = exchange_data.get("acp", 100.0)
                    rtm_data_val = exchange_data.get("rtm_data", 0.0)
                    dam_data_val = exchange_data.get("dam_data", 0.0)

                    # 3. Fetch existing record for this type to preserve forecast/schedule
                    existing_record = await self.demand_repository.find_one_async(
                         self.demand_repository.collection_name,
                         {"scheduled_at": scheduled_at, "time_block": time_block, "type": type_val}
                    )

                    forecast = float(existing_record.get("forecast") or 0) if existing_record else 0.0
                    schedule = float(existing_record.get("schedule") or 0) if existing_record else 0.0

                    # 4. Calculate metrics using this type's own forecast
                    # MAPE/MPE are skipped (set to 0) when actual == 0
                    metrics = calculate_demand_metrics(
                        actual=actual,
                        forecast=forecast,
                        frequency=frequency,
                        time_block=time_block,
                        rostering=rostering,
                        rtm_data=rtm_data_val,
                        dam_data=dam_data_val,
                        acp=acp,
                        drawal=drawal,
                        schedule=schedule
                    )

                    # 5. Save the primary record (IDF or DAF as given)
                    
                    await self._save_demand_record(
                        scheduled_at=scheduled_at,
                        time_block=time_block,
                        type_val=type_val,
                        drawal=drawal,
                        actual=actual,
                        rostering=rostering,
                        frequency=frequency,
                        forecast=forecast,
                        schedule=schedule,
                        metrics=metrics,
                        demand_multiplier=demand_multiplier,
                        drawal_before_loss=drawal_before_loss,
                        existing_record=existing_record,
                    )

                    # 6. Sync to DAF when processing IDF
                    # DAF shares the same actual/drawal/rostering but uses its own forecast
                    # so its MAPE/MPE will differ from IDF's
                    if type_val == "IDF":
                        existing_daf = await self.demand_repository.find_one_async(
                            self.demand_repository.collection_name,
                            {"scheduled_at": scheduled_at, "time_block": time_block, "type": "DAF"}
                        )
                        if existing_daf:
                            daf_forecast = float(existing_daf.get("forecast") or 0)
                            daf_schedule = float(existing_daf.get("schedule") or 0)

                            daf_metrics = calculate_demand_metrics(
                                actual=actual,
                                forecast=daf_forecast,
                                frequency=frequency,
                                time_block=time_block,
                                rostering=rostering,
                                rtm_data=rtm_data_val,
                                dam_data=dam_data_val,
                                acp=acp,
                                drawal=drawal,
                                schedule=daf_schedule
                            )

                            await self._save_demand_record(
                                scheduled_at=scheduled_at,
                                time_block=time_block,
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
                                existing_record=existing_daf,
                            )

                    processed_count += 1

                except Exception as item_error:
                    logger.error(f"Error processing item block {item.get('time_block')}: {item_error}")
                    errors.append(f"Block {item.get('time_block')}: {str(item_error)}")
            
            return {
                "success": True,
                "processed_count": processed_count,
                "errors": errors
            }
            
        except Exception as e:
            logger.error(f"Error in process_manual_demand_data: {e}")
            return {
                "success": False,
                "error": str(e)
            }

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
        """Delete-insert a demand record and its historical copy."""
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
        
        collection_name =  self.demand_repository.collection_name
        historical_collection_name ='demand_serve_historical'
            
        await self.demand_repository.delete_many_async(collection_name, filter_query)
        await self.demand_repository.insert_one_async(collection_name, full_document)

        # Historical copy
        now_utc = datetime.now()
        full_document_hist = {**full_document, "archived_at": now_utc, "archived_timestamp": now_utc}
        await self.demand_repository.delete_many_async(historical_collection_name, filter_query)
        await self.demand_repository.insert_one_async(historical_collection_name, full_document_hist)

    async def _get_supply_volume(self, scheduled_at: datetime, time_block: int) -> float:
        """Fetch supply volume for scheduled_at and time_block."""
        try:
             # SupplyServeRepository might not have find_one exposed well in processing service version
             # It inherits GenericRepository.
             # Need to know filter.
             # Typically 'scheduled_at' and 'time_block'.
             # And Plant? No, we need TOTAL supply volume?
             # demand_processor uses:
             # keys_for_supply.append({
             #        "scheduled_at": scheduled_at,
             #        "time_block": time_block,
             #        "dc_sg": "SG",
             #        "plant_id": "PPCLGPP"
             #    })
             # Wait, demand_processor HARDCODES plant_id="PPCLGPP" and dc_sg="SG".
             # Is this global supply or specific plant supply?
             # 'supply_volume' for 'actual' calculation usually implies total supply or a specific source.
             # In demand_processor.py line 628: 
             # key for supply lookup (Plant=PPCLGPP, DC_SG=SG hardcoded in original)
             # So I should replicate this hardcoding.
             
             filter_query = {
                 "scheduled_at": scheduled_at,
                 "time_block": time_block,
                 "dc_sg": "SG",
                 "plant_id": "PPCLGPP"
             }
             
             record = await self.supply_repository.find_one_async(
                 self.supply_repository.collection_name, 
                 filter_query
             )
             
             if record:
                 return float(record.get("volume") or 0)
             return 0.0
             
        except Exception as e:
            logger.error(f"Error fetching supply volume: {e}")
            return 0.0

    async def _get_exchange_data(self, scheduled_at: datetime, time_block: int) -> Dict[str, float]:
        """Fetch Exchange data (ACP, RTM, DAM)."""
        try:
            # Logic similar to demand_processor._fetch_exchange_data_for_dsm_for_one_time_block
            # Simplified for service efficiency.
            
            # Fetch RTM
            rtm_query = {
                "scheduled_at": scheduled_at,
                "time_block": time_block,
                "market_type": "RTM"
            }
            rtm_record = await self.exchange_repository.find_one_async(
                self.exchange_repository.collection_name,
                rtm_query
            )
            
            # Fetch DAM
            dam_query = {
                "scheduled_at": scheduled_at,
                "time_block": time_block,
                "market_type": "DAM"
            }
            dam_record = await self.exchange_repository.find_one_async(
                self.exchange_repository.collection_name,
                dam_query
            )
            
            rtm_w1 = 0.0
            dam_w1 = 0.0
            acp = 100.0 # Default
            
            if rtm_record:
                rtm_w1 = float(rtm_record.get("acp", 0) or rtm_record.get("mcp", 0) or 0)
            
            if dam_record:
                dam_w1 = float(dam_record.get("acp", 0) or dam_record.get("mcp", 0) or 0)
                
            # ACP Logic
            if rtm_record and (rtm_record.get("acp") or 0) > 0:
                acp = float(rtm_record.get("acp") or 0)
            elif dam_record and (dam_record.get("acp") or 0) > 0:
                acp = float(dam_record.get("acp") or 0)
            elif rtm_record and (rtm_record.get("mcp") or 0) > 0:
                acp = float(rtm_record.get("mcp") or 0)
            elif dam_record and (dam_record.get("mcp") or 0) > 0:
                acp = float(dam_record.get("mcp") or 0)
                
            return {
                "acp": acp,
                "rtm_data": rtm_w1,
                "dam_data": dam_w1
            }
            
        except Exception as e:
            logger.error(f"Error fetching exchange data: {e}")
            return {"acp": 100.0, "rtm_data": 0.0, "dam_data": 0.0}
   
    async def process_manual_temp_demand_data(self, manual_data: List[Dict[str, Any]], call_from='') -> Dict[str, Any]:
        """
        Process manual demand data: calculate fields and update database.
        Args:
            temp_manual_data: List of dictionaries containing:
                - time_block (int)
                - drawal (float)
                - rostering (float)
                - frequency (float)
        
        Returns:
            Dict containing success status and processed count.
        """
        try:
            processed_count = 0
            errors = []
            for item in manual_data:
                try:
                    # Validate and parse input
                    scheduled_at_raw = item.get("scheduled_at")
                    time_block = item.get("time_block")
                    if not scheduled_at_raw:
                        raise ValueError("scheduled_at is required")
                    scheduled_at = parse_csv_scheduled_at(scheduled_at_raw)
                    if not scheduled_at or not time_block:
                            logger.warning(f"Skipping item due to missing date or time block: {item}")
                            continue
                    
                    drawal = float(item.get("drawal") or 0)
                    rostering = float(item.get("rostering") or 0)
                    frequency = float(item.get("frequency") or 0)
                    if time_block is None:
                        raise ValueError("time_block is required")
                    existing_record = await self.demand_repository.find_one_async(
                            self.demand_repository.temp_demand_collection_name,
                            {"time_block": time_block}
                        )
                    await self._save_temp_demand_record(
                        scheduled_at=scheduled_at,
                        time_block=time_block,
                        drawal=drawal,
                        rostering=rostering,
                        frequency=frequency,
                        existing_record=existing_record
                        )
                    processed_count += 1

                except Exception as item_error:
                    logger.error(f"Error processing item block {item.get('time_block')}: {item_error}")
                    errors.append(f"Block {item.get('time_block')}: {str(item_error)}")
            
            return {
                "success": True,
                "processed_count": processed_count,
                "errors": errors
            }
            
        except Exception as e:
            logger.error(f"Error in process_manual_demand_data: {e}")
            return {
                "success": False,
                "error": str(e)
            }
        
    async def _save_temp_demand_record(
        self,
        scheduled_at: datetime,
        time_block: int,
        drawal: float,
        rostering: float,
        frequency: float,
        existing_record: Optional[Dict[str, Any]],
    ) -> None:
        """Delete-insert a temp demand record."""
        filter_query = {"time_block": time_block}
        now = datetime.now()

        full_document = {
            "scheduled_at": scheduled_at,
            "time_block": time_block,
            "drawal": drawal,
            "rostering": rostering,
            "frequency": frequency,
            "updated_flag": UpdatedFlag.UPDATED.value,
            "updated_at": now,
            "created_at": existing_record.get("created_at", now) if existing_record else now,
            "inserted_at": existing_record.get("inserted_at", now) if existing_record else now,
        }
        collection_name = self.demand_repository.temp_demand_collection_name            
            
        await self.demand_repository.delete_many_async(collection_name, filter_query)
        await self.demand_repository.insert_one_async(collection_name, full_document)

    def _format_scheduled_at_for_csv_export(self, scheduled_at: Any) -> str:
        """Format scheduled_at as DD-MM-YYYY for CSV export."""
        if not scheduled_at:
            return datetime.now().strftime("%d-%m-%Y")
        if isinstance(scheduled_at, datetime):
            return scheduled_at.strftime("%d-%m-%Y")
        return parse_ingestion_date(scheduled_at).strftime("%d-%m-%Y")

    async def export_temp_demand_csv(self) -> str:
        """
        Export today's records from temp_demand_serve as a CSV string.
        Columns: scheduled_at, time_block, drawal, rostering, frequency
        """
        records = await self.demand_repository.get_all_async(
            self.demand_repository.temp_demand_collection_name,
            sort=[("time_block", 1)],
        )

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["scheduled_at", "time_block", "drawal", "rostering", "frequency"])
        for rec in records:
            scheduled_at = self._format_scheduled_at_for_csv_export(rec.get("scheduled_at"))
            writer.writerow([
                scheduled_at,
                rec.get("time_block", ""),
                rec.get("drawal", 0),
                rec.get("rostering", 0),
                rec.get("frequency", 0),
            ])
        return output.getvalue()

    REQUIRED_CSV_COLUMNS = {"scheduled_at", "time_block", "drawal", "rostering", "frequency"}

    async def process_temp_demand_csv(self, file_contents: bytes, original_filename: str) -> Dict[str, Any]:
        """
        Persist the uploaded CSV to disk, parse it, and bulk-upsert temp demand records.

        Args:
            file_contents: Raw bytes of the uploaded CSV file.
            original_filename: Original filename (used only for the saved copy's name).

        Returns:
            Dict with success, processed_count, saved_path, and errors.
        """
        # 1. Save raw file to persistent directory
        upload_dir = Path(settings.DEMAND_MANUAL_UPLOAD_DIR)
        upload_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        saved_path = upload_dir / f"demand_manual_{timestamp}.csv"
        saved_path.write_bytes(file_contents)
        logger.info("Demand manual CSV saved to %s", saved_path)

        # 2. Parse CSV
        reader = csv.DictReader(io.StringIO(file_contents.decode("utf-8")))
        missing = self.REQUIRED_CSV_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV is missing required columns: {missing}")

        data_list = []
        for row in reader:
            data_list.append({
                "scheduled_at": row["scheduled_at"],
                "time_block": int(row["time_block"]),
                "drawal": float(row.get("drawal") or 0),
                "rostering": float(row.get("rostering") or 0),
                "frequency": float(row.get("frequency") or 0),
            })

        # 3. Delegate to existing bulk-upsert logic
        result = await self.process_manual_temp_demand_data(data_list)
        result["saved_path"] = str(saved_path)
        return result


demand_manual_service = DemandManualServices()
