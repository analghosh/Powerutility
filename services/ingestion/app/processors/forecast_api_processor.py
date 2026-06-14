"""
Forecast API Processor
Handles fetching forecast data from external API and storing in limit_values,
forecast_serve (DAF, source=dam_forecasted_api), and demand_serve collections.
"""

import json
import logging
import asyncio
import aiohttp
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from libs.database.mongo_client_factory import get_mongo_client
from libs.models.forecast_serve_entity import FORECAST_SOURCE_DAM_API
from libs.models.integration_apilog_entity import IntegrationApiLogEntity
from ..repository.forecast_serve_repository import forecast_serve_repository
from services.ingestion.app.core.config import settings
from services.ingestion.app.core.logger import get_ingestion_logger
from services.ingestion.app.processors.base_raw_data_processor import BaseRawDataProcessor
from services.ingestion.app.repositories.integration_apilog_repository import IntegrationApiLogRepository

logger = get_ingestion_logger("forecast_api_processor")

DAF_TYPE = "DAF"
DAM_SOURCE_FORECAST = FORECAST_SOURCE_DAM_API


class ForecastAPIProcessor(BaseRawDataProcessor):
    """
    Processor for fetching forecast data from external API and storing:
    - Exchange forecasted prices (DAM, GDAM, HPDAM) in limit_values collection
    - DAF demand forecasts in forecast_serve (source=dam_forecasted_api)
    - DAF demand forecasts in demand_serve collection
    """
    
    def __init__(self, audit_logger=None):
        """Initialize forecast API processor"""
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
        self.forecast_repository = forecast_serve_repository
        
    async def execute_cron_job(self) -> Dict[str, Any]:
        """
        Execute forecast API processing as a cron job
        """
        job_type = "forecast_api_processor"
        job_name = "Forecast API Processor"
        schedule = "0 6 * * *"
        start_time = datetime.now()
        
        # Record job start
        execution_id = await self.record_job_start(job_type, job_name, schedule, start_time)
        
        logger.info("Starting forecast API processing cron job at %s", start_time.strftime('%Y-%m-%d %H:%M:%S'))
        
        try:
            # Get API configuration from database
            api_config = await self._get_api_config_from_db()
            
            if not api_config:
                logger.warning("No forecast API configuration found in database")
                result = {
                    'status': 'error',
                    'error': 'No API configuration found',
                    'execution_time': (datetime.now() - start_time).total_seconds()
                }
                if execution_id:
                    end_time = datetime.now()
                    await self.record_job_completion(
                        job_type, job_name, schedule, start_time, end_time, execution_id,
                        "error", False, execution_id, result, "No API configuration found"
                    )
                return result
            
            # Fetch data from API
            api_response = await self._fetch_forecast_data(api_config)
            
            if not api_response:
                logger.error("Failed to fetch forecast data from API")
                result = {
                    'status': 'error',
                    'error': 'Failed to fetch API data',
                    'execution_time': (datetime.now() - start_time).total_seconds()
                }
                if execution_id:
                    end_time = datetime.now()
                    await self.record_job_completion(
                        job_type, job_name, schedule, start_time, end_time, execution_id,
                        "error", False, execution_id, result, "Failed to fetch API data"
                    )
                return result
            
            # Process and store the data
            limit_values_stored = 0
            forecast_serve_stored = 0
            demand_serve_stored = 0
            
            data_items = api_response.get("data", [])
            logger.info("Processing %d date entries from API response", len(data_items))
            
            for date_entry in data_items:
                date_str = date_entry.get("date")
                if not date_str:
                    continue
                    
                # Parse date
                try:
                    scheduled_at = datetime.strptime(date_str, "%Y-%m-%d")
                except ValueError:
                    logger.warning("Invalid date format: %s", date_str)
                    continue
                
                # Process exchange data (forecasted prices) -> limit_values
                exchange_data = date_entry.get("exchange", [])
                for exchange in exchange_data:
                    px_data = exchange.get("px_data", {})
                    
                    # Process DAM, GDAM, HPDAM
                    for market in ["DAM", "GDAM", "HPDAM"]:
                        market_data = px_data.get(market, [])
                        if market_data:
                            stored = await self._store_limit_values(
                                scheduled_at=scheduled_at,
                                market=market,
                                data=market_data
                            )
                            limit_values_stored += stored
                
                # Process demand data -> forecast_serve, then demand_serve
                demand_data = date_entry.get("demand", {})
                demand_market = demand_data.get("market", "DAF")
                demand_records = demand_data.get("data", [])
                
                if demand_records:
                    stored_fs, forecast_revision = await self._store_forecast_serve(
                        scheduled_at=scheduled_at,
                        market_type=demand_market,
                        data=demand_records,
                    )
                    forecast_serve_stored += stored_fs

                    stored = await self._store_demand_serve(
                        scheduled_at=scheduled_at,
                        market_type=demand_market,
                        data=demand_records,
                        forecast_revision=forecast_revision,
                    )
                    demand_serve_stored += stored
            
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            
            result = {
                'status': 'success',
                'limit_values_stored': limit_values_stored,
                'forecast_serve_stored': forecast_serve_stored,
                'demand_serve_stored': demand_serve_stored,
                'execution_time': execution_time,
                'execution_id': execution_id
            }
            
            if execution_id:
                await self.record_job_completion(
                    job_type, job_name, schedule, start_time, end_time, execution_id,
                    "success", True, execution_id, result
                )
            
            logger.info(
                "Forecast API processing completed: limit_values=%d, forecast_serve=%d, "
                "demand_serve=%d in %.2fs",
                limit_values_stored,
                forecast_serve_stored,
                demand_serve_stored,
                execution_time,
            )
            
            return result
            
        except Exception as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            logger.error("Error in forecast API processing cron job: %s", str(e))
            
            result = {'status': 'error', 'error': str(e), 'execution_time': execution_time}
            
            if execution_id:
                await self.record_job_completion(
                    job_type, job_name, schedule, start_time, end_time, execution_id,
                    "error", False, execution_id, result, str(e)
                )
            
            return result
    
    async def _get_api_config_from_db(self) -> Optional[Dict[str, Any]]:
        """
        Get forecast API configuration from database (providers collection)
        
        Returns:
            Optional[Dict[str, Any]]: API configuration or None
        """
        try:
            client = get_mongo_client(self.mongo_uri, "forecast_api_config")
            db = client[self.database_name]
            
            # Look for a provider with code "forecast_api_provider" or similar
            provider = db["providers"].find_one({
                "code": "forecast_api_provider",
                "enabled": True
            })
            
            if not provider:
                # Also try looking for provider type
                provider = db["providers"].find_one({
                    "provider_type": "forecast_api",
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
                'date_range_days': metadata.get('date_range_days', 2),  # Default: tomorrow and day after
                'area': metadata.get('area', 'Puducherry')
            }
            
            if not api_config['url']:
                logger.error("API URL not configured in provider metadata")
                return None
            
            if not api_config['api_key']:
                logger.error("API key not configured in provider metadata")
                return None
                
            logger.info("Loaded forecast API config: url=%s", api_config['url'])
            return api_config
            
        except Exception as e:
            logger.error("Error getting forecast API config from database: %s", str(e))
            return None
    
    async def _fetch_forecast_data(self, api_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Fetch forecast data from the external API
        
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
            date_range_days = api_config.get('date_range_days', 2)
            
            # Calculate date range (tomorrow to tomorrow + date_range_days)
            tomorrow = datetime.now() + timedelta(days=1)
            start_date = tomorrow.strftime('%Y-%m-%d')
            end_date = (tomorrow + timedelta(days=date_range_days - 1)).strftime('%Y-%m-%d')
            
            # Build request
            headers = {
                auth_header: api_key,
                'User-Agent': 'EPMS-Backend/1.0'
            }
            
            params = {
                'start_date': start_date,
                'end_date': end_date
            }
            
            logger.info("Fetching forecast data from %s for dates %s to %s", url, start_date, end_date)
            
            for attempt in range(self.retry_attempts):
                try:
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                        async with session.get(url, params=params, headers=headers) as response:
                            
                            if response.status != 200:
                                error_msg = f"API returned status {response.status}: {response.reason}"
                                logger.error("Forecast API request failed: %s", error_msg)
                                
                                if attempt < self.retry_attempts - 1:
                                    logger.info("Retrying forecast API request (attempt %d/%d)", 
                                               attempt + 2, self.retry_attempts)
                                    await asyncio.sleep(self.retry_delay)
                                    continue
                                else:
                                    return None
                            
                            response_text = await response.text()
                            try:
                                response_data = json.loads(response_text)
                            except json.JSONDecodeError as e:
                                logger.error("Forecast API returned invalid JSON: %s", e)
                                return None
                            
                            # Log API response to integration_apilog
                            try:
                                log_entity = IntegrationApiLogEntity(
                                    provider_id="forecast_api_provider",
                                    raw_data=response_data,
                                    timestamp=datetime.now(),
                                    metadata={
                                        "url": url,
                                        "method": "GET",
                                        "status_code": response.status,
                                        "params": params,
                                        "attempt": attempt + 1
                                    }
                                )
                                log_id = self.integration_apilog_repository.save_log("forecast", log_entity)
                                logger.info("Logged API response to integration_apilog with id: %s", log_id)
                            except Exception as log_error:
                                logger.warning("Failed to log API response: %s", log_error)
                            
                            logger.info("Successfully fetched forecast data from API")
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
            logger.error("Error fetching forecast data: %s", str(e))
            return None
    
    async def _store_limit_values(
        self, 
        scheduled_at: datetime, 
        market: str, 
        data: List[Dict[str, Any]]
    ) -> int:
        """
        Store exchange forecasted prices in limit_values collection
        
        Args:
            scheduled_at: Schedule date
            market: Market type (DAM, GDAM, HPDAM)
            data: List of {time_block, forecasted_price} records
            
        Returns:
            int: Number of records stored
        """
        try:
            if not data:
                return 0
            
            client = get_mongo_client(self.mongo_uri, "forecast_limit_values")
            collection = client[self.database_name]["limit_values"]
            
            current_time = datetime.now()
            stored_count = 0
            
            # Build the limit_values id based on market
            limit_id = f"{market}_forecasted_price"
            
            for record in data:
                try:
                    time_block = record.get("time_block")
                    forecasted_price = record.get("forecasted_price", 0.0)
                    
                    if time_block is None:
                        continue
                    
                    filter_doc = {
                        "scheduled_at": scheduled_at,
                        "block": int(time_block),
                        "id": limit_id,
                        "market": market,
                        "category": "Limits",
                        "area": "Puducherry"
                    }
                    
                    update_doc = {
                        "$set": {
                            "scheduled_at": scheduled_at,
                            "block": int(time_block),
                            "id": limit_id,
                            "market": market,
                            "category": "Limits",
                            "area": "Puducherry",
                            "values": float(forecasted_price),
                            "unit": "Rs/MWh",
                            "is_active": True,
                            "source": "forecast_api",
                            "updated_at": current_time,
                            "updated_by": "forecast_api_processor"
                        },
                        "$setOnInsert": {
                            "version": 1,
                            "created_at": current_time,
                            "created_by": "forecast_api_processor"
                        }
                    }
                    
                    result = await asyncio.to_thread(
                        collection.update_one, filter_doc, update_doc, upsert=True
                    )
                    
                    if result.upserted_id or result.modified_count > 0:
                        stored_count += 1
                        
                except Exception as record_error:
                    logger.warning("Failed to store limit_values record: time_block=%s, error=%s", 
                                  record.get("time_block"), str(record_error))
            
            logger.info("Stored %d %s limit_values records for date %s", 
                       stored_count, market, scheduled_at.strftime('%Y-%m-%d'))
            return stored_count
            
        except Exception as e:
            logger.error("Error storing limit_values: %s", str(e))
            return 0
    
    async def _store_forecast_serve(
        self,
        scheduled_at: datetime,
        market_type: str,
        data: List[Dict[str, Any]],
    ) -> tuple[int, Optional[int]]:
        """
        Store DAF demand forecasts in forecast_serve (source=dam_forecasted_api).

        Persists every time block present in the API payload for this delivery date.
        Assigns one forecast_revision per fetch (increments per day per source).

        Returns:
            Tuple of (stored_count, forecast_revision).
        """
        try:
            if not data:
                return 0, None

            time_block_data: Dict[int, Dict[str, Any]] = {}
            for record in data:
                tb = record.get("time_block")
                if tb is not None and int(tb) not in time_block_data:
                    time_block_data[int(tb)] = record

            forecast_revision = await self.forecast_repository.next_forecast_revision(
                scheduled_at,
                DAM_SOURCE_FORECAST,
                market_type=market_type,
            )

            bulk_records: List[Dict[str, Any]] = []
            for time_block, record in time_block_data.items():
                forecast_mw = float(record.get("demand_forecast_mw", 0.0) or 0.0)
                bulk_records.append(
                    {
                        "scheduled_at": scheduled_at,
                        "time_block": int(time_block),
                        "forecast": forecast_mw,
                        "forecast_revision": forecast_revision,
                    }
                )

            if not bulk_records:
                return 0, forecast_revision

            result = await self.forecast_repository.bulk_insert_forecasts(
                bulk_records,
                source=DAM_SOURCE_FORECAST,
                market_type=market_type,
            )
            stored_count = result.get("inserted", 0) or result.get("upserted", 0)
            logger.info(
                "Stored %d forecast_serve records (%s) for date %s type %s forecast_revision=%d",
                stored_count,
                DAM_SOURCE_FORECAST,
                scheduled_at.strftime("%Y-%m-%d"),
                market_type,
                forecast_revision,
            )
            return stored_count, forecast_revision
        except Exception as e:
            logger.error("Error storing forecast_serve: %s", str(e))
            return 0, None

    async def _store_demand_serve(
        self, 
        scheduled_at: datetime, 
        market_type: str, 
        data: List[Dict[str, Any]],
        forecast_revision: Optional[int] = None,
    ) -> int:
        """
        Store demand forecasts in demand_serve collection
        
        Args:
            scheduled_at: Schedule date
            market_type: Market type (DAF, IDF)
            data: List of {time_block, demand_forecast_mw} records
            
        Returns:
            int: Number of records stored
        """
        try:
            if not data:
                return 0
            
            client = get_mongo_client(self.mongo_uri, "forecast_demand_serve")
            collection = client[self.database_name]["demand_serve"]
            
            current_time = datetime.now()
            stored_count = 0
            
            # Group by time_block taking the first value (in case of duplicates)
            time_block_data = {}
            for record in data:
                tb = record.get("time_block")
                if tb is not None and tb not in time_block_data:
                    time_block_data[tb] = record
            
            for time_block, record in time_block_data.items():
                try:
                    forecast_mw = float(record.get("demand_forecast_mw", 0.0))
                    
                    filter_doc = {
                        "scheduled_at": scheduled_at,
                        "time_block": int(time_block),
                        "type": market_type
                    }
                    
                    # Build update document with calculated fields
                    # mape, mpe, rostering are initialized to 0 as actual data isn't available yet
                    update_doc = {
                        "$set": {
                            "scheduled_at": scheduled_at,
                            "time_block": int(time_block),
                            "type": market_type,
                            "forecast": forecast_mw,
                            "updated_at": current_time,
                            "timestamp": current_time,
                            "source_forecast": DAM_SOURCE_FORECAST,
                            # Calculated fields - initialized to 0, will be updated when actual data arrives
                            "comp_act_demand": forecast_mw,
                            "comp_udm" : forecast_mw
                            
                        },
                        "$setOnInsert": {
                            "inserted_at": current_time,
                            "created_at": current_time,
                            "source": "forecast_api",
                            "system_revision_number": 1
                        }
                    }
                    if forecast_revision is not None:
                        update_doc["$set"]["forecast_revision"] = forecast_revision
                    
                    result = await asyncio.to_thread(
                        collection.update_one, filter_doc, update_doc, upsert=True
                    )
                    
                    if result.upserted_id or result.modified_count > 0:
                        stored_count += 1
                        
                except Exception as record_error:
                    logger.warning("Failed to store demand_serve record: time_block=%s, error=%s", 
                                  time_block, str(record_error))
            
            logger.info("Stored %d demand_serve records for date %s, type %s", 
                       stored_count, scheduled_at.strftime('%Y-%m-%d'), market_type)
            return stored_count
            
        except Exception as e:
            logger.error("Error storing demand_serve: %s", str(e))
            return 0
