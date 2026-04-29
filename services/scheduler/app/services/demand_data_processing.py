"""
Demand Data Processing Service
Handles the processing of demand raw data to demand serve entities
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import logging
from libs.models.demand_serve_entity import DemandServeEntity, DemandServeHistoricalEntity
from libs.enum.updated_flag import UpdatedFlag
from ..repository.demand_serve_repository import DemandServeRepository

logger = logging.getLogger(__name__)


class DemandDataProcessing:
    """
    Service for processing demand raw data into demand serve entities
    Handles 4-day rolling window and field mapping
    """
    
    def __init__(self):
        """Initialize the demand data processing service"""
        self.demand_repository = DemandServeRepository()

    
    async def process_demand_record(self, raw_data_record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a single demand raw data record
        
        Args:
            raw_data_record: Raw data record from CSV upload
            
        Returns:
            Dict[str, Any]: Processing result
        """
        try:
            logger.info("Processing demand record: %s", raw_data_record.get('_id'))
            
            # Get CSV data
            csv_data = raw_data_record.get("csv_data", [])
            if not csv_data:
                return {
                    "success": False,
                    "errors": ["No CSV data found in record"]
                }
            
            # Process each CSV row
            processed_entities = []
            errors = []
            
            for row in csv_data:
                try:
                    # Normalize field names to lowercase
                    normalized_row = self._normalize_field_names(row)
                    
                    # Map to demand serve entity
                    demand_entity = self._map_to_demand_serve_entity(normalized_row)
                    
                    if demand_entity:
                        processed_entities.append(demand_entity)
                    else:
                        errors.append("Failed to map row: %s" % row)
                        
                except (ValueError, TypeError, KeyError) as e:
                    errors.append("Error processing row %s: %s" % (row, str(e)))
                    logger.error("Error processing CSV row: %s", e)
            
            if not processed_entities:
                return {
                    "success": False,
                    "errors": errors if errors else ["No valid entities created"]
                }
            
            # Handle 4-day rolling window
            await self._handle_four_day_rolling_window(processed_entities)
            
            logger.info("Successfully processed %s demand entities", len(processed_entities))
            
            return {
                "success": True,
                "processed_count": len(processed_entities),
                "errors": errors
            }
            
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            logger.error("Error processing demand record: %s", e)
            return {
                "success": False,
                "errors": [f"Error processing demand record: {str(e)}"]
            }
    
    def _normalize_field_names(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize field names to lowercase for case-insensitive mapping
        
        Args:
            row: CSV row data
            
        Returns:
            Dict[str, Any]: Row with normalized field names
        """
        normalized = {}
        for key, value in row.items():
            if key:
                normalized[key.lower()] = value
        return normalized
    
    def _map_to_demand_serve_entity(self, row: Dict[str, Any]) -> Optional[DemandServeEntity]:
        """
        Map CSV row to DemandServeEntity
        
        Args:
            row: Normalized CSV row data
            
        Returns:
            Optional[DemandServeEntity]: Mapped entity or None if mapping fails
        """
        try:
            # Field mapping from CSV to DemandServeEntity
            entity_data = {}
            
            # Map time_block
            if "time_block" in row:
                entity_data["time_block"] = self._parse_int(row["time_block"])
            
            # Map schedule_date to scheduled_at
            if "schedule_date" in row:
                entity_data["scheduled_at"] = self._parse_datetime(row["schedule_date"])
            elif "timestamp" in row:
                entity_data["scheduled_at"] = self._parse_datetime(row["timestamp"])
            elif "scheduled_at" in row:
                entity_data["scheduled_at"] = self._parse_datetime(row["scheduled_at"])
            elif "date" in row:
                entity_data["scheduled_at"] = self._parse_datetime(row["date"])
            else:
                logger.warning("No timestamp field found in row")
                return None
            
            # Map type (IDF/DAF)
            if "type" in row:
                entity_data["type"] = str(row["type"]).upper()
            
            # Map zone
            if "zone" in row:
                entity_data["zone"] = str(row["zone"])
            
            # Map actual (Actual column)
            if "actual" in row:
                entity_data["actual"] = self._parse_float(row["actual"])
            
            # Map drawal (Drawal column)
            if "drawal" in row:
                entity_data["drawal"] = self._parse_float(row["drawal"])
            
            # Map error metrics
            if "mape" in row:
                entity_data["mape"] = self._parse_float(row["mape"])
            
            if "mpe" in row:
                entity_data["mpe"] = self._parse_float(row["mpe"])
            
            # Map raw frequency (Raw_Frequency column)
            if "raw_frequency" in row:
                entity_data["frequency"] = self._parse_float(row["raw_frequency"])
            
            # Map schedule (Schedule column)
            if "schedule" in row:
                entity_data["schedule"] = self._parse_float(row["schedule"])
            
            # Map computed actual demand (comp_act_demand column)
            if "comp_act_demand" in row:
                entity_data["comp_act_demand"] = self._parse_float(row["comp_act_demand"])
            
            # Map DSM rate (dsm_rate column)
            if "dsm_rate" in row:
                entity_data["dsm_rate"] = self._parse_float(row["dsm_rate"])
            
            # Map forecast (forecast column)
            if "forecast" in row:
                entity_data["forecast"] = self._parse_float(row["forecast"])
            
            # Map forecasted frequency (forecasted_frequency column)
            if "forecasted_frequency" in row:
                entity_data["forecasted_frequency"] = self._parse_float(row["forecasted_frequency"])
            
            # Map net DSM amount (net_dsm_amount column)
            if "net_dsm_amount" in row:
                entity_data["net_dsm_amount"] = self._parse_float(row["net_dsm_amount"])
            
            # Map remarks (remarks column)
            if "remarks" in row:
                entity_data["remarks"] = str(row["remarks"])
            
            # Map rostering (rostering column)
            if "rostering" in row:
                entity_data["rostering"] = self._parse_float(row["rostering"])
            
            # Map source fields
            if "source_actual" in row:
                entity_data["source_actual"] = str(row["source_actual"])
            
            if "source_forecast" in row:
                entity_data["source_forecast"] = str(row["source_forecast"])
            
            if "source_rostering" in row:
                entity_data["source_rostering"] = str(row["source_rostering"])
            
            # Map UDM (udm column)
            if "udm" in row:
                entity_data["udm"] = self._parse_float(row["udm"])
            
            # Map computed UDM (comp_udm column)
            if "comp_udm" in row:
                entity_data["comp_udm"] = self._parse_float(row["comp_udm"])
            
            # Create entity
            return DemandServeEntity(**entity_data)
            
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            logger.error("Error mapping row to DemandServeEntity: %s", e)
            return None
    
    def _parse_datetime(self, value: Any) -> Optional[datetime]:
        """
        Parse datetime value from various formats
        
        Args:
            value: Datetime value to parse
            
        Returns:
            Optional[datetime]: Parsed datetime or None
        """
        if not value:
            return None
        
        if isinstance(value, datetime):
            return value
        
        try:
            # Try ISO format first
            if isinstance(value, str):
                return datetime.fromisoformat(value.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            pass
        
        try:
            # Try parsing as string
            if isinstance(value, str):
                # Common formats
                for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d']:
                    try:
                        return datetime.strptime(value, fmt)
                    except (ValueError, TypeError):
                        continue
        except (ValueError, TypeError):
            pass
        
        logger.warning("Could not parse datetime: %s", value)
        return None
    
    def _parse_float(self, value: Any) -> float:
        """
        Parse float value
        
        Args:
            value: Value to parse
            
        Returns:
            float: Parsed float or 0.0
        """
        if value is None or value == '':
            return 0.0
        
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0
    
    def _parse_int(self, value: Any) -> Optional[int]:
        """
        Parse int value
        
        Args:
            value: Value to parse
            
        Returns:
            Optional[int]: Parsed int or None
        """
        if value is None or value == '':
            return None
        
        try:
            return int(value)
        except (ValueError, TypeError):
            return None
    
    async def _handle_four_day_rolling_window(self, entities: List[DemandServeEntity]):
        """
        Handle 4-day rolling window logic
        
        Args:
            entities: List of demand serve entities to process
        """
        try:
            today = datetime.now().date()
            
            # Define 4-day window: day_before_yesterday, yesterday, today, tomorrow
            day_before_yesterday = today - timedelta(days=2)
            yesterday = today - timedelta(days=1)
            tomorrow = today + timedelta(days=1)
            
            # Get existing demand serve data
            existing_data = await self.demand_repository.find_many({}, limit=10000)
            
            # Process each entity
            for entity in entities:
                entity_date = entity.scheduled_at.date()
                
                # Check if entity date is within 4-day window
                if entity_date < day_before_yesterday:
                    # Data is too old, skip
                    logger.info("Skipping data for %s - outside 4-day window", entity_date)
                    continue
                elif entity_date > tomorrow:
                    # Data is too far in future, skip
                    logger.info("Skipping data for %s - outside 4-day window", entity_date)
                    continue
                
                # Check if we need to move old data to historical
                if entity_date == today:
                    # Move data older than 4-day window to historical
                    self._move_old_data_to_historical(day_before_yesterday)
                
                # Store/update the entity
                self._store_demand_entity(entity)
            
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            logger.error("Error handling 4-day rolling window: %s", e)
            raise
    
    def _move_old_data_to_historical(self, cutoff_date):
        """
        Move data older than cutoff date to historical collection
        
        Args:
            cutoff_date: Date cutoff for moving to historical
        """
        try:
            # Find data older than cutoff date
            old_data = self.demand_repository.find_many(
                {"scheduled_at": {"$lt": datetime.combine(cutoff_date, datetime.min.time())}},
                limit=10000
            )
            
            for data in old_data:
                # Create historical entity
                historical_entity = DemandServeHistoricalEntity(**data)
                
                # Insert into historical collection
                self.demand_repository.insert_historical(historical_entity)
                
                # Delete from active collection
                self.demand_repository.delete(str(data["_id"]))
            
            if old_data:
                logger.info("Moved %s records to historical collection", len(old_data))
                
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            logger.error("Error moving old data to historical: %s", e)
            raise
    
    async def _store_demand_entity(self, entity: DemandServeEntity):
        """
        Store demand entity using delete-insert pattern with updated flag logic
        
        Args:
            entity: DemandServeEntity to store
        """
        try:
            # First, check if entity already exists to determine updated flag
            existing = await self.demand_repository.find_one({
                "time_block": entity.time_block,
                "scheduled_at": entity.scheduled_at,
                "type": entity.type
            })

            should_insert = True
            
            # Apply business logic for updated flag
            if existing:
                # Check if there are changes in the data
                has_changes = self._has_data_changes(existing, entity)
                
                if has_changes:
                    # Data has changed, set as UPDATED
                    entity.updated_flag = UpdatedFlag.UPDATED
                    logger.debug("Data changed for %s - setting UPDATED flag", entity.scheduled_at)
                elif not has_changes and existing["updated_flag"] == UpdatedFlag.UPDATED:
                    # No changes, set as OLD
                    entity.updated_flag = UpdatedFlag.OLD
                    logger.debug("No data changes for %s - setting OLD flag", entity.scheduled_at)
                else:
                    should_insert = False
            else:
                # First time entry, set as UPDATED
                entity.updated_flag = UpdatedFlag.UPDATED
                logger.debug("First entry for %s - setting UPDATED flag", entity.scheduled_at)
            
            # Use delete-insert method
            if should_insert:
                result = await self.demand_repository.delete_insert_demand_serve(
                    filter_criteria={
                        "time_block": entity.time_block,
                        "scheduled_at": entity.scheduled_at,
                        "type": entity.type
                    },
                    entities=[entity]
                )
            
            logger.debug("Delete-insert operation for %s: deleted %s, inserted %s", 
                        entity.scheduled_at, result['deleted_count'], result['inserted_count'])
                
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            logger.error("Error storing demand entity: %s", e)
            raise

    def _has_data_changes(self, existing: Dict[str, Any], new_entity: DemandServeEntity) -> bool:
        """
        Check if there are changes between existing and new entity data
        
        Args:
            existing: Existing entity data from database
            new_entity: New entity to compare
            
        Returns:
            bool: True if there are changes, False otherwise
        """
        try:
            # Compare key fields that indicate data changes
            comparison_fields = [
                "actual", "drawal", "mape", "mpe", "frequency", "schedule",
                "comp_act_demand", "dsm_rate", "forecast", "forecasted_frequency",
                "net_dsm_amount", "rostering", "udm", "comp_udm", "zone"
            ]
            
            for field in comparison_fields:
                existing_value = existing.get(field)
                new_value = getattr(new_entity, field, None)
                
                # Handle float comparison with tolerance
                if isinstance(existing_value, (int, float)) and isinstance(new_value, (int, float)):
                    if abs(float(existing_value) - float(new_value)) > 0.001:  # Small tolerance for float comparison
                        return True
                elif existing_value != new_value:
                    return True
            
            return False
            
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            logger.error("Error comparing data changes: %s", e)
            # If comparison fails, assume there are changes to be safe
            return True
