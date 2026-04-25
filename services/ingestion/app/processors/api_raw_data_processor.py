"""
API Raw Data Processor
Handles the processing of API raw data downloads into raw data entities

Example Provider Configuration:
{
    "code": "weather_api_provider",
    "name": "Weather API Data Provider",
    "data_type": "WEATHER",
    "ingestion_type": "API",
    "enabled": true,
    "metadata": {
        "api_url": "https://api.weather.com/v3/wx/forecast/hourly/2day/enterprise",
        "method": "GET",
        "timeout": 30,
        "params": {
            "geocode": "11.55,79.49",
            "format": "json",
            "units": "e",
            "language": "en-US",
            "apiKey": "543558"
        },
        "headers": {
            "Accept": "application/json",
            "User-Agent": "EPMS-Backend/1.0"
        },
        "response_format": "json",
        "data_path": null,
        "config": {
            "data_source": "weather_api",
            "frequency": "hourly"
        }
    },
    "fields": [
        {
            "field_name": "hour",
            "field_type": "datetime",
            "is_required": true
        },
        {
            "field_name": "cloudCover",
            "field_type": "float",
            "is_required": true
        }
    ]
}

API Configuration Options:
- api_url: API endpoint URL (required)
- method: HTTP method - GET, POST, PUT, DELETE (default: "GET")
- timeout: Request timeout in seconds (default: 30)
- params: Query parameters as key-value pairs (optional)
- headers: HTTP headers as key-value pairs (optional)
- body: Request body for POST/PUT requests (optional)
- response_format: Expected response format - "json", "xml", "csv" (default: "json")
- data_path: JSONPath to extract data from response (optional, for nested JSON)
- auth_type: Authentication type - "bearer", "basic", "api_key" (optional)
- auth_config: Authentication configuration (optional)
"""

import asyncio
import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import csv
import io
import base64

import aiohttp
import pandas as pd

from libs.models.provider_models import DataType, IngestionType, ProviderEntity, ProviderSummary
from libs.core.job_entities import JobDetail
from services.ingestion.app.core.config import settings
from services.ingestion.app.processors.base_raw_data_processor import BaseRawDataProcessor
from libs.audit_queue.simple_logger import AuditLogger
from libs.messaging.job_publisher import JobPublisher
from libs.parsers.parser_factory import create_parser
from libs.models.integration_apilog_entity import IntegrationApiLogEntity
from services.ingestion.app.repositories.integration_apilog_repository import IntegrationApiLogRepository

logger = logging.getLogger("api_raw_data_processor")


