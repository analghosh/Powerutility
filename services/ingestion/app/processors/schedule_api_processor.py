"""
Schedule API Processor
Handles fetching schedule data from external API and storing in supply_serve collection
"""

import logging
import asyncio
import aiohttp
import json
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from libs.database.mongo_client_factory import get_mongo_client
from libs.models.integration_apilog_entity import IntegrationApiLogEntity
from services.ingestion.app.core.config import settings
from services.ingestion.app.core.logger import get_ingestion_logger
from services.ingestion.app.processors.base_raw_data_processor import BaseRawDataProcessor
from services.ingestion.app.repositories.integration_apilog_repository import IntegrationApiLogRepository

logger = get_ingestion_logger("schedule_api_processor")


class ScheduleAPIProcessor(BaseRawDataProcessor):
    """
    Processor for fetching schedule data from external API and storing:
    - Plant schedules (sg_volume_after_loss_mw) in supply_serve collection as SG records
    """
    
    def __init__(self, audit_logger=None):
        """Initialize schedule API processor"""
        super().__init__()
        self.audit_logger = audit_logger
        self.api_timeout = getattr(settings, 'API_TIMEOUT', 30)
        self.retry_attempts = getattr(settings, 'API_RETRY_ATTEMPTS', 3)
        self.retry_delay = getattr(settings, 'API_RETRY_DELAY', 5)
        
        # MongoDB connection settings
        self.mongo_uri = settings.MONGODB_URI
        self.database_name = settings.MONGODB_DATABASE
        
        # Integration API log repository
        self.integration_apilog_repository = IntegrationApiLogRepository(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE
        )
        
    async def execute_cron_job(self) -> Dict[str, Any]:
        """
        Execute schedule API processing as a cron job
        """
        job_type = "schedule_api_processor"
        job_name = "Schedule API Processor"
        schedule = "15 8,14 * * *"  # 08:15 and 14:15 daily
        start_time = datetime.now()
        
        # Record job start
        execution_id = self.record_job_start(job_type, job_name, schedule, start_time)
        
        logger.info("Starting schedule API processing cron job at %s", start_time.strftime('%Y-%m-%d %H:%M:%S'))
        
        try:
            # Get API configuration from database
            api_config = await self._get_api_config_from_db()
            
            if not api_config:
                logger.warning("No schedule API configuration found in database")
                result = {
                    'status': 'error',
                    'error': 'No API configuration found',
                    'execution_time': (datetime.now() - start_time).total_seconds()
                }
                if execution_id:
                    end_time = datetime.now()
                    self.record_job_completion(
                        job_type, job_name, schedule, start_time, end_time, execution_id,
                        "error", False, execution_id, result, "No API configuration found"
                    )
                return result
            
            # Fetch data from API
            api_response = await self._fetch_schedule_data(api_config)
            
            if not api_response:
                logger.error("Failed to fetch schedule data from API")
                result = {
                    'status': 'error',
                    'error': 'Failed to fetch API data',
                    'execution_time': (datetime.now() - start_time).total_seconds()
                }
                if execution_id:
                    end_time = datetime.now()
                    self.record_job_completion(
                        job_type, job_name, schedule, start_time, end_time, execution_id,
                        "error", False, execution_id, result, "Failed to fetch API data"
                    )
                return result
            
            # Process and store the data
            supply_serve_stored = 0
            
            data = api_response.get("data", {})
            logger.info("Processing schedule data for %d dates", len(data))
            
            for date_str, plant_schedules in data.items():
                try:
                    scheduled_at = datetime.strptime(date_str, "%Y-%m-%d")
                except ValueError:
                    logger.warning("Invalid date format: %s", date_str)
                    continue
                
                for plant_data in plant_schedules:
                    plant_id = plant_data.get("plant_id")
                    schedule_records = plant_data.get("schedule", [])
                    
                    if not plant_id or not schedule_records:
                        continue
                    
                    stored = await self._store_supply_serve(
                        scheduled_at=scheduled_at,
                        plant_id=plant_id,
                        schedule_data=schedule_records
                    )
                    supply_serve_stored += stored
            
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            
            result = {
                'status': 'success',
                'supply_serve_stored': supply_serve_stored,
                'execution_time': execution_time,
                'execution_id': execution_id
            }
            
            if execution_id:
                self.record_job_completion(
                    job_type, job_name, schedule, start_time, end_time, execution_id,
                    "success", True, execution_id, result
                )
            
            logger.info("Schedule API processing completed: supply_serve=%d records in %.2fs",
                       supply_serve_stored, execution_time)
            
            return result
            
        except Exception as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            logger.error("Error in schedule API processing cron job: %s", str(e))
            
            result = {'status': 'error', 'error': str(e), 'execution_time': execution_time}
            
            if execution_id:
                self.record_job_completion(
                    job_type, job_name, schedule, start_time, end_time, execution_id,
                    "error", False, execution_id, result, str(e)
                )
            
            return result
    
    async def _get_api_config_from_db(self) -> Optional[Dict[str, Any]]:
        """
        Get schedule API configuration from database (providers collection)
        
        Returns:
            Optional[Dict[str, Any]]: API configuration or None
        """
        try:
            client = get_mongo_client(self.mongo_uri, "schedule_api_config")
            db = client[self.database_name]
            
            # Look for a provider with code "schedule_api_provider"
            provider = db["providers"].find_one({
                "code": "schedule_api_provider",
                "enabled": True
            })
            
            if not provider:
                # Also try looking for provider type
                provider = db["providers"].find_one({
                    "provider_type": "schedule_api",
                    "enabled": True
                })
            
            if not provider or not provider.get("metadata"):
                return None
            
            metadata = provider.get("metadata", {})
            
            # Build API config from provider metadata
            api_config = {
                'url': metadata.get('api_url'),
                'api_key': metadata.get('api_key'),
                'timeout': metadata.get('timeout', self.api_timeout),
                'auth_header': metadata.get('auth_header', 'X-API-Key'),
            }
            
            if not api_config['url']:
                logger.error("API URL not configured in provider metadata")
                return None
            
            if not api_config['api_key']:
                logger.error("API key not configured in provider metadata")
                return None
                
            logger.info("Loaded schedule API config: url=%s", api_config['url'])
            return api_config
            
        except Exception as e:
            logger.error("Error getting schedule API config from database: %s", str(e))
            return None
    
    async def _fetch_schedule_data(self, api_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Fetch schedule data from the external API
        
        Args:
            api_config: API configuration dictionary
            
        Returns:
            Optional[Dict[str, Any]]: API response data or None
        """
        try:
            url = api_config['url']
            api_key = api_config['api_key']
            timeout = api_config.get('timeout', self.api_timeout)
            auth_header = api_config.get('auth_header', 'X-API-Key')
            
            # Use tomorrow's date
            tomorrow = datetime.now() + timedelta(days=1)
            target_date = tomorrow.strftime('%Y-%m-%d')
            
            # Build request
            headers = {
                auth_header: api_key,
                'User-Agent': 'EPMS-Backend/1.0'
            }
            
            params = {
                'start_date': target_date,
                'end_date': target_date
            }
            
            logger.info("Fetching schedule data from %s for date %s", url, target_date)
            
            for attempt in range(self.retry_attempts):
                try:
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                        async with session.get(url, params=params, headers=headers) as response:
                            
                            if response.status != 200:
                                error_msg = f"API returned status {response.status}: {response.reason}"
                                logger.error("Schedule API request failed: %s", error_msg)
                                
                                if attempt < self.retry_attempts - 1:
                                    logger.info("Retrying schedule API request (attempt %d/%d)", 
                                               attempt + 2, self.retry_attempts)
                                    await asyncio.sleep(self.retry_delay)
                                    continue
                                else:
                                    return None
                            
                            response_text = await response.text()
                            response_data = json.loads(response_text)
                            # response_data = await response.json()
                            
                            # Log API response to integration_apilog
                            try:
                                log_entity = IntegrationApiLogEntity(
                                    provider_id="schedule_api_provider",
                                    raw_data=response,
                                    timestamp=datetime.now(),
                                    metadata={
                                        "url": url,
                                        "method": "GET",
                                        "status_code": response.status,
                                        "params": params,
                                        "attempt": attempt + 1
                                    }
                                )
                                log_id = self.integration_apilog_repository.save_log("schedule", log_entity)
                                logger.info("Logged API response to integration_apilog with id: %s", log_id)
                            except Exception as log_error:
                                logger.warning("Failed to log API response: %s", log_error)
                            
                            logger.info("Successfully fetched schedule data from API")
                            return response_data
                            
                except aiohttp.ClientError as e:
                    logger.error("HTTP client error (attempt %d/%d): %s", 
                               attempt + 1, self.retry_attempts, str(e))
                    
                    if attempt < self.retry_attempts - 1:
                        await asyncio.sleep(self.retry_delay)
                    else:
                        return None
            
            return None
            
        except Exception as e:
            logger.error("Error fetching schedule data: %s", str(e))
            return None
    
    async def _store_supply_serve(
        self, 
        scheduled_at: datetime, 
        plant_id: str, 
        schedule_data: List[Dict[str, Any]]
    ) -> int:
        """
        Store schedule data in supply_serve collection as SG records
        
        Args:
            scheduled_at: Schedule date
            plant_id: Plant identifier
            schedule_data: List of {time_block, sg_volume_after_loss_mw} records
            
        Returns:
            int: Number of records stored
        """
        try:
            if not schedule_data:
                return 0
            
            client = get_mongo_client(self.mongo_uri, "schedule_supply_serve")
            db = client[self.database_name]
            collection = db["serve_dam_scheduling"]
            
            current_time = datetime.now()
            stored_count = 0
            
            for record in schedule_data:
                try:
                    time_block = record.get("time_block")
                    sg_volume = record.get("sg_volume_after_loss_mw", 0.0)
                    
                    if time_block is None:
                        continue
                    
                    filter_doc = {
                        "scheduled_at": scheduled_at,
                        "time_block": int(time_block),
                        "plant_id": plant_id,
                    }
                    
                    update_doc = {
                        "$set": {
                            "scheduled_at": scheduled_at,
                            "time_block": int(time_block),
                            "plant_id": plant_id,
                            "volume": float(sg_volume),
                            "updated_at": current_time,
                            "uploadedBy": "schedule_api"
                        },
                        "$setOnInsert": {
                            "created_at": current_time
                        },
                        "$inc": {
                            "system_revision_number": 1
                        }
                    }
                    
                    result = await collection.update_one(filter_doc, update_doc, upsert=True)
                    
                    if result.upserted_id or result.modified_count > 0:
                        stored_count += 1
                        
                except Exception as record_error:
                    logger.warning("Failed to store supply_serve record: plant=%s, time_block=%s, error=%s", 
                                  plant_id, record.get("time_block"), str(record_error))
            
            logger.debug("Stored %d supply_serve SG records for plant %s on %s", 
                       stored_count, plant_id, scheduled_at.strftime('%Y-%m-%d'))
            return stored_count
            
        except Exception as e:
            logger.error("Error storing supply_serve: %s", str(e))
            return 0
