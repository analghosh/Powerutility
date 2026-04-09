"""
Scrape Raw Data Processor
Handles the processing of scraped raw data into raw data entities
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from libs.audit_queue.simple_logger import AuditLogger
from libs.messaging.job_publisher import JobPublisher
from libs.models.provider_models import DataType, IngestionType, ProviderEntity, ProviderSummary
from services.ingestion.app.core.config import settings
from services.ingestion.app.processors.base_raw_data_processor import BaseRawDataProcessor
from libs.models.integration_apilog_entity import IntegrationApiLogEntity
from services.ingestion.app.repositories.integration_apilog_repository import IntegrationApiLogRepository

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ScrapeRawDataProcessor(BaseRawDataProcessor):
    """
    Service for managing scrape raw data processing tasks with job detail recording
    """

    def __init__(self, audit_logger: AuditLogger):
        """Initialize scrape raw data processor"""
        super().__init__()
        self.audit_logger = audit_logger
        self.job_publisher = JobPublisher()
        self.http_timeout = settings.SCRAPER_TIMEOUT
        self.retry_attempts = settings.SCRAPER_RETRY_ATTEMPTS
        self.retry_delay = settings.SCRAPER_RETRY_DELAY
        self.integration_apilog_repository = IntegrationApiLogRepository(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE
        )

    async def execute_cron_job(self) -> Dict[str, Any]:
        """
        Execute scrape raw data processing as a cron job (implementation of abstract method)
        """
        return await self.process_scraped_data(IngestionType.SCRAPE, DataType.EXCHANGE)

    async def process_scraped_data(self, ingestion_type: IngestionType, data_type: DataType) -> Dict[str, Any]:
        """
        Execute scrape raw data processing as a cron job with job detail recording
        This method is called by the cron manager to process scrape raw data

        Returns:
            Dict[str, Any]: Processing result with statistics
        """
        job_type = "scrape_raw_data_processor"
        job_name = "Scrape Raw Data Processor"
        schedule = "*/15 * * * *"  # Every 15 minutes
        start_time = datetime.now()

        # Record job start
        execution_id = await self.record_job_start(job_type, job_name, schedule, start_time)

        # Log job start with both audit and regular logging
        logger.info("🚀 Starting scrape raw data processing cron job at %s", start_time.strftime('%Y-%m-%d %H:%M:%S'))
        
        if execution_id:
            self.audit_logger.log_system_operation(
                service="ingestion",
                operation="cron_job_started",
                message=f"Cron job started: {job_name}",
                correlation_id=execution_id,
                job_name=job_name,
                job_type=job_type,
                schedule=schedule,
                execution_mode="scheduled"
            )

        try:
            # Get providers information for ingestion type SCRAPE
            providers = await self._get_provider_details(ingestion_type, data_type)

            if not providers:
                end_time = datetime.now()
                execution_time = (end_time - start_time).total_seconds()
                
                logger.info("ℹ️ No SCRAPE providers found for processing (completed in %.2fs)", execution_time)
                
                result = {
                    'status': 'success',
                    'processed_providers': 0,
                    'execution_time': execution_time
                }

                # Log job completion
                if execution_id:
                    self.audit_logger.log_system_operation(
                        service="ingestion",
                        operation="cron_job_completed",
                        message=f"Cron job completed: {job_name} - No providers found",
                        correlation_id=execution_id,
                        job_name=job_name,
                        job_type=job_type,
                        execution_mode="scheduled",
                        duration_ms=int(execution_time * 1000),
                        processed_providers=0,
                        success=True
                    )

                    # Record job completion in job detail system
                    await self.record_job_completion(
                        job_type, job_name, schedule, start_time, end_time, execution_id,
                        "success", True, execution_id, result
                    )

                return result

            logger.info("📋 Found %d providers for ingestion type SCRAPE", len(providers))

            processed_providers = 0
            failed_providers = 0

            # Process data for each provider and their data types
            for provider in providers:
                try:
                    provider_code = provider.code
                    data_type = provider.data_type 

                    logger.info("🔄 Processing provider: %s, data type: %s", provider_code, data_type)

                    # Log provider processing start
                    if execution_id:
                        self.audit_logger.log_system_operation(
                            service="ingestion",
                            operation="provider_scrape_started",
                            message=f"Starting scrape processing for provider: {provider_code}",
                            correlation_id=execution_id,
                            provider_code=provider_code,
                            data_type=data_type
                        )

                    # Process scraped data for this provider and data type
                    result = await self._process_provider_data(provider)

                    if result and result.get('success', False):
                        scraped_records = result.get('scraped_records', 0)
                        stored_records = result.get('stored_records', 0)
                        
                        logger.info("✅ Successfully processed provider: %s | Scraped: %d, Stored: %d records", 
                                   provider_code, scraped_records, stored_records)
                        
                        # Log successful provider processing
                        if execution_id:
                            self.audit_logger.log_system_operation(
                                service="ingestion",
                                operation="provider_scrape_completed",
                                message=f"Successfully processed provider: {provider_code}",
                                correlation_id=execution_id,
                                provider_code=provider_code,
                                data_type=data_type,
                                scraped_records=scraped_records,
                                stored_records=stored_records,
                                success=True
                            )
                        
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
                                "record_count": stored_records
                            }
                        )
                    else:
                        error_msg = result.get('error', 'Unknown error') if result else 'No result returned'
                        logger.error("❌ Failed to process provider: %s | Error: %s", provider_code, error_msg)
                        
                        # Log failed provider processing
                        if execution_id:
                            self.audit_logger.log_system_operation(
                                service="ingestion",
                                operation="provider_scrape_failed",
                                message=f"Provider scrape failed: {error_msg}",
                                correlation_id=execution_id,
                                provider_code=provider_code,
                                data_type=data_type,
                                success=False
                            )

                    processed_providers += 1

                except (ValueError, OSError, RuntimeError) as provider_error:
                    failed_providers += 1
                    provider_code = getattr(provider, 'code', 'unknown')
                    logger.error("💥 Exception processing provider %s: %s (%s)", 
                               provider_code, str(provider_error), type(provider_error).__name__)
                    
                    # Log provider processing exception
                    if execution_id:
                        self.audit_logger.log_system_operation(
                            service="ingestion",
                            operation="provider_scrape_error",
                            message=f"Provider scrape error: {str(provider_error)}",
                            correlation_id=execution_id,
                            provider_code=provider_code,
                            exception_type=type(provider_error).__name__,
                            success=False
                        )

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

            # Log job completion with queue-based audit
            if execution_id:
                error_message = None if overall_success else f"Processed {processed_providers}, failed {failed_providers}"
                
                self.audit_logger.log_system_operation(
                    service="ingestion",
                    operation="cron_job_completed",
                    message=f"Cron job completed: {job_name} - {processed_providers} processed, {failed_providers} failed",
                    correlation_id=execution_id,
                    job_name=job_name,
                    job_type=job_type,
                    execution_mode="scheduled",
                    duration_ms=int(execution_time * 1000),
                    processed_providers=processed_providers,
                    failed_providers=failed_providers,
                    total_providers=len(providers),
                    success=overall_success
                )

                # Record job completion in job detail system
                await self.record_job_completion(
                    job_type, job_name, schedule, start_time, end_time, execution_id,
                    status, overall_success, execution_id, result, error_message
                )

            logger.info("🏁 Scrape raw data processing completed in %.2fs | Processed: %d, Failed: %d, Total: %d | ID: %s", 
                       execution_time, processed_providers, failed_providers, len(providers), execution_id)

            return result

        except (ValueError, OSError, RuntimeError) as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            logger.error("💥 Scrape raw data processing failed after %.2fs: %s (%s)", 
                        execution_time, str(e), type(e).__name__)

            result = {'status': 'error', 'error': str(e), 'execution_time': execution_time}

            # Log job error
            if execution_id:
                self.audit_logger.log_system_operation(
                    service="ingestion",
                    operation="cron_job_failed",
                    message=f"Cron job failed: {job_name} - {str(e)}",
                    correlation_id=execution_id,
                    job_name=job_name,
                    job_type=job_type,
                    execution_mode="scheduled",
                    duration_ms=int(execution_time * 1000),
                    exception_type=type(e).__name__,
                    success=False
                )

                # Record job completion with error in job detail system
                await self.record_job_completion(
                    job_type, job_name, schedule, start_time, end_time, execution_id,
                    "error", False, execution_id, result, str(e)
                )

            return result

    async def _process_provider_data(self, provider: ProviderSummary) -> Dict[str, Any]:
        """
        Process scraped data for a single provider
        
        Args:
            provider: Provider entity to process
            
        Returns:
            Dict[str, Any]: Processing result
        """
        try:
            provider_code = provider.code
            data_type = provider.data_type
            
            logger.debug("🔍 Processing provider: %s, data type: %s", provider_code, data_type)
            
            # Get full provider entity for processing
            full_provider = await self._get_full_provider_entity(provider_code)
            if not full_provider:
                logger.error("❌ Could not retrieve full provider entity for: %s", provider_code)
                return {'success': False, 'error': 'Provider entity not found'}
            
            # Extract URLs from provider metadata
            urls = self._extract_urls_from_provider(full_provider)
            if not urls:
                logger.warning("⚠️ No URLs found for provider: %s", provider_code)
                return {'success': False, 'error': 'No URLs found in provider metadata'}
            
            all_scraped_data = []
            successful_urls = 0
            failed_urls = 0
            
            # Scrape data from each URL
            all_log_ids = []
            
            for url in urls:
                try:
                    scrape_result = await self._scrape_url_data(url, full_provider)
                    
                    scraped_data = scrape_result.get("data", [])
                    log_id = scrape_result.get("log_id")
                    
                    if scraped_data:
                        all_scraped_data.extend(scraped_data)
                        successful_urls += 1
                        if log_id:
                            all_log_ids.append(log_id)
                        logger.debug("✅ Scraped %d records from URL: %s", len(scraped_data), url)
                    else:
                        failed_urls += 1
                        logger.warning("⚠️ No data scraped from URL: %s", url)
                        if log_id: # Even if parsing failed, we might have a log (if response was received)
                            all_log_ids.append(log_id)
                        
                except Exception as url_error:
                    failed_urls += 1
                    logger.error("❌ Error scraping URL %s: %s (%s)", url, str(url_error), type(url_error).__name__)
            
            # Check if any data or logs were collected
            if not all_scraped_data and not all_log_ids:
                logger.warning("⚠️ No data scraped and no logs generated for provider: %s", provider_code) 
                return {'success': False, 'error': 'No data scraped from any URL'}
            
            if not all_scraped_data:
                logger.warning("⚠️ No data scraped for provider: %s (URLs: %d successful, %d failed)", 
                             provider_code, successful_urls, failed_urls)
                return {'success': False, 'error': 'No data scraped from any URL'}
            
            # Validate and map data using base class methods
            validation_result = self.validate_data_schema(all_scraped_data, full_provider)
            if not validation_result["success"]:
                error_summary = "; ".join(validation_result["errors"][:3])
                logger.error("❌ Data validation failed for provider %s: %s", provider_code, error_summary)
                return {'success': False, 'error': 'Data validation failed', 'validation_errors': validation_result["errors"]}
            
            # Map data to schema
            mapped_data = self.map_data_to_schema(validation_result["validated_data"], full_provider)
            
            # Combine log IDs from all successful scrapes
            # Remove duplicates if any
            unique_log_ids = list(set(all_log_ids))
            
            # Store data using raw data service
            storage_result = await self._store_scraped_data(mapped_data, full_provider, unique_log_ids)
            
            if storage_result["success"]:
                stored_records = storage_result.get("stored_records", 0)
                logger.debug("✅ Provider %s processed: %d records stored", provider_code, stored_records)
                return {
                    'success': True,
                    'provider_code': provider_code,
                    'data_type': data_type,
                    'scraped_records': len(all_scraped_data),
                    'validated_records': validation_result["validated_records"],
                    'stored_records': stored_records,
                    'successful_urls': successful_urls,
                    'failed_urls': failed_urls
                }
            else:
                error_msg = storage_result.get("error", "Unknown storage error")
                logger.error("❌ Failed to store data for provider %s: %s", provider_code, error_msg)
                return {'success': False, 'error': error_msg}
                
        except Exception as e:
            logger.error("💥 Unexpected error processing provider %s: %s (%s)", 
                        provider.code, str(e), type(e).__name__)
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
            logger.error("Error retrieving provider entity for code %s: %s (%s)",
                        provider_code, str(e), type(e).__name__)
            return None

    async def _store_scraped_data(self, data: List[Dict[str, Any]], provider: ProviderEntity, log_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Store scraped data using the raw data service
        
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
            logger.error("💥 Error storing scraped data: %s (%s)", str(e), type(e).__name__)
            return {'success': False, 'error': str(e)}

    def _extract_urls_from_provider(self, provider: ProviderEntity) -> List[str]:
        """
        Extract URLs from provider metadata

        Args:
            provider: Provider entity containing metadata

        Returns:
            List[str]: List of URLs to scrape
        """
        try:
            metadata = provider.metadata or {}
            urls = []

            if not isinstance(metadata, dict):
                logger.warning("⚠️ Provider %s metadata is not a dictionary", provider.code)
                return []
            
            if 'url' in metadata and isinstance(metadata['url'], str):
                urls.append(metadata['url'].strip())

            # Remove duplicates and empty strings
            urls = list(set(filter(None, urls)))

            logger.debug("🔗 Extracted %d URLs from provider %s metadata", len(urls), provider.code)
            return urls

        except (ValueError, TypeError, AttributeError) as e:
            logger.error("💥 Error extracting URLs from provider %s metadata: %s (%s)", 
                        provider.code, str(e), type(e).__name__)
            return []

    async def _scrape_url_data(self, url: str, provider: ProviderEntity) -> Dict[str, Any]:
        """
        Scrape data from a specific URL using data_scrapper functionality

        Args:
            url: URL to scrape
            provider: Provider entity for configuration

        Returns:
            Dict[str, Any]: Result containing 'data' (List) and 'log_id' (Optional[str])
        """
        try:
            # Get scraping configuration from provider metadata
            metadata = provider.metadata or {}
            config = metadata.copy() # Start with all metadata including parser_type
            
            # Update with defaults/overrides
            config.update({
                'timeout': metadata.get('timeout', self.http_timeout),
                'retry_attempts': metadata.get('retry_attempts', self.retry_attempts),
                'retry_delay': metadata.get('retry_delay', self.retry_delay),
                'fallback_url': metadata.get('fallback_url'),
                'headers': metadata.get('headers', {}),
                'params': metadata.get('params', {}),
                'provider_id': str(provider.id) if provider.id else provider.code, # Add provider ID for logging
                'data_type': provider.data_type
            })

            # Use data_scrapper's scraping method
            scrape_result = await self._try_scrape_single_url(url, config, datetime.now(), "primary")

            if scrape_result.get("status") == "success":
                return {
                    "data": scrape_result.get("data", []),
                    "log_id": scrape_result.get("log_id")
                }
            else:
                error_msg = scrape_result.get("error", "Unknown error")
                logger.warning("⚠️ Scraping failed for URL %s: %s", url, error_msg)
                return {
                    "data": [],
                    "log_id": scrape_result.get("log_id") # Return log ID even if processing failed, so we can track it
                }

        except (ValueError, TypeError, OSError, RuntimeError) as e:
            logger.error("💥 Unexpected error scraping URL %s: %s (%s)", url, str(e), type(e).__name__)
            return []

    async def _try_scrape_single_url(
        self, url: str, config: Dict[str, Any], start_time: datetime, url_type: str
    ) -> Dict[str, Any]:
        """Try to scrape a single URL with retries using data_scrapper logic"""
        try:
            timeout = config.get('timeout', self.http_timeout)
            retry_attempts = config.get('retry_attempts', self.retry_attempts)
            retry_delay = config.get('retry_delay', self.retry_delay)

            log_id = None
            for attempt in range(retry_attempts):
                try:
                    logger.debug(
                        "🌐 Scraping %s URL: %s (attempt %d/%d)",
                        url_type,
                        url,
                        attempt + 1,
                        retry_attempts,
                    )

                    # Prepare headers
                    headers = config.get('headers', {})
                    default_headers = {
                        'User-Agent': 'EPMS-Scraper/1.0',
                        'Accept': 'application/json, text/html, */*'
                    }
                    headers.update(default_headers)

                    # Make request
                    response = requests.get(
                        url,
                        headers=headers,
                        params=config.get('params', {}),
                        timeout=timeout
                    )
                    
                    # Blind Log the response

                    # Blind Log the response
                    try:
                        provider_id = config.get('provider_id', 'unknown')
                        log_entity = IntegrationApiLogEntity(
                            provider_id=str(provider_id),
                            raw_data=response,  # Store full text
                            timestamp=datetime.now(),
                            metadata={
                                "url": url,
                                "status_code": response.status_code,
                                "attempt": attempt + 1
                            }
                        )
                        data_type_log = config.get('data_type', 'unknown')
                        # Ensure we use the value if it's an enum, otherwise string
                        data_type_str = data_type_log.value if hasattr(data_type_log, 'value') else str(data_type_log)
                        log_id = self.integration_apilog_repository.save_log(data_type_str.lower(), log_entity)
                    except Exception as log_error:
                        logger.error("Failed to integration log: %s", log_error)
                        log_id = None

                    response.raise_for_status()

                    # Parse content based on type
                    content_type = response.headers.get('content-type', '').lower()

                    if 'application/json' in content_type:
                        data = response.json()
                        table_data = self._process_json_response(data, url)
                    elif 'text/html' in content_type:
                        # Use data_scrapper's HTML parsing logic
                        soup = BeautifulSoup(response.text, "html.parser")
                        table_data = self._extract_table_data(soup, config)
                    else:
                        # Try to parse as JSON anyway
                        try:
                            data = response.json()
                            table_data = self._process_json_response(data, url)
                        except json.JSONDecodeError:
                            logger.warning("⚠️ Could not parse response from URL: %s as JSON", url)
                            table_data = []

                    if table_data:
                        logger.debug(
                            "✅ Successfully scraped %d records from %s URL: %s",
                            len(table_data),
                            url_type,
                            url,
                        )
                        return {
                            "status": "success",
                            "data": table_data,
                            "scraped_at": start_time,
                            "response_time_ms": (datetime.now() - start_time).total_seconds() * 1000,
                            "url": url,
                            "url_type": url_type,
                            "config": config,
                            "data_type": config.get("data_type", "unknown"),
                            "source": config.get("source", "unknown"),
                            "config_id": config.get("config_id", "scrape_processor"),
                            "log_id": log_id
                        }
                    else:
                        logger.warning("⚠️ No data found at %s URL: %s", url_type, url)
                        return {
                            "status": "no_data",
                            "data": [],
                            "scraped_at": start_time,
                            "url": url,
                            "url_type": url_type,
                            "config_id": config.get("config_id", "scrape_processor"),
                            "data_type": config.get("data_type", "unknown"),
                            "source": config.get("source", "unknown"),
                            "error": f"No data found at {url_type} URL",
                        }

                except (requests.RequestException, requests.HTTPError) as e:
                    logger.warning(
                        "⚠️ Error on %s URL attempt %d: %s (%s)",
                        url_type,
                        attempt + 1,
                        str(e),
                        type(e).__name__
                    )
                    if attempt < retry_attempts - 1:
                        await asyncio.sleep(retry_delay)
                    else:
                        return {
                            "status": "error",
                            "data": [],
                            "scraped_at": start_time,
                            "url": url,
                            "url_type": url_type,
                            "config_id": config.get("config_id", "scrape_processor"),
                            "data_type": config.get("data_type", "unknown"),
                            "source": config.get("source", "unknown"),
                            "error": f"Failed after {retry_attempts} attempts: {str(e)}",
                            "log_id": log_id
                        }

            # If we reach here, all attempts failed
            return {
                "status": "error",
                "data": [],
                "scraped_at": start_time,
                "url": url,
                "url_type": url_type,
                "config_id": config.get("config_id", "scrape_processor"),
                "data_type": config.get("data_type", "unknown"),
                "source": config.get("source", "unknown"),
                "data_type": config.get("data_type", "unknown"),
                "source": config.get("source", "unknown"),
                "error": f"Failed after {retry_attempts} attempts: Unknown error",
                "log_id": log_id
            }

        except (ValueError, TypeError, OSError, RuntimeError) as e:
            logger.error("💥 Failed to scrape %s URL %s: %s (%s)", url_type, url, str(e), type(e).__name__)
            return {
                "status": "error",
                "data": [],
                "scraped_at": start_time,
                "url": url,
                "url_type": url_type,
                "config_id": config.get("config_id", "scrape_processor"),
                "data_type": config.get("data_type", "unknown"),
                "source": config.get("source", "unknown"),
                "source": config.get("source", "unknown"),
                "error": str(e),
                "log_id": log_id if 'log_id' in locals() else None
            }



    def _extract_table_data(self, soup: BeautifulSoup, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract table data from HTML using data_scrapper logic"""
        
        # Check for configured parser
        parser_type = config.get("parser_type")
        if parser_type:
             try:
                 from libs.parsers.parser_factory import create_parser
                 # Create a dummy provider entity with the config's data_type/source/metadata
                 # or reconstruct mapped dict if needed. The create_parser takes an Optional[ProviderEntity]
                 # But we also allow passing parser_type directly.
                 # The parser.parse expects content string.
                 
                 parser = create_parser(parser_type=parser_type)
                 # Pass soup directly in kwargs if parser supports it (IEX parser does)
                 # Converting soup back to string is inefficient, but BaseParser contract is string.
                 # However, our IEX parser checks kwargs for 'soup'.
                 
                 # Construct minimal provider entity for context if needed, or pass None
                 # config has 'data_type', 'source', etc.
                 # Let's pass None for provider entity for now, as IEX parser uses metadata passed in config mainly?
                 # Actually IEX parser looks at provider.data_type. 
                 # We should pass a synthetic provider object or update the parser to take kwargs.
                 # For now, let's stick to the simplest path: 
                 # passing None but relying on the parser to extract what it needs or just use defaults.
                 # Wait, IEXParser uses provider.data_type...
                 # We can create a lightweight namedtuple or object mocking ProviderEntity or just update config to pass what's needed in kwargs?
                 # Better: create a partial ProviderEntity
                 
                 from libs.models.provider_models import ProviderEntity
                 # Create a complete dummy provider entity to satisfy Pydantic validation
                 provider_context = ProviderEntity(
                     name="Context Provider", # Dummy name
                     code=config.get('provider_code', 'unknown'),
                     ingestion_type="scrape", # Dummy ingestion type
                     data_type=config.get('data_type', 'exchange'),
                     metadata=config,
                     fields=[], # Empty fields list
                     mapping=[],
                     enabled=True
                 )
                 
                 return parser.parse(str(soup), provider=provider_context, soup=soup)
                 
             except Exception as e:
                 logger.error("Failed to use configured parser '%s': %s", parser_type, str(e))
                 # Fallback to generic logic
        
        try:
            # Find all tables in the HTML
            # Strategy 1: Look for specific aria-label "sticky table" (IEX specific)
            table = soup.find("table", attrs={"aria-label": "sticky table"})
            
            # Strategy 2: If not found, look for ANY table with class "MuiTable-root" (Material UI)
            if not table:
                table = soup.find("table", class_=lambda c: c and "MuiTable-root" in c)
                
            # Strategy 3: Just take the first table found
            if not table:
                tables = soup.find_all("table")
                if tables:
                    table = tables[0]
                    if len(tables) > 1:
                        logger.warning("⚠️ Multiple tables found, selecting the first one. (Total: %d)", len(tables))

            if not table:
                logger.warning("⚠️ No table found with aria-label='sticky table' or generic table tag")
                # Debug: log part of HTML to see what's wrong
                # logger.debug("HTML Content Start: %s", soup.prettify()[:500])
                return []

            all_extracted_data = []
            try:
                # Extract headers
                headers = self._extract_headers(table, config)
                if not headers:
                    logger.warning("⚠️ No headers found in sticky table")
                    return []

                # Extract rows
                rows = self._extract_rows(table, config)
                if not rows:
                    logger.warning("⚠️ No data rows found in sticky table")
                    return []

                logger.debug("📊 Sticky-table: Found %d headers and %d rows", len(headers), len(rows))

                # Process table data
                table_data = self._process_table_data(headers, rows, config)
                all_extracted_data.extend(table_data)

            except (ValueError, TypeError, AttributeError, OSError) as e:
                logger.error("💥 Error extracting table data: %s (%s)", str(e), type(e).__name__)
                return []

            logger.debug("📊 Total extracted data: %d items", len(all_extracted_data))
            return all_extracted_data

        except (ValueError, TypeError, AttributeError, OSError) as e:
            logger.error("💥 Error extracting table data: %s (%s)", str(e), type(e).__name__)
            return []

    def _extract_headers(self, table, config: Dict[str, Any]) -> List[str]:  # pylint: disable=unused-argument
        """Extract table headers"""
        try:
            headers = []
            # Try to find headers in thead first
            thead = table.find("thead")
            if thead:
                # Handle multi-row headers: flatten them or just take the last row
                # For simplified scraping, we usually want the last row of headers as it contains the most specific column names
                # Or, we might want to concatenate them. Let's try to get all unique text from the cell hierarchy if possible.
                # Simplest robust approach: Find all 'th' in the thead. 
                # If there are multiple rows, this might duplicate headers or be messy.
                # Let's target the last 'tr' in thead which usually has the column names.
                rows = thead.find_all("tr")
                if rows:
                    # Strategy: Use the last row for direct headers
                    header_row = rows[-1]
                    headers = [
                        th.get_text(strip=True) for th in header_row.find_all(["th", "td"])
                    ]
                    
            # Fallback or if invalid headers found in thead
            if not headers:
                # Fallback: use first row of the table (tbody or direct tr)
                first_row = table.find("tr")
                if first_row:
                    headers = [
                        cell.get_text(strip=True)
                        for cell in first_row.find_all(["th", "td"])
                    ]

            # Filter out empty headers but keep index alignment
            # Note: We should NOT filter out empty string headers if they exist in the DOM, 
            # because that shifts the index alignment for data rows.
            # Instead, we should perhaps name them "Unknown_X". 
            # But the previous logic filtered them: return [h for h in headers if h]
            # This might be why columns are missing if the header cell was empty text!
            # Let's keep empty headers as empty strings to maintain column alignment.
            
            cleaned_headers = [h if h else f"Column_{i}" for i, h in enumerate(headers)]
            logger.info("🔍 Extracted Headers for %s: %s", config.get('data_type', 'unknown'), cleaned_headers)
            
            try:
                with open("d:\\EPMS-Backend\\headers_debug.txt", "w", encoding='utf-8') as f:
                    f.write(f"Headers: {cleaned_headers}\n")
            except Exception as e:
                logger.error("Failed to write headers debug file: %s", e)
                
            return cleaned_headers

        except (ValueError, TypeError, AttributeError, OSError) as e:
            logger.error("💥 Error extracting headers: %s (%s)", str(e), type(e).__name__)
            return []

    def _extract_rows(self, table, config: Dict[str, Any]) -> List[List[str]]:  # pylint: disable=unused-argument
        """Extract table rows"""
        try:
            rows = []
            tbody = table.find("tbody")
            table_body = tbody if tbody else table

            # Track cells that need to be filled due to rowspan
            span_map = {}

            for row_idx, row in enumerate(table_body.find_all("tr")):
                cells = []
                col_idx = 0
                for cell in row.find_all(["td", "th"]):
                    # Fill cells from previous rowspans
                    while span_map.get(col_idx, 0):
                        cells.append(span_map[col_idx][0])
                        span_map[col_idx][1] -= 1
                        if span_map[col_idx][1] == 0:
                            del span_map[col_idx]
                        col_idx += 1

                    cell_text = cell.get_text(strip=True)
                    rowspan = int(cell.get("rowspan", 1))
                    colspan = int(cell.get("colspan", 1))

                    # Add cell value for colspan
                    for i in range(colspan):
                        cells.append(cell_text)
                        # Handle rowspan for this column
                        if rowspan > 1:
                            span_map[col_idx] = [cell_text, rowspan - 1]
                        col_idx += 1

                # Fill remaining cells from previous rowspans
                while span_map.get(col_idx, 0):
                    cells.append(span_map[col_idx][0])
                    span_map[col_idx][1] -= 1
                    if span_map[col_idx][1] == 0:
                        del span_map[col_idx]
                    col_idx += 1

                if cells and any(cell for cell in cells):  # Skip empty rows
                    rows.append(cells)

            return rows

        except (ValueError, TypeError, AttributeError, OSError) as e:
            logger.error("💥 Error extracting rows: %s (%s)", str(e), type(e).__name__)
            return []

    def _process_table_data(
        self, headers: List[str], rows: List[List[str]], config: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Process table data into structured format"""
        try:
            processed_data = []
            current_time = datetime.now()

            # Process each row
            for row in rows:
                # Create row data
                row_data = {}
                for i, header in enumerate(headers):
                    if i < len(row):
                        row_data[header] = row[i].strip()
                    else:
                        row_data[header] = ""  # Default empty value

                # Add metadata
                row_data["scraped_at"] = current_time
                row_data["data_type"] = config.get("data_type", "unknown")
                row_data["source"] = config.get("source", "unknown")
                row_data["createdAt"] = current_time
                row_data["updatedAt"] = current_time
                processed_data.append(row_data)

            logger.info("Processed %d data rows", len(processed_data))
            return processed_data

        except (ValueError, TypeError, AttributeError, OSError) as e:
            logger.error("Error processing table data: %s", str(e))
            return []

    def _process_json_response(self, data: Any, url: str) -> List[Dict[str, Any]]:
        """
        Process JSON response data into structured format
        
        Args:
            data: JSON response data
            url: Source URL for logging
            
        Returns:
            List[Dict[str, Any]]: Processed data records
        """
        try:
            processed_data = []
            current_time = datetime.now()
            
            # Handle different JSON structures
            if isinstance(data, list):
                # Direct list of records
                for item in data:
                    if isinstance(item, dict):
                        item["scraped_at"] = current_time
                        item["source_url"] = url
                        processed_data.append(item)
            elif isinstance(data, dict):
                # Check for common data container keys
                for key in ['data', 'results', 'items', 'records']:
                    if key in data and isinstance(data[key], list):
                        for item in data[key]:
                            if isinstance(item, dict):
                                item["scraped_at"] = current_time
                                item["source_url"] = url
                                processed_data.append(item)
                        break
                else:
                    # Single record
                    data["scraped_at"] = current_time
                    data["source_url"] = url
                    processed_data.append(data)
            
            logger.info("Processed %d JSON records from URL: %s", len(processed_data), url)
            return processed_data
            
        except Exception as e:
            logger.error("Error processing JSON response from URL %s: %s", url, str(e))
            return []

    def _process_json_response(self, data: Any, url: str) -> List[Dict[str, Any]]:
        """Process JSON response data"""
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            # Look for common data keys
            for key in ['data', 'results', 'items', 'records']:
                if key in data and isinstance(data[key], list):
                    return data[key]
            # Return the dict as a single record
            return [data]
        else:
            logger.warning("Unexpected JSON structure from URL: %s", url)
            return []

    async def _get_provider_details(self, ingestion_type: IngestionType, data_type: DataType) -> Optional[List[ProviderSummary] | None]:
        """
        Get complete provider details for validation and storage

        Args:
            ingestion_type: Ingestion type code
            data_type: Data type (demand, supply, etc.) - Optional, if None gets all data types

        Returns:
            Optional[List[ProviderSummary]] | None: Complete provider objects or None if not found
        """
        try:
            # Get provider details - if data_type is None, get all enabled FTP providers
            if data_type:
                providerResponse = await self.provider_service.get_all_providers(
                    ingestion_type=ingestion_type, data_type=data_type, enabled=True
                )
            else:
                # Get all enabled SCRAPE providers regardless of data type
                providerResponse = await self.provider_service.get_all_providers(
                    ingestion_type=ingestion_type, enabled=True
                )

            if not providerResponse:
                logger.error("No providers found for ingestion_type: %s", ingestion_type)
                return None

            if not providerResponse or (hasattr(providerResponse, 'providers') and not providerResponse.providers):
                logger.error("No provider exist for ingestion_type: %s", ingestion_type)
                return None

            return providerResponse.providers

        except (ValueError, TypeError, OSError, RuntimeError) as e:
            logger.error("Error getting provider details for %s: %s", ingestion_type, e)
            return None

    def map_data_to_schema(self, data: List[Dict[str, Any]], provider: ProviderEntity) -> List[Dict[str, Any]]:
        """
        Map raw data to provider schema format using proper mapping field
        
        Args:
            data: Raw data records
            provider: Provider entity containing schema and mapping configuration
            
        Returns:
            List[Dict[str, Any]]: Mapped data records
        """
        try:
            if not provider.fields:
                logger.warning("No schema defined for provider %s, returning data as-is", provider.code)
                return data

            # Create mapping dictionary from provider.mapping field
            field_mappings = {}
            if provider.mapping:
                for mapping_obj in provider.mapping:
                    field_mappings[mapping_obj.serve_field] = mapping_obj.provider_field
            
            mapped_data = []
            for record in data:
                mapped_record = {}
                
                for field in provider.fields:
                    field_name = field.field_name
                    
                    # Check if there's a mapping for this field
                    if field_name in field_mappings:
                        source_field = field_mappings[field_name]
                        if source_field in record:
                            mapped_record[field_name] = record[source_field]
                        else:
                            # Try case-insensitive match
                            source_field_lower = source_field.lower()
                            for key, value in record.items():
                                if key.lower() == source_field_lower:
                                    mapped_record[field_name] = value
                                    break
                            else:
                                if field.is_required:
                                    logger.warning("Required field '%s' not found in data (mapped from '%s')", field_name, source_field)
                    else:
                        # Direct field name match
                        if field_name in record:
                            mapped_record[field_name] = record[field_name]
                        else:
                            # Try case-insensitive match
                            field_name_lower = field_name.lower()
                            for key, value in record.items():
                                if key.lower() == field_name_lower:
                                    mapped_record[field_name] = value
                                    break
                            else:
                                if field.is_required:
                                    logger.warning("Required field '%s' not found in data", field_name)
                
                mapped_data.append(mapped_record)

            logger.info("Mapped %d records to schema format", len(mapped_data))
            return mapped_data

        except Exception as e:
            logger.error("Error mapping data to schema: %s", str(e))
            return data  # Return original data if mapping fails