class APIRawDataProcessor(BaseRawDataProcessor):
    """
    Service for managing API raw data processing tasks with job detail recording
    """

    def __init__(self, audit_logger: AuditLogger):
        """Initialize API raw data processor"""
        super().__init__()
        self.audit_logger = audit_logger
        self.job_publisher = JobPublisher()
        self.api_timeout = getattr(settings, 'API_TIMEOUT', 30)
        self.retry_attempts = getattr(settings, 'API_RETRY_ATTEMPTS', 1)
        self.retry_delay = getattr(settings, 'API_RETRY_DELAY', 5)
        
        self.integration_apilog_repository = IntegrationApiLogRepository(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE
        )

    async def execute_cron_job(self) -> Dict[str, Any]:
        """
        Execute API raw data processing as a cron job (implementation of abstract method)
        """
        # Process API providers for all data types
        return await self.process_all_api_data()

    async def _get_all_api_providers(self) -> Optional[List[ProviderSummary]]:
        """
        Get all enabled API providers regardless of data type
        
        Returns:
            Optional[List[ProviderSummary]]: List of API providers or None if error
        """
        try:
            # Get all enabled API providers
            provider_response = await self.provider_service.get_all_providers(
                ingestion_type=IngestionType.API, enabled=True
            )
            
            if not provider_response or not hasattr(provider_response, 'providers') or not provider_response.providers:
                logger.warning("No API providers found")
                return []
            
            return provider_response.providers
            
        except Exception as e:
            logger.error("Error getting API providers: %s", str(e))
            return None

    async def process_all_api_data(self) -> Dict[str, Any]:
        """
        Execute API raw data processing for all data types
        """
        job_type = "api_raw_data_processor"
        job_name = "API Raw Data Processor"
        schedule = "*/15 * * * *"  # Every 15 minutes
        start_time = datetime.now()

        # Record job start
        execution_id = await self.record_job_start(job_type, job_name, schedule, start_time)

        logger.info("Starting API raw data processing cron job at %s", start_time.strftime('%Y-%m-%d %H:%M:%S'))

        try:
            # Get all enabled API providers regardless of data type
            providers = await self._get_all_api_providers()

            if not providers:
                logger.info("No API providers found for processing")
                end_time = datetime.now()
                result = {
                    'status': 'success',
                    'processed_providers': 0,
                    'execution_time': (end_time - start_time).total_seconds()
                }

                # Record job completion
                if execution_id:
                    await self.record_job_completion(
                        job_type, job_name, schedule, start_time, end_time, execution_id,
                        "success", True, execution_id, result
                    )

                return result

            logger.info("Found %d API providers for processing", len(providers))

            processed_providers = 0
            failed_providers = 0

            # Process data for each provider
            for provider in providers:
                try:
                    provider_code = provider.code
                    data_type = provider.data_type 

                    logger.info("Processing API data for provider: %s, data type: %s", provider_code, data_type)

                    # Process API data for this provider
                    result = await self._process_provider_data(provider)

                    if result and result.get('success', False):
                        logger.info("Successfully processed API data for provider: %s, data_type: %s",
                                       provider_code, data_type)
                        processed_providers += 1
                        
                        # Publish job trigger
                        dt_value = str(data_type.value if hasattr(data_type, 'value') else data_type).lower()
                        trigger_job_type = f"{dt_value}_data_processing"
                        logger.info(f"Triggering job: {trigger_job_type} for provider {provider_code}")
                        self.job_publisher.publish_job_trigger(
                            job_type=trigger_job_type,
                            source_service="ingestion",
                            payload={
                                "provider_code": provider_code,
                                "data_type": dt_value,
                                "record_count": result.get('stored_records', 0)
                            }
                        )
                    else:
                        error_msg = result.get('error', 'Unknown error') if result else 'No result returned'
                        logger.error("Failed to process API data for provider: %s, data_type: %s, error: %s",
                                       provider_code, data_type, error_msg)
                        failed_providers += 1

                except (ValueError, OSError, RuntimeError) as provider_error:
                    failed_providers += 1
                    provider_code = getattr(provider, 'code', 'unknown')
                    logger.error("Exception processing provider %s: %s", provider_code, str(provider_error))

            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()

            # Determine overall status
            overall_success = processed_providers > 0 and failed_providers == 0
            status = "success" if overall_success else ("partial_success" if processed_providers > 0 else "error")

            result = {
                'status': status,
                'processed_providers': processed_providers,
                'failed_providers': failed_providers,
                'total_providers': len(providers),
                'execution_time': execution_time,
                'execution_id': execution_id
            }

            # Record job completion
            if execution_id:
                error_message = None if overall_success else f"Processed {processed_providers}, failed {failed_providers}"
                await self.record_job_completion(
                    job_type, job_name, schedule, start_time, end_time, execution_id,
                    status, overall_success, execution_id, result, error_message
                )

            logger.info("API raw data processing cron job completed in %.2fs", execution_time)
            logger.info("   - Processed: %d providers", processed_providers)
            logger.info("   - Failed: %d providers", failed_providers)
            logger.info("   - Total: %d providers", len(providers))
            logger.info("   - Execution ID: %s", execution_id)

            return result

        except (ValueError, OSError, RuntimeError) as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            logger.error("Error in API raw data processing cron job after %.2fs: %s", execution_time, str(e))

            result = {'status': 'error', 'error': str(e), 'execution_time': execution_time}

            # Record job completion with error
            if execution_id:
                await self.record_job_completion(
                    job_type, job_name, schedule, start_time, end_time, execution_id,
                    "error", False, execution_id, result, str(e)
                )

            return result

    async def _process_provider_data(self, provider: ProviderSummary) -> Dict[str, Any]:
        """
        Process API data for a single provider
        
        Args:
            provider: Provider entity to process
            
        Returns:
            Dict[str, Any]: Processing result
        """
        try:
            provider_code = provider.code
            data_type = provider.data_type
            
            logger.info("Processing provider: %s, data type: %s", provider_code, data_type)
            
            # Get full provider entity for processing
            full_provider = await self._get_full_provider_entity(provider_code)
            if not full_provider:
                logger.error("Could not retrieve full provider entity for: %s", provider_code)
                return {'success': False, 'error': 'Provider entity not found'}
            
            # Extract API configuration from provider metadata
            api_config = self._extract_api_config_from_provider(full_provider)
            if not api_config:
                logger.warning("No API configuration found for provider: %s", provider_code)
                return {'success': False, 'error': 'No API configuration found in provider metadata'}
            
            # Fetch data from API
            log_id = None
            try:
                fetch_result = await self._fetch_api_data(api_config, full_provider)
                api_data = fetch_result.get("data", [])
                log_id = fetch_result.get("log_id")
                
                if not api_data:
                    logger.warning("No data fetched from API for provider: %s", provider_code)
                    return {'success': False, 'error': 'No data fetched from API'}
                
                logger.info("Successfully fetched %d records from API for provider: %s", 
                           len(api_data), provider_code)
                        
            except Exception as api_error:
                logger.error("Error fetching from API for provider %s: %s", provider_code, str(api_error))
                return {'success': False, 'error': f'API fetch error: {str(api_error)}'}
            
            # Special handling for weather data type - transpose the data
            if data_type == DataType.WEATHER:
                api_data = self._transpose_weather_data(api_data, full_provider)
                logger.info("Transposed weather data for provider: %s, resulting in %d records", 
                           provider_code, len(api_data))
            
            # Validate and map data using base class methods
            validation_result = self.validate_data_schema(api_data, full_provider)
            if not validation_result["success"]:
                logger.error("Data validation failed for provider %s: %s", 
                           provider_code, "; ".join(validation_result["errors"][:3]))
                return {'success': False, 'error': 'Data validation failed', 'validation_errors': validation_result["errors"]}
            
            # For weather data, transposition already handled field mapping
            # For other data types, apply field mapping
            if data_type == DataType.WEATHER:
                # Weather data is already mapped by transposition, use validated data directly
                mapped_data = validation_result["validated_data"]
                logger.info("Using transposed weather data directly (field mapping already applied)")
            else:
                # Map data to schema for non-weather data
                mapped_data = self.map_data_to_schema(validation_result["validated_data"], full_provider)
            
            # Store data using raw data service
            storage_result = await self._store_api_data(mapped_data, full_provider, [log_id] if log_id else None)
            
            # Handle optimization parser limit_values_data (direct insertion)
            limit_values_stored = 0
            if full_provider.metadata and '_parsed_limit_values_data' in full_provider.metadata:
                limit_values_data = full_provider.metadata.pop('_parsed_limit_values_data', [])
                if limit_values_data:
                    try:
                        limit_values_stored = await self._store_limit_values_direct(limit_values_data, full_provider)
                        logger.info("Stored %d limit_values records directly for provider %s", 
                                   limit_values_stored, provider_code)
                    except Exception as lv_error:
                        logger.error("Failed to store limit_values for provider %s: %s", provider_code, str(lv_error))
            
            if storage_result["success"]:
                logger.info("Successfully processed provider %s: %d records stored", 
                           provider_code, storage_result.get("stored_records", 0))
                return {
                    'success': True,
                    'provider_code': provider_code,
                    'data_type': data_type,
                    'fetched_records': len(api_data),
                    'validated_records': validation_result["validated_records"],
                    'stored_records': storage_result.get("stored_records", 0),
                    'limit_values_stored': limit_values_stored
                }
            else:
                logger.error("Failed to store data for provider %s: %s", provider_code, storage_result.get("error"))
                return {'success': False, 'error': storage_result.get("error")}
                
        except Exception as e:
            logger.error("Unexpected error processing provider %s: %s", provider.code, str(e))
            return {'success': False, 'error': str(e)}

    async def _get_full_provider_entity(self, provider_code: str) -> Optional[ProviderEntity]:
        """
        Get full provider entity by code

        Args:
            provider_code: Provider code to retrieve

        Returns:
            Optional[ProviderEntity]: Full provider entity or None if not found
        """
        try:
            # Use provider service to get full provider entity
            provider_entity = await self.provider_service.get_provider_by_code(provider_code)
            if provider_entity:
                return provider_entity
            else:
                logger.error("Provider not found for code: %s", provider_code)
                return None
        except Exception as e:
            logger.error("Error retrieving provider entity for code %s: %s", provider_code, str(e))
            return None

    async def _store_api_data(self, data: List[Dict[str, Any]], provider: ProviderEntity, log_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Store API data using the raw data service
        
        Args:
            data: Processed data to store
            provider: Provider entity
            log_ids: List of integration log IDs
            
        Returns:
            Dict[str, Any]: Storage result
        """
        try:
            if not data:
                return {'success': False, 'error': 'No data to store'}
            
            additional_metadata = provider.metadata.get("config", {}).copy() if provider.metadata else {}
            if log_ids:
                if len(log_ids) == 1:
                    additional_metadata["integration_apilog_id"] = log_ids[0]
                else:
                    additional_metadata["integration_apilog_id"] = log_ids
            
            # Use raw data service to store the data
            result = await self.raw_data_service.store_raw_data(
                raw_data=data,
                provider=provider,
                additional_metadata=additional_metadata
            )
            
            if result.get("success"):
                return {
                    'success': True,
                    'stored_records': len(data),
                    'batch_id': result.get("batch_id")
                }
            else:
                return {'success': False, 'error': result.get("error", "Unknown storage error")}
                
        except Exception as e:
            logger.error("Error storing API data: %s", str(e))
            return {'success': False, 'error': str(e)}

    async def _store_limit_values_direct(self, limit_values_data: List[Dict[str, Any]], provider: ProviderEntity) -> int:
        """
        Store limit_values data directly to the final limit_values collection.
        Bypass raw_data collection and scheduler processing as per latest requirement.
        
        Args:
            limit_values_data: List of limit values records to store
            provider: Provider entity for context
            
        Returns:
            int: Number of records stored
        """
        try:
            if not limit_values_data:
                return 0
            
            from libs.database.mongo_client_factory import get_mongo_client
            from datetime import datetime
            
            # Use ingestion database or scheduler database? 
            # Limit values is technically domain data, shared. 
            # We use MONGODB_URI from settings.
            
            mongo_uri = settings.MONGODB_URI
            database_name = settings.MONGODB_DATABASE
            limit_values_collection_name = getattr(settings, 'MONGODB_LIMIT_VALUES_COLLECTION', 'limit_values')
            
            client = get_mongo_client(mongo_uri, "ingestion_limit_values_direct")
            db = client[database_name]
            collection = db[limit_values_collection_name]
            
            current_time = datetime.now()
            stored_count = 0
            failed_count = 0
            
            for record in limit_values_data:
                try:
                    # Build the unique key for upsert
                    scheduled_at = record.get("scheduled_at")
                    if isinstance(scheduled_at, str):
                        try:
                            # Handle potential Z suffix
                            if scheduled_at.endswith('Z'):
                                scheduled_at = datetime.fromisoformat(scheduled_at.replace('Z', '+00:00'))
                            else:
                                scheduled_at = datetime.fromisoformat(scheduled_at)
                        except ValueError:
                            logger.warning(f"Invalid scheduled_at format: {scheduled_at}")
                            continue
                    
                    filter_doc = {
                        "scheduled_at": scheduled_at,
                        "block": record.get("block"),
                        "id": record.get("id"),
                        "market": record.get("market"),
                        "category": record.get("category", "Limits"),
                        "area": record.get("area", "")
                    }
                    
                    # Prepare the update document for FINAL collection
                    update_doc = {
                        "$set": {
                            "scheduled_at": scheduled_at,
                            "block": record.get("block"),
                            "id": record.get("id"),
                            "market": record.get("market"),
                            "category": record.get("category", "Limits"),
                            "area": record.get("area", ""),
                            "values": record.get("values"),
                            "is_active": True,
                            "source": record.get("source", "optimization_api"),
                            "updated_at": current_time,
                            "updated_by": "ingestion_api_processor_direct"
                        },
                        "$setOnInsert": {
                            "version": 1,
                            "created_at": current_time,
                            "created_by": "ingestion_api_processor_direct"
                        }
                    }
                    
                    # Add optional fields if present
                    if record.get("px_name"):
                        update_doc["$set"]["px_name"] = record.get("px_name")
                    if record.get("inserted_at"):
                        update_doc["$set"]["inserted_at"] = record.get("inserted_at")
                    
                    # Upsert the record
                    result = await collection.update_one(filter_doc, update_doc, upsert=True)
                    
                    if result.upserted_id or result.modified_count > 0:
                        stored_count += 1
                    
                except Exception as record_error:
                    failed_count += 1
                    logger.warning("Failed to process limit_values record: %s, error: %s", 
                                  record.get("id"), str(record_error))
            
            logger.info("Directly stored %d limit_values records to final collection (%d failed)", stored_count, failed_count)
            return stored_count
            
        except Exception as e:
            logger.error("Error storing limit_values directly: %s", str(e))
            raise


    def _replace_date_placeholders(self, data: Any) -> Any:
        """
        Recursively replace {today} and {tomorrow} placeholders in data
        
        Args:
            data: Data to process (dict, list, string, or other)
            
        Returns:
            Any: Processed data with replacements
        """
        if isinstance(data, str):
            # Simple replacements first for performance
            today_str = datetime.now().strftime('%d-%m-%Y')
            tomorrow_str = (datetime.now() + timedelta(days=1)).strftime('%d-%m-%Y')
            data = data.replace('{today}', today_str).replace('{tomorrow}', tomorrow_str)
            
            # Handle custom format specifiers if present (e.g. {today:%Y-%m-%d})
            if '{today:' in data or '{tomorrow:' in data:
                import re
                def replace_match(match):
                    tag = match.group(1)  # today or tomorrow
                    fmt = match.group(2)  # format string
                    
                    base_date = datetime.now()
                    if tag == 'tomorrow':
                        base_date += timedelta(days=1)
                    
                    return base_date.strftime(fmt)
                
                # Pattern to match {today:format} or {tomorrow:format}
                pattern = r'\{(today|tomorrow):([^}]*)\}'
                data = re.sub(pattern, replace_match, data)
            
            return data
            
        elif isinstance(data, dict):
            return {k: self._replace_date_placeholders(v) for k, v in data.items()}
            
        elif isinstance(data, list):
            return [self._replace_date_placeholders(item) for item in data]
            
        return data

    def _extract_api_config_from_provider(self, provider: ProviderEntity) -> Optional[Dict[str, Any]]:
        """
        Extract API configuration from provider metadata

        Args:
            provider: Provider entity containing metadata

        Returns:
            Optional[Dict[str, Any]]: API configuration or None if invalid
        """
        try:
            metadata = provider.metadata or {}

            if not isinstance(metadata, dict):
                logger.warning("Provider %s metadata is not a dictionary", provider.code)
                return None
            
            # Extract basic config
            api_url = metadata.get('api_url')
            method = metadata.get('method', 'GET').upper()
            timeout = metadata.get('timeout', self.api_timeout)
            response_format = metadata.get('response_format', 'json').lower()
            data_path = metadata.get('data_path')
            auth_type = metadata.get('auth_type')
            auth_config = metadata.get('auth_config', {})
            
            # Extract and process params
            raw_params = metadata.get('params', {})
            # Support provider-specific dynamic params (Keep existing logic for backward compatibility if needed)
            # But preferred method is now the placeholder replacement
            
            # Existing logic for optimization/rtm (preserves specific behavior)
            parser_type = metadata.get('parser_type', '')
            if parser_type in ['optimization', 'rtm_optimization']:
                raw_params = raw_params.copy() if raw_params else {}
                today_dash = datetime.now().strftime('%Y-%m-%d')
                tomorrow_dash = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
                raw_params['start_date'] = raw_params.get('start_date', today_dash)
                raw_params['end_date'] = raw_params.get('end_date', tomorrow_dash)
            
            # Apply generic placeholder replacement
            params = self._replace_date_placeholders(raw_params)
            
            # Process body
            # Check for body_type (e.g. 'json' or 'form_urlencoded')
            body_type = metadata.get('body_type', 'json')
            
            # Start with body_params if body is not set directly, or use body
            raw_body = metadata.get('body')
            if raw_body is None and 'body_params' in metadata:
                raw_body = metadata.get('body_params')
            
            # Existing legacy logic for 'Date' in body
            date_type = metadata.get('dateType', 'today')
            if isinstance(raw_body, dict) and 'Date' in raw_body and '{' not in str(raw_body['Date']):
                # Only apply legacy logic if it's NOT using new placeholders
                try:
                    raw_body = raw_body.copy()
                    if date_type == 'tomorrow':
                        raw_body['Date'] = (datetime.now() + timedelta(days=1)).strftime('%d-%m-%Y')
                    else:
                        raw_body['Date'] = datetime.now().strftime('%d-%m-%Y')
                except Exception:
                    pass

            # Apply generic placeholder replacement to body
            body = self._replace_date_placeholders(raw_body)

            # Validate required fields
            if not api_url:
                logger.error("API URL is required for provider %s", provider.code)
                return None
            
            # Validate method
            if method not in ['GET', 'POST', 'PUT', 'DELETE', 'PATCH']:
                logger.error("Invalid HTTP method '%s' for provider %s", method, provider.code)
                return None

            # Validate response format
            if response_format not in ['json', 'xml', 'csv', 'text']:
                logger.error("Invalid response format '%s' for provider %s", response_format, provider.code)
                return None

            api_config = {
                'url': api_url,
                'method': method,
                'timeout': timeout,
                'params': params,
                'headers': metadata.get('headers', {}),
                'body': body,
                'body_type': body_type,
                'response_format': response_format,
                'data_path': data_path,
                'auth_type': auth_type,
                'auth_config': auth_config
            }

            logger.info("Extracted API configuration for provider %s: url=%s, method=%s, body_type=%s", 
                       provider.code, api_config['url'], api_config['method'], api_config['body_type'])
            return api_config

        except (ValueError, TypeError, AttributeError) as e:
            logger.error("Error extracting API configuration from provider %s metadata: %s", provider.code, str(e))
            return None

    async def _fetch_api_data(self, api_config: Dict[str, Any], provider: ProviderEntity) -> Dict[str, Any]:
        """
        Fetch data from API endpoint
        
        Args:
            api_config: API configuration dictionary
            provider: Provider entity for additional context
        
        Returns:
            Dict[str, Any]: Result containing 'data' and 'log_id'
        """
        try:
            # Prepare request parameters
            url = api_config['url']
            method = api_config['method']
            timeout = api_config['timeout']
            params = api_config.get('params', {})
            headers = api_config.get('headers', {})
            body = api_config.get('body')
            body_type = api_config.get('body_type', 'json')
            
            # Add authentication if configured
            headers = self._add_authentication(headers, api_config.get('auth_type'), api_config.get('auth_config', {}))
            
            # Set default headers if not provided
            if 'User-Agent' not in headers:
                headers['User-Agent'] = 'EPMS-Backend/1.0'
            
            logger.info("Making %s request to %s for provider %s", method, url, provider.code)
            
            # Prepare request kwargs
            request_kwargs = {
                'method': method,
                'url': url,
                'headers': headers,
                'params': params if method == 'GET' else None
            }
            
            # Handle body based on type
            if method in ['POST', 'PUT', 'PATCH']:
                if body_type == 'form_urlencoded':
                    # For form_urlencoded, passed as data
                    request_kwargs['data'] = body
                    if 'Content-Type' not in headers:
                         # aiohttp adds this automatically if data is dict, but good to be explicit if needed
                         pass
                elif isinstance(body, dict):
                    # Default JSON behavior
                    request_kwargs['json'] = body
                else:
                    # String or other body
                    request_kwargs['data'] = body
            
            # Make HTTP request with retry logic
            for attempt in range(self.retry_attempts):
                try:
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                        async with session.request(**request_kwargs) as response:
                            
                            # Check response status
                            if response.status != 200:
                                error_msg = f"API returned status {response.status}: {response.reason}"
                                logger.error("API request failed for provider %s: %s", provider.code, error_msg)
                                
                                # Log failure attempts if needed, but integration log usually stores the interaction.
                                # Since we want to log the RAW data, we might want to log even failed responses?
                                # For now let's focus on logging the successful response before parsing.
                                
                                if attempt < self.retry_attempts - 1:
                                    logger.info("Retrying API request for provider %s (attempt %d/%d)", 
                                               provider.code, attempt + 2, self.retry_attempts)
                                    await asyncio.sleep(self.retry_delay)
                                    continue
                                else:
                                    raise Exception(error_msg)
                            
                            # Get response content
                            response_text = await response.text()
                            
                            # Blind Log
                            log_id = None
                            try:
                                try:
                                    raw_data_to_log = json.loads(response_text)
                                except (json.JSONDecodeError, ValueError):
                                    raw_data_to_log = response_text
                                log_entity = IntegrationApiLogEntity(
                                    provider_id=str(provider.id) if provider.id else provider.code,
                                    raw_data=raw_data_to_log,
                                    timestamp=datetime.now(),
                                    metadata={
                                        "url": url,
                                        "method": method,
                                        "status_code": response.status,
                                        "attempt": attempt + 1
                                    }
                                )
                                log_id = await self.integration_apilog_repository.save_log(provider.data_type.lower(), log_entity)
                            except Exception as e:
                                logger.error("Failed to integration log API response: %s", e)
                            
                            # Parse response based on format
                            parsed_data = self._parse_api_response(
                                response_text, 
                                api_config['response_format'], 
                                api_config.get('data_path'),
                                provider
                            )
                            
                            logger.info("Successfully fetched and parsed %d records from API for provider %s", 
                                       len(parsed_data), provider.code)
                            return {"data": parsed_data, "log_id": log_id}
                            
                except aiohttp.ClientError as e:
                    logger.error("HTTP client error for provider %s (attempt %d/%d): %s", 
                               provider.code, attempt + 1, self.retry_attempts, str(e))
                    
                    if attempt < self.retry_attempts - 1:
                        logger.info("Retrying API request for provider %s", provider.code)
                        await asyncio.sleep(self.retry_delay)
                    else:
                        raise Exception(f"API request failed after {self.retry_attempts} attempts: {str(e)}")
                        
                except Exception as e:
                    logger.error("Unexpected error during API request for provider %s (attempt %d/%d): %s (%s)", 
                               provider.code, attempt + 1, self.retry_attempts, str(e) or "No error message", repr(e))
                    
                    if attempt < self.retry_attempts - 1:
                        await asyncio.sleep(self.retry_delay)
                    else:
                        raise
            
            return {"data": [], "log_id": None}
            
        except Exception as e:
            logger.error("Error fetching API data for provider %s: %s (%s)", provider.code, str(e) or "No error message", repr(e))
            raise

    def _add_authentication(self, headers: Dict[str, str], auth_type: Optional[str], auth_config: Dict[str, Any]) -> Dict[str, str]:
        """
        Add authentication headers based on configuration
        
        Args:
            headers: Existing headers dictionary
            auth_type: Type of authentication (bearer, basic, api_key)
            auth_config: Authentication configuration
            
        Returns:
            Dict[str, str]: Updated headers with authentication
        """
        if not auth_type or not auth_config:
            return headers
        
        headers = headers.copy()
        
        try:
            if auth_type.lower() == 'bearer':
                token = auth_config.get('token')
                if token:
                    headers['Authorization'] = f'Bearer {token}'
                    
            elif auth_type.lower() == 'basic':
                username = auth_config.get('username')
                password = auth_config.get('password')
                if username and password:
                    credentials = base64.b64encode(f'{username}:{password}'.encode()).decode()
                    headers['Authorization'] = f'Basic {credentials}'
                    
            elif auth_type.lower() == 'api_key':
                api_key = auth_config.get('api_key')
                header_name = auth_config.get('header_name', 'X-API-Key')
                if api_key:
                    headers[header_name] = api_key
                    
        except Exception as e:
            logger.warning("Error adding authentication: %s", str(e))
        
        return headers

    def _parse_api_response(self, response_text: str, response_format: str, data_path: Optional[str], provider: ProviderEntity) -> List[Dict[str, Any]]:
        """
        Parse API response based on format
        
        Args:
            response_text: Raw response text
            response_format: Expected format (json, xml, csv, text)
            data_path: JSONPath to extract data (for JSON responses)
            provider: Provider entity for context
            
        Returns:
            List[Dict[str, Any]]: Parsed data records
        """
        try:
            # Check if provider has a specific parser type configured
            parser_type = None
            if provider.metadata and 'parser_type' in provider.metadata:
                parser_type = provider.metadata.get('parser_type')
                
            if parser_type:
                logger.info("Using specific parser '%s' for provider %s", parser_type, provider.code)
                parser = create_parser(provider, parser_type)
                
                # Use the parser to parse the content
                result = parser.parse(response_text, provider=provider)
                
                # Handle optimization parser special output format
                if parser_type in ['optimization', 'rtm_optimization'] and isinstance(result, dict):
                    # Optimization parser returns: {demand_data: [...], limit_values_data: [...]}
                    # For now, return demand_data for the normal flow
                    # limit_values_data will be handled separately by the processor
                    logger.info("Optimization parser returned: demand=%d records, limit_values=%d records",
                               len(result.get('demand_data', [])), len(result.get('limit_values_data', [])))
                    # Attach limit_values_data to provider metadata for later processing
                    if provider.metadata is None:
                        provider.metadata = {}
                    provider.metadata['_parsed_limit_values_data'] = result.get('limit_values_data', [])
                    return result.get('demand_data', [])
                
                return result

            # Fallback to standard response format parsing
            if response_format == 'json':
                return self._parse_json_response(response_text, data_path, provider)
            elif response_format == 'xml':
                return self._parse_xml_response(response_text, provider)
            elif response_format == 'csv':
                return self._parse_csv_response(response_text, provider)
            elif response_format == 'text':
                return self._parse_text_response(response_text, provider)
            else:
                logger.error("Unsupported response format: %s", response_format)
                return []
                
        except Exception as e:
            logger.error("Error parsing API response for provider %s: %s", provider.code, str(e))
            return []

    def _parse_json_response(self, response_text: str, data_path: Optional[str], provider: ProviderEntity) -> List[Dict[str, Any]]:
        """
        Parse JSON response
        
        Args:
            response_text: JSON response text
            data_path: JSONPath to extract data
            provider: Provider entity for context
            
        Returns:
            List[Dict[str, Any]]: Parsed data records
        """
        try:
            data = json.loads(response_text)
            
            # If data_path is specified, extract data from nested structure
            if data_path:
                # Simple JSONPath implementation for basic paths like "data.records" or "results"
                path_parts = data_path.split('.')
                for part in path_parts:
                    if isinstance(data, dict) and part in data:
                        data = data[part]
                    else:
                        logger.warning("Data path '%s' not found in response for provider %s", data_path, provider.code)
                        return []
            
            # Ensure data is a list
            if isinstance(data, dict):
                # Single record, wrap in list
                return [data]
            elif isinstance(data, list):
                return data
            else:
                logger.warning("Unexpected data type in JSON response for provider %s: %s", provider.code, type(data))
                return []
                
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON response for provider %s: %s", provider.code, str(e))
            return []

    def _parse_xml_response(self, response_text: str, provider: ProviderEntity) -> List[Dict[str, Any]]:
        """
        Parse XML response
        
        Args:
            response_text: XML response text
            provider: Provider entity for context
            
        Returns:
            List[Dict[str, Any]]: Parsed data records
        """
        try:
            root = ET.fromstring(response_text)
            records = []
            
            # Simple XML parsing - assumes each child element is a record
            for child in root:
                record = {}
                for elem in child:
                    record[elem.tag] = elem.text
                records.append(record)
            
            return records
            
        except ET.ParseError as e:
            logger.error("Invalid XML response for provider %s: %s", provider.code, str(e))
            return []

    def _parse_csv_response(self, response_text: str, provider: ProviderEntity) -> List[Dict[str, Any]]:
        """
        Parse CSV response
        
        Args:
            response_text: CSV response text
            provider: Provider entity for context
            
        Returns:
            List[Dict[str, Any]]: Parsed data records
        """
        try:
            # Use pandas to parse CSV
            df = pd.read_csv(io.StringIO(response_text))
            return df.to_dict('records')
            
        except Exception as e:
            logger.error("Error parsing CSV response for provider %s: %s", provider.code, str(e))
            return []

    def _parse_text_response(self, response_text: str, provider: ProviderEntity) -> List[Dict[str, Any]]:
        """
        Parse plain text response
        
        Args:
            response_text: Plain text response
            provider: Provider entity for context
            
        Returns:
            List[Dict[str, Any]]: Parsed data records
        """
        try:
            # For text responses, create a single record with the content
            return [{'content': response_text, 'timestamp': datetime.now().isoformat()}]
            
        except Exception as e:
            logger.error("Error parsing text response for provider %s: %s", provider.code, str(e))
            return []

    def _transpose_weather_data(self, data: List[Dict[str, Any]], provider: ProviderEntity) -> List[Dict[str, Any]]:
        """
        Transpose weather data from array format to record format and apply field mappings
        
        For weather APIs that return data like:
        {
            "cloudCover": [10, 20, 30],
            "temperature": [25, 26, 27]
        }
        
        Transform to:
        [
            {"cloud_cover": 10, "temperature": 25},
            {"cloud_cover": 20, "temperature": 26},
            {"cloud_cover": 30, "temperature": 27}
        ]
        
        Also applies field mappings from camelCase to snake_case based on provider metadata.
        
        Args:
            data: Original data from API
            provider: Provider entity for context
            
        Returns:
            List[Dict[str, Any]]: Transposed data records with field mappings applied
        """
        try:
            if not data:
                return []
            
            # Get field mappings from provider mapping array (API camelCase -> schema snake_case)
            field_mappings = {}
            if provider.mapping:
                # Convert mapping array to dictionary: {api_field: schema_field}
                field_mappings = {mapping.provider_field: mapping.serve_field for mapping in provider.mapping}
                logger.info("Using field mappings for provider %s: %d mappings", provider.code, len(field_mappings))
            
            # Handle case where data is already in the correct format
            if isinstance(data, list) and all(isinstance(item, dict) for item in data):
                # Check if any item has array values that need transposing
                first_item = data[0]
                has_arrays = any(isinstance(value, list) for value in first_item.values())
                
                if not has_arrays:
                    logger.info("Weather data for provider %s is already in record format", provider.code)
                    # Still apply field mappings even if no transposition needed
                    return self._apply_field_mappings(data, field_mappings, provider)
            
            transposed_records = []
            
            # Process each data item (could be multiple API responses)
            for item in data if isinstance(data, list) else [data]:
                if not isinstance(item, dict):
                    continue
                
                # Find arrays in the data
                array_fields = {}
                scalar_fields = {}
                
                for key, value in item.items():
                    if isinstance(value, list):
                        array_fields[key] = value
                    else:
                        scalar_fields[key] = value
                
                if not array_fields:
                    # No arrays to transpose, add as single record with field mapping
                    mapped_item = self._apply_field_mappings([item], field_mappings, provider)[0]
                    transposed_records.append(mapped_item)
                    continue
                
                # Get the length of arrays (should be the same for all)
                array_lengths = [len(arr) for arr in array_fields.values()]
                if not array_lengths:
                    continue
                
                max_length = max(array_lengths)
                if len(set(array_lengths)) > 1:
                    logger.warning("Weather data arrays have different lengths for provider %s: %s", 
                                 provider.code, array_lengths)
                
                # Create records by combining array elements at each index
                for i in range(max_length):
                    record = scalar_fields.copy()  # Add scalar fields to each record
                    
                    for field_name, field_array in array_fields.items():
                        if i < len(field_array):
                            record[field_name] = field_array[i]
                        else:
                            record[field_name] = None  # Handle arrays of different lengths
                    
                    transposed_records.append(record)
            
            # Apply field mappings to all transposed records
            if field_mappings:
                transposed_records = self._apply_field_mappings(transposed_records, field_mappings, provider)
            
            logger.info("Transposed weather data for provider %s: %d records created", 
                       provider.code, len(transposed_records))
            return transposed_records
            
        except Exception as e:
            logger.error("Error transposing weather data for provider %s: %s", provider.code, str(e))
            return data  # Return original data if transposition fails

    def _apply_field_mappings(self, records: List[Dict[str, Any]], field_mappings: Dict[str, str], provider: ProviderEntity) -> List[Dict[str, Any]]:
        """
        Apply field mappings to convert camelCase API fields to snake_case database fields
        
        Args:
            records: List of records to apply mappings to
            field_mappings: Dictionary mapping camelCase API fields -> snake_case schema fields
            provider: Provider entity for context
            
        Returns:
            List[Dict[str, Any]]: Records with field names mapped
        """
        try:
            if not field_mappings:
                return records
            
            # field_mappings is now {camelCase: snake_case} from provider.mapping
            mapped_records = []
            for record in records:
                mapped_record = {}
                
                for api_field, value in record.items():
                    # Check if this field has a mapping
                    if api_field in field_mappings:
                        db_field = field_mappings[api_field]
                        mapped_record[db_field] = value
                    else:
                        # Keep original field name if no mapping found
                        mapped_record[api_field] = value
                
                mapped_records.append(mapped_record)
            
            logger.info("Applied field mappings for provider %s: %d records processed", 
                       provider.code, len(mapped_records))
            return mapped_records
            
        except Exception as e:
            logger.error("Error applying field mappings for provider %s: %s", provider.code, str(e))
            return records  # Return original records if mapping fails
