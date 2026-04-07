"""
Weather data processor for handling weather data processing logic.
"""

import logging
from dateutil import parser
from datetime import datetime
from typing import Dict, List, Any, Optional

from .base_processor import BaseProcessor
from ..repository.weather_serve_repository import WeatherServeRepository
from libs.models.weather_serve_entity import WeatherServeEntity, WeatherServeHistoricalEntity
from libs.enum.updated_flag import UpdatedFlag
from libs.database.base_serve_repository import DataInsertionError
from libs.core.job_entities import JobDetail

logger = logging.getLogger(__name__)


class WeatherProcessor(BaseProcessor):
    """
    Weather data processor that handles weather data processing and storage.
    """
    
    def __init__(self):
        super().__init__()
        self.repository = WeatherServeRepository()
    
    async def execute_cron_job(self) -> Dict[str, Any]:
        """
        Execute weather data processing as a cron job with correlation ID
        """
        job_type = "weather_data_processing"
        job_name = "Weather Data Processing"
        schedule = "* /15 * * *"  # Default schedule
        start_time = datetime.now()
        
        # Generate and record job start to get execution_id (correlation ID)
        execution_id = self._record_job_start(job_type, job_name, schedule, start_time)
        if not execution_id:
            logger.error("Failed to record job start for %s", job_type)
            return {
                "status": "error",
                "message": "Failed to record job start",
                "execution_time": 0.0,
                "data_type": "weather"
            }
        
        # Set execution_id as correlation ID using base processor method
        self.set_correlation_id(execution_id)
        
        logger.info("=== EXECUTING WEATHER DATA PROCESSING JOB ===")
        
        try:
            # Create metadata for weather data processing with correlation ID
            metadata = {
                "data_type": "weather",
                "scraped_at": start_time,
                "source": "weather_processor_cron",
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
                "message": "Weather data processed successfully" if success else "Weather data processing failed",
                "execution_time": execution_time,
                "data_type": "weather",
                "correlation_id": execution_id,
                "cron_job":"weather",
                "trigger_details": trigger_res
            }
            
            self._record_job_completion(
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
                logger.info("Weather data processing completed successfully in %.2fs", execution_time)
            else:
                logger.error("Weather data processing failed after %.2fs", execution_time)
                
            return result
                
        except Exception as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            error_result = {
                "status": "error",
                "message": f"Weather data processing error: {str(e)}",
                "execution_time": execution_time,
                "data_type": "weather",
                "correlation_id": execution_id,
                "cron_job":"weather"
            }
            
            self._record_job_completion(
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
            
            logger.error("Error executing weather data processing job: %s", str(e))
            return error_result
        finally:
            # Clear correlation ID after job completion
            self.clear_correlation_id()
    
    # In WeatherProcessor class

# In your WeatherProcessor class

    def process_raw_data_to_serve(
        self, 
        raw_data: List[Dict[str, Any]], 
        metadata: Dict[str, Any],
        provider_config: Dict[str, Any]
    ) -> bool:
        """
        Process raw data for time-series. This uses a delete_many-then-insert
        pattern to prevent duplicates and handle data updates.
        """
        try:
            if not raw_data:
                logger.warning("No raw data provided for processing")
                return True

            # De-duplicate the incoming raw data batch first
            unique_raw_records = {}
            for record in raw_data:
                key = record.get("valid_time_local")
                if key:
                    unique_raw_records[key] = record
            
            clean_raw_data = list(unique_raw_records.values())
            logger.info(f"Processing {len(clean_raw_data)} unique weather records.")

            weather_entities_to_save = []
            historical_entities_to_save = []
            source = provider_config.get("metadata", {}).get("source", "IBM")
            city = provider_config.get("metadata", {}).get("city", "puducherry")
            today_date = datetime.now().date()

            for raw_record in clean_raw_data:
                try:
                    valid_time_local = raw_record.get("valid_time_local")
                    if not valid_time_local:
                        continue

                    # Find an existing record to check if data has changed
                    existing_record = self.repository.find_one({
                        "valid_time_local": valid_time_local, 
                        "source": source,
                        "city": city 
                    })

                    weather_entity = WeatherServeEntity.from_raw_data(
                        raw_data=raw_record,
                        metadata=metadata,
                        provider_config=provider_config
                    )

                    # Extract source revision number from raw data
                    source_revision = raw_record.get("revision_number") or raw_record.get("version") or raw_record.get("rev_no")

                    if existing_record:
                        # Increment system revision for updates (handles migration: 0 -> 1 for old records)
                        existing_system_revision = existing_record.get("system_revision_number", 0)
                        weather_entity.system_revision_number = existing_system_revision + 1
                        
                        if self._has_weather_data_changed(existing_record, raw_record):
                            weather_entity.updated_flag = UpdatedFlag.UPDATED
                        else:
                            weather_entity.updated_flag = UpdatedFlag.OLD
                    else:
                        # New record starts at 1
                        weather_entity.system_revision_number = 1
                    
                    # If not received in raw data, source revision = system revision
                    if source_revision is None:
                        if existing_record:
                            # Check if existing record has source revision, otherwise use new system revision
                            existing_source_revision = existing_record.get("source_revision_number")
                            if existing_source_revision is not None:
                                source_revision = existing_source_revision
                            else:
                                source_revision = weather_entity.system_revision_number
                        else:
                            source_revision = weather_entity.system_revision_number
                    
                    weather_entity.source_revision_number = source_revision
                    
                    # CRITICAL FIX: Delete ALL existing records for this timestamp and source
                    # This prevents any possibility of duplicates.
                    delete_count = self.repository.delete_by_time_and_source(valid_time_local, source,city)
                    if delete_count > 0:
                        logger.debug(f"Deleted {delete_count} old record(s) for {valid_time_local}")

                    # Now, queue the single new/updated entity for insertion
                    record_date = parser.parse(valid_time_local).date()
                    day_diff = (today_date - record_date).days

                    if day_diff >= 3:
                        historical_entities_to_save.append(WeatherServeHistoricalEntity(**weather_entity.model_dump(by_alias=True)))
                    else:
                        weather_entities_to_save.append(weather_entity)
                        if day_diff >= 1:
                            historical_entities_to_save.append(WeatherServeHistoricalEntity(**weather_entity.model_dump(by_alias=True)))

                except Exception as entity_error:
                    logger.error("Error processing record for %s: %s", valid_time_local, str(entity_error))
                    continue
            
            # --- Perform Bulk Inserts ---
            serve_success = True
            if weather_entities_to_save:
                try:
                    inserted_count = self.repository.bulk_insert(weather_entities_to_save)
                    logger.info("Successfully inserted/updated %d records in the serve collection.", inserted_count)
                except Exception as e:
                    logger.error("Error during bulk insert to serve collection: %s", str(e))
                    serve_success = False

            historical_success = True
            if historical_entities_to_save:
                try:
                    inserted_count = self.repository.bulk_insert_historical(historical_entities_to_save)
                    logger.info("Successfully inserted %d records in the historical collection.", inserted_count)
                except Exception as e:
                    logger.error("Error inserting historical weather entities: %s", str(e))
                    historical_success = False
            
            return serve_success and historical_success

        except Exception as e:
            logger.error("Critical error in process_raw_data_to_serve: %s", str(e), exc_info=True)
            return False


    async def execute_archival_job(self) -> Dict[str, Any]:
        """
        Execute weather data archival as a separate cron job with its own correlation ID
        Moves data older than 3 days to historical collection
        """
        job_type = "weather_serve_archival"
        job_name = "Weather Data Archival"
        schedule = "0 2 * * *"  # Daily at 2 AM
        start_time = datetime.now()
        
        # Generate and record job start to get execution_id (correlation ID)
        execution_id = self._record_job_start(job_type, job_name, schedule, start_time)
        if not execution_id:
            logger.error("Failed to record job start for %s", job_type)
            return {
                "status": "error",
                "message": "Failed to record job start",
                "execution_time": 0.0,
                "data_type": "weather"
            }
        
        # Set execution_id as correlation ID using base processor method
        self.set_correlation_id(execution_id)
        
        logger.info("=== EXECUTING WEATHER DATA ARCHIVAL JOB ===")
        
        try:
            # Archive old weather data (older than 4 days)
            archive_result = self.repository.archive_old_weather_data()
            
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            
            if archive_result.get("error"):
                error_result = {
                    "status": "error",
                    "message": f"Weather data archival error: {archive_result['error']}",
                    "execution_time": execution_time,
                    "data_type": "weather",
                    "correlation_id": execution_id
                }
                
                self._record_job_completion(
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
                
                logger.error("Weather data archival failed: %s", archive_result["error"])
                return error_result
            else:
                archived_count = archive_result.get("archived_count", 0)
                success_result = {
                    "status": "success",
                    "message": f"Archived {archived_count} weather records",
                    "execution_time": execution_time,
                    "archived_count": archived_count,
                    "cutoff_date": archive_result.get("cutoff_date"),
                    "data_type": "weather",
                    "correlation_id": execution_id
                }
                
                self._record_job_completion(
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
                
                logger.info("Weather data archival completed: %d records archived in %.2fs", 
                           archived_count, execution_time)
                return success_result
                
        except Exception as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            error_result = {
                "status": "error",
                "message": f"Weather data archival error: {str(e)}",
                "execution_time": execution_time,
                "data_type": "weather",
                "correlation_id": execution_id
            }
            
            self._record_job_completion(
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
            
            logger.error("Error executing weather data archival job: %s", str(e))
            return error_result
        finally:
            # Clear correlation ID after job completion
            self.clear_correlation_id()
    
    def _has_weather_data_changed(self, existing_record: Dict[str, Any], new_record: Dict[str, Any]) -> bool:
        """
        Check if weather data has changed by comparing key fields
        
        Args:
            existing_record: Existing weather record from database
            new_record: New weather record from raw data
            
        Returns:
            bool: True if data has changed
        """
        # List of fields to compare for changes
        compare_fields = [
            "cloud_cover", "temperature", "relative_humidity", "wind_speed",
            "wind_direction", "precip_chance", "pressure_mean_sea_level",
            "visibility", "uv_index", "temperature_feels_like"
        ]
        
        for field in compare_fields:
            existing_value = existing_record.get(field)
            new_value = new_record.get(field)
            
            # Handle None values and type conversions
            if existing_value != new_value:
                # Handle float comparison with tolerance
                if isinstance(existing_value, (int, float)) and isinstance(new_value, (int, float)):
                    if abs(float(existing_value) - float(new_value)) > 0.001:  # Small tolerance for float comparison
                        return True
                else:
                    return True
        
        return False


