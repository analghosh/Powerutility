"""
Supply Data Processing Service
Handles the processing of supply raw data to supply serve entities
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import logging
from libs.models.supply_serve_entity import SupplyServeEntity, SupplyServeHistoricalEntity
from libs.database.base_serve_repository import BaseServeRepository
from libs.enum.updated_flag import UpdatedFlag
from services.scheduler.app.core.config import settings

from ..repository.supply_serve_repository import SupplyServeRepository

logger = logging.getLogger(__name__)


class SupplyDataProcessing:
    """
    Service for processing supply raw data into supply serve entities
    Handles 4-day rolling window and field mapping
    """
    
    def __init__(self):
        """Initialize the supply data processing service"""
        self.supply_repository = SupplyServeRepository()

    async def process_supply_data(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Process supply data from raw data collection
        
        Args:
            payload: Optional payload with processing parameters
            
        Returns:
            Dict[str, Any]: Processing result
        """
        try:
            logger.info("Starting supply data processing")
            
            # Get raw supply data from raw data collection
            from ..core.config import settings
            from libs.enum.raw_data_status import RawDataStatus
            
            from ..core.database import get_database
            db = get_database()
            raw_data_collection = db["supply_raw_data"]
            
            # Get pending supply raw data
            cursor = raw_data_collection.find({
                "status": RawDataStatus.PENDING.value
            }).limit(100)
            try:
                raw_data_records = await cursor.to_list(length=10000)
            finally:
                cursor.close()
            
            if not raw_data_records:
                logger.info("No pending supply raw data found")
                return {
                    "status": "success",
                    "message": "No pending supply data to process",
                    "processed_records": 0
                }
            
            processed_count = 0
            error_count = 0
            
            for raw_record in raw_data_records:
                try:
                    result = self.process_supply_record(raw_record)
                    if result.get("success", False):
                        processed_count += result.get("processed_count", 0)
                    else:
                        error_count += 1
                        logger.error(f"Failed to process record {raw_record.get('_id')}: {result.get('errors', [])}")
                except Exception as e:
                    error_count += 1
                    logger.error(f"Error processing record {raw_record.get('_id')}: {str(e)}")
            
            logger.info(f"Supply data processing completed: {processed_count} processed, {error_count} errors")
            
            return {
                "status": "success",
                "message": f"Processed {processed_count} supply records",
                "processed_records": processed_count,
                "error_count": error_count
            }
            
        except Exception as e:
            logger.error(f"Error in supply data processing: {str(e)}")
            return {
                "status": "error",
                "message": f"Error processing supply data: {str(e)}",
                "processed_records": 0
            }
    
    def process_supply_record(self, raw_data_record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a single supply raw data record
        
        Args:
            raw_data_record: Raw data record from CSV upload
            
        Returns:
            Dict[str, Any]: Processing result
        """
        try:
            logger.info(f"Processing supply record: {raw_data_record.get('_id')}")
            
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
                    
                    # Map to supply serve entity
                    supply_entity = self._map_to_supply_serve_entity(normalized_row)
                    
                    if supply_entity:
                        processed_entities.append(supply_entity)
                    else:
                        errors.append(f"Failed to map row: {row}")
                        
                except Exception as e:
                    errors.append(f"Error processing row {row}: {str(e)}")
                    logger.error(f"Error processing CSV row: {e}")
            
            if not processed_entities:
                return {
                    "success": False,
                    "errors": errors if errors else ["No valid entities created"]
                }
            
            # Handle 4-day rolling window
            self._handle_four_day_rolling_window(processed_entities)
            
            logger.info(f"Successfully processed {len(processed_entities)} supply entities")
            
            return {
                "success": True,
                "processed_count": len(processed_entities),
                "errors": errors
            }
            
        except Exception as e:
            logger.error(f"Error processing supply record: {e}")
            return {
                "success": False,
                "errors": [f"Error processing supply record: {str(e)}"]
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
    
    def _map_to_supply_serve_entity(self, row: Dict[str, Any]) -> Optional[SupplyServeEntity]:
        """
        Map CSV row to SupplyServeEntity
        
        Args:
            row: Normalized CSV row data
            
        Returns:
            Optional[SupplyServeEntity]: Mapped entity or None if mapping fails
        """
        try:
            # Field mapping from CSV to SupplyServeEntity
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
            
            # Map plant_id and plant_name
            if "plant_id" in row:
                entity_data["plant_id"] = str(row["plant_id"])
            
            if "plant_name" in row:
                entity_data["plant_name"] = str(row["plant_name"])
            
            # Map c_s (Display category)
            if "c_s" in row:
                entity_data["c_s"] = str(row["c_s"])
            
            # Map category
            if "category" in row:
                entity_data["category"] = str(row["category"])
            
            # Map technology
            if "technology" in row:
                entity_data["technology"] = str(row["technology"])
            
            # Map price_group
            if "price_group" in row:
                entity_data["price_group"] = str(row["price_group"])
            
            # Map dc_sg
            if "dc_sg" in row:
                entity_data["dc_sg"] = str(row["dc_sg"])
            
            # Map ramping
            if "ramping" in row:
                entity_data["ramping"] = str(row["ramping"])
            
            # Map TM
            if "tm" in row:
                entity_data["tm"] = self._parse_float(row["tm"])
            
            # Map FC_price
            if "fc_price" in row:
                entity_data["fc_price"] = self._parse_float(row["fc_price"])
            
            # Map VC_price
            if "vc_price" in row:
                entity_data["vc_price"] = self._parse_float(row["vc_price"])
            
            # Map px_price
            if "px_price" in row:
                entity_data["px_price"] = self._parse_float(row["px_price"])
            
            # Map multiplier
            if "multiplier" in row:
                entity_data["multiplier"] = str(row["multiplier"])
            
            # Map volume
            if "volume" in row:
                entity_data["volume"] = self._parse_float(row["volume"])
            
            # Create entity
            return SupplyServeEntity(**entity_data)
            
        except Exception as e:
            logger.error(f"Error mapping row to SupplyServeEntity: {e}")
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
        except:
            pass
        
        try:
            # Try parsing as string
            if isinstance(value, str):
                # Common formats
                for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d']:
                    try:
                        return datetime.strptime(value, fmt)
                    except:
                        continue
        except:
            pass
        
        logger.warning(f"Could not parse datetime: {value}")
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
    
    def _handle_four_day_rolling_window(self, entities: List[SupplyServeEntity]):
        """
        Handle 4-day rolling window logic using delete-insert pattern
        
        Args:
            entities: List of supply serve entities to process
        """
        try:
            today = datetime.now().date()
            
            # Define 4-day window: day_before_yesterday, yesterday, today, tomorrow
            day_before_yesterday = today - timedelta(days=2)
            yesterday = today - timedelta(days=1)
            tomorrow = today + timedelta(days=1)
            
            # Filter entities within 4-day window
            valid_entities = []
            for entity in entities:
                entity_date = entity.scheduled_at.date()
                
                # Check if entity date is within 4-day window
                if entity_date < day_before_yesterday:
                    # Data is too old, skip
                    logger.info(f"Skipping data for {entity_date} - outside 4-day window")
                    continue
                elif entity_date > tomorrow:
                    # Data is too far in future, skip
                    logger.info(f"Skipping data for {entity_date} - outside 4-day window")
                    continue
                
                valid_entities.append(entity)
            
            if not valid_entities:
                logger.info("No valid entities within 4-day window")
                return
            
            # Check if we need to move old data to historical
            if today in [entity.scheduled_at.date() for entity in valid_entities]:
                # Move data older than 4-day window to historical
                self._move_old_data_to_historical(day_before_yesterday)
            
            # Use batch delete-insert for better performance
            self._store_supply_entities_batch(valid_entities)
            
        except Exception as e:
            logger.error(f"Error handling 4-day rolling window: {e}")
            raise
    
    def _move_old_data_to_historical(self, cutoff_date):
        """
        Move data older than cutoff date to historical collection
        
        Args:
            cutoff_date: Date cutoff for moving to historical
        """
        try:
            # Find data older than cutoff date
            old_data = self.supply_repository.find_many(
                {"scheduled_at": {"$lt": datetime.combine(cutoff_date, datetime.min.time())}},
                limit=10000
            )
            
            for data in old_data:
                # Create historical entity
                historical_entity = SupplyServeHistoricalEntity(**data)
                
                # Insert into historical collection
                self.supply_repository.insert_historical(historical_entity)
                
                # Delete from active collection
                self.supply_repository.delete(str(data["_id"]))
            
            if old_data:
                logger.info(f"Moved {len(old_data)} records to historical collection")
                
        except Exception as e:
            logger.error(f"Error moving old data to historical: {e}")
            raise
    
    def _store_supply_entities_batch(self, entities: List[SupplyServeEntity]):
        """
        Store multiple supply entities using delete-insert pattern grouped by date
        
        Args:
            entities: List of SupplyServeEntity to store
        """
        try:
            if not entities:
                return
            
            # Group entities by scheduled_at date
            entities_by_date = {}
            for entity in entities:
                date_key = entity.scheduled_at.date()
                if date_key not in entities_by_date:
                    entities_by_date[date_key] = []
                entities_by_date[date_key].append(entity)
            
            # Process each date group
            for date_key, date_entities in entities_by_date.items():
                # Convert date back to datetime for the query
                start_datetime = datetime.combine(date_key, datetime.min.time())
                end_datetime = datetime.combine(date_key, datetime.max.time())
                
                # Get existing records for this date to apply business logic
                existing_records = self.supply_repository.find_supply_serve_by_date_range(
                    start_datetime, end_datetime
                )
                
                # Apply updated flag business logic to each entity
                processed_entities = []
                for entity in date_entities:
                    # Find matching existing record
                    existing = next(
                        (record for record in existing_records 
                         if (record.get("time_block") == entity.time_block and
                             record.get("scheduled_at") == entity.scheduled_at and
                             record.get("plant_id") == getattr(entity, 'plant_id', None))),
                        None
                    )
                    
                    # Apply business logic for updated flag
                    if existing:
                        has_changes = self._has_data_changes(existing, entity)
                        if has_changes:
                            entity.updated_flag = UpdatedFlag.UPDATED
                        else:
                            entity.updated_flag = UpdatedFlag.OLD
                    else:
                        entity.updated_flag = UpdatedFlag.UPDATED
                    
                    processed_entities.append(entity)
                
                # Use delete-insert method
                result = self.supply_repository.delete_insert_supply_serve(
                    filter_criteria={
                        "scheduled_at": {
                            "$gte": start_datetime,
                            "$lt": end_datetime
                        }
                    },
                    entities=processed_entities
                )
                
                logger.info(f"Batch delete-insert for {date_key}: "
                          f"deleted {result['deleted_count']}, inserted {result['inserted_count']}")
                
        except Exception as e:
            logger.error(f"Error storing supply entities batch: {e}")
            raise

    def _has_data_changes(self, existing: Dict[str, Any], new_entity: SupplyServeEntity) -> bool:
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
                "plant_name", "dc_sg", "volume", "fc_price", "px_price", "vc_price",
                "category", "technology", "price_group", "c_s", "tm", "multiplier", "ramping"
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
            
        except Exception as e:
            logger.error(f"Error comparing data changes: {e}")
            # If comparison fails, assume there are changes to be safe
            return True
