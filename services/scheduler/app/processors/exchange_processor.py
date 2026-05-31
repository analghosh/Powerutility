"""
Exchange data processor for handling exchange data processing logic.
"""

import logging
import uuid
from datetime import datetime, timedelta, date
from typing import Dict, List, Any, Optional, Tuple
from enum import Enum

from .base_processor import BaseProcessor
from libs.models.exchange_serve_entity import ExchangeServeEntity, ExchangeHistoricalEntity
from libs.enum.updated_flag import UpdatedFlag
from ..repository.exchange_repository import ExchangeRepository
from libs.core.job_entities import JobDetail


logger = logging.getLogger(__name__)

# Constants for better maintainability
MIN_TIMEBLOCK = 1
MAX_TIMEBLOCK = 96
FLOAT_COMPARISON_TOLERANCE = 0.001
PRICE_COMPARISON_TOLERANCE = 0.01


class ExchangeProcessor(BaseProcessor):
    """
    Exchange data processor that handles exchange data processing and storage.
    Uses provider mapping configuration for dynamic field mapping.
    """
    
    def __init__(self):
        super().__init__()
        self.repository = ExchangeRepository()
        
        # Initialize job repositories for execution tracking
        from libs.core.job_factory import create_job_data_repository, create_job_detail_repository
        from ..core.config import settings
        
        self.job_data_repository = create_job_data_repository(
            settings.AUDIT_DB_URI,
            settings.AUDIT_DB_NAME,
            settings.JOB_DATA_COLLECTION
        )
        self.job_detail_repository = create_job_detail_repository(
            settings.AUDIT_DB_URI,
            settings.AUDIT_DB_NAME,
            settings.JOB_DETAIL_COLLECTION
        )

    async def execute_cron_job(self) -> Dict[str, Any]:
        """
        Execute exchange data processing as a cron job with execution_id as correlation ID
        """
        job_type = "exchange_data_processing"
        job_name = "Exchange Data Processing"
        schedule = "*/15 * * * *"  # Default schedule
        start_time = datetime.now()
        
        # Generate and record job start to get execution_id (correlation ID)
        execution_id = await self._record_job_execution(job_type, job_name, schedule, start_time, is_start=True)
        if not execution_id:
            logger.error("Failed to record job start for %s", job_type)
            return {
                "status": "error",
                "message": "Failed to record job start",
                "execution_time": 0.0,
                "data_type": "exchange"
            }
        
        # Set execution_id as correlation ID using base processor method
        self.set_correlation_id(execution_id)
        
        logger.info("=== EXECUTING EXCHANGE DATA PROCESSING JOB ===")
        
        try:
            # Create metadata for exchange data processing with correlation ID
            metadata = {
                "data_type": "exchange",
                "scraped_at": start_time,
                "source": "exchange_processor_cron",
                "triggered_by": "cron_job",
                "execution_id": execution_id,
                "correlation_id": execution_id
            }
            
            # Process the metadata trigger with correlation ID context
            trigger_res = await self.process_metadata_trigger(metadata)
            success = trigger_res.get("success", False)
            
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            
            # Record job completion with correlation ID
            result = {
                "status": "success" if success else "error",
                "message": "Exchange data processed successfully" if success else "Exchange data processing failed",
                "execution_time": execution_time,
                "data_type": "exchange",
                "correlation_id": execution_id,
                "cron_job":"exchange",
                "trigger_details": trigger_res
            }
            
            await self._record_job_execution(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=end_time,
                execution_id=execution_id,
                status=result["status"],
                success=success,
                result=result,
                is_start=False
            )
            
            if success:
                logger.info("Exchange data processing completed successfully in %.2fs", execution_time)
            else:
                logger.error("Exchange data processing failed after %.2fs", execution_time)
                
            return result
                
        except Exception as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            error_result = {
                "status": "error",
                "message": f"Exchange data processing error: {str(e)}",
                "execution_time": execution_time,
                "data_type": "exchange",
                "correlation_id": execution_id
            }
            
            await self._record_job_execution(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=end_time,
                execution_id=execution_id,
                status="error",
                success=False,
                result=error_result,
                error_message=str(e),
                is_start=False
            )
            
            logger.error("Error executing exchange data processing job: %s", str(e), exc_info=True)
            return error_result
        finally:
            # Clear correlation ID after job completion
            self.clear_correlation_id()
    
    async def process_raw_data_to_serve(
        self, 
        raw_data: List[Dict[str, Any]], 
        metadata: Dict[str, Any],
        provider_config: Dict[str, Any]
    ) -> bool:
        """
        Process raw data and convert to exchange serve entities for storage using Bulk Operations.
        """
        try:
            if not self._validate_raw_data(raw_data):
                return False
            
            count = len(raw_data)
            logger.info("Processing %d exchange records (Bulk Mode)", count)
            
            # Get market type and px_name from provider config or code
            market_type = self._extract_market_type(provider_config)
            px_name = self._extract_px_name(provider_config)
            
            # 1. Pre-fetch existing records to avoid N+1 queries
            lookup_keys = []
            
            for item in raw_data:
                if not isinstance(item, dict): continue
                if self._is_summary_record(item): continue

                time_block_val = item.get("Time Block") or item.get("time_block") or item.get("timeblock") or ""
                tb = await self._process_timeblock(time_block_val) # cached
                
                date_val = item.get("Date") or item.get("date") or item.get("scheduled_at")
                if isinstance(date_val, str):
                    try:
                        clean_date = date_val.strip()
                        if "-" in clean_date:
                            date_obj = datetime.strptime(clean_date, "%d-%m-%Y")
                        else:
                            date_obj = datetime.strptime(clean_date, "%d/%m/%Y")
                    except: continue
                elif isinstance(date_val, datetime):
                    date_obj = date_val
                else:
                    continue
                
                if tb and date_obj:
                    # Normalize date to start of day for query
                    d_start = date_obj.replace(hour=0, minute=0, second=0, microsecond=0)
                    lookup_keys.append({
                        "scheduled_at": d_start,
                        "time_block": tb,
                        "market_type": market_type
                    })

            existing_records_map = {}
            if lookup_keys:
                chunk_size = 1000
                total_found = 0
                for i in range(0, len(lookup_keys), chunk_size):
                    chunk = lookup_keys[i:i+chunk_size]
                    if not chunk: continue
                    query = {"$or": chunk}
                    try:
                        cursor = self.repository.serve_collection.find(query)
                        try:
                            found = await cursor.to_list(length=10000)
                        finally:
                            await cursor.close()
                        total_found += len(found)
                        for rec in found:
                             s_at = rec.get("scheduled_at")
                             if isinstance(s_at, datetime): s_at = s_at.replace(hour=0, minute=0, second=0, microsecond=0)
                             k = (s_at, rec.get("time_block"), rec.get("market_type"))
                             existing_records_map[k] = rec
                    except Exception as e:
                        logger.error(f"Error bulk fetching exchange records: {e}")
                logger.info(f"Bulk fetched {total_found} existing exchange records.")

            # Process raw data into exchange entities
            exchange_entities, historical_entities = await self._build_exchange_entities_with_mapping_and_routing(
                raw_data, metadata, provider_config, market_type, px_name, existing_records_map
            )
            
            # Persist entities
            return await self._persist_exchange_entities_with_routing(exchange_entities, historical_entities, provider_config)

        except Exception as e:
            logger.error("Error processing raw data to serve: %s", str(e), exc_info=True)
            return False
    
    def _validate_raw_data(self, raw_data: List[Dict[str, Any]]) -> bool:
        """Validate raw data input"""
        if not raw_data:
            logger.warning("No raw data provided for processing")
            return False

        if not isinstance(raw_data, list):
            logger.error("Raw data must be a list, got: %s", type(raw_data))
            return False
            
        return True
            
    def _extract_market_type(self, provider_config: Dict[str, Any]) -> str:
        """Extract market type from provider configuration"""
        config = provider_config.get("metadata", {}).get("config", {})
        market_type = config.get("market_type", "unknown").upper()
                
        if market_type == "UNKNOWN":
            # Fallback to data_type for backward compatibility
            market_type = config.get("data_type", "unknown").upper()
            
        # Fallback to provider code parsing if still unknown
        if market_type == "UNKNOWN" or not market_type:
            provider_code = provider_config.get("code", "")
            if provider_code:
                parts = provider_code.split("_")
                if len(parts) >= 2:
                    # Generic heuristic for XXX_MARKET_PROVIDER
                    # Iterate to find known market types
                    known_types = ["RTM", "DAM", "GDAM", "HPDAM", "SCED", "RRAS"]
                    for part in parts:
                        if part in known_types:
                            market_type = part
                            logger.info("Inferred market_type '%s' from provider code '%s'", market_type, provider_code)
                            break
        
        if market_type == "UNKNOWN":
            logger.warning("Could not determine market_type from config or code: %s", provider_config.get("code"))
            
        logger.debug("Using extracted market_type: %s", market_type)
        return market_type

    def _extract_px_name(self, provider_config: Dict[str, Any]) -> str:
        """Extract PX name from provider configuration with fallback"""
        meta_data = provider_config.get("metadata", {})
        px_name = meta_data.get("source", "unknown").upper()
        
        # Fallback to provider code parsing if unknown
        if px_name == "UNKNOWN" or not px_name:
            provider_code = provider_config.get("code", "")
            if provider_code:
                parts = provider_code.split("_")
                if len(parts) >= 1:
                    # First part is usually PX name (IEX, PXIL, HPX)
                    potential_px = parts[0]
                    known_pxs = ["IEX", "PXIL", "HPX"]
                    if potential_px in known_pxs:
                        px_name = potential_px
                        logger.info("Inferred px_name '%s' from provider code '%s'", px_name, provider_code)
        
        return px_name
    
    async def _build_exchange_entities_with_mapping(self, raw_data: List[Dict[str, Any]], metadata: Dict[str, Any],
                                             provider_config: Dict[str, Any], market_type: str) -> List[ExchangeServeEntity]:
        """Build exchange entities using provider mapping configuration"""
        exchange_entities = []
        
        # Get mapping configuration from provider
        mapping_config = self._get_provider_mapping_config(provider_config)
        
        for item in raw_data:
            if not isinstance(item, dict):
                logger.warning("Skipping non-dict item in raw data")
                continue

            # Skip summary data with more robust detection
            if self._is_summary_record(item):
                continue

            try:
                # Validate required fields before processing
                validation_errors = ExchangeServeEntity.validate_required_fields(item, provider_config)
                if validation_errors:
                    logger.warning("Validation errors for exchange record: %s", validation_errors)
                    continue
                
                # Process time block with enhanced validation
                time_block = await self._process_timeblock(item.get("Time Block", ""))
                if time_block is None:
                    continue

                scheduled_date = item.get("Date")
                if not isinstance(scheduled_date, datetime):
                    logger.warning("Invalid or missing Date field")
                    continue

                # Determine updated flag based on existing record and data changes
                updated_flag = await self._determine_updated_flag_with_mapping(
                    scheduled_date, time_block, market_type, item, mapping_config
                )
                
                # Only create entity if we need to upsert (skip if no changes needed)
                if updated_flag is not None:
                    exchange_entity = await self._build_exchange_record_with_mapping(
                        raw_data=item,
                        time_block=time_block,
                        metadata=metadata,
                        provider_config=provider_config,
                        mapping_config=mapping_config,
                        updated_flag=updated_flag,
                        market_type=market_type
                    )
                    exchange_entities.append(exchange_entity)

            except Exception as entity_error:
                logger.error("Error processing exchange record for item %s: %s", 
                           item.get("Date", "unknown"), str(entity_error), exc_info=True)
                continue

        return exchange_entities
    
    async def _build_exchange_entities_with_mapping_and_routing(
        self, 
        raw_data: List[Dict[str, Any]], 
        metadata: Dict[str, Any], 
        provider_config: Dict[str, Any], 
        market_type: str,
        px_name: str,
        existing_records_map: Optional[Dict[Tuple[Any, ...], Any]] = None
    ) -> tuple[List[ExchangeServeEntity], List[ExchangeHistoricalEntity]]:
        """Build exchange entities using provider mapping configuration with date-based routing"""
        exchange_entities = []
        historical_entities = []
        
        # Get mapping configuration from provider
        mapping_config = self._get_provider_mapping_config(provider_config)
        
        for item in raw_data:
            if not isinstance(item, dict):
                logger.warning("Skipping non-dict item in raw data")
                continue

            # Skip summary data with more robust detection
            if self._is_summary_record(item):
                continue

            try:
                # Validate required fields before processing
                validation_errors = ExchangeServeEntity.validate_required_fields(item, provider_config)
                if validation_errors:
                    logger.warning("Validation errors for exchange record: %s", validation_errors)
                    continue
                
                # Process time block with enhanced validation
                # Support both "Time Block" and "time_block" (and "timeblock") keys
                time_block_val = item.get("Time Block") or item.get("time_block") or item.get("timeblock") or ""
                time_block = await self._process_timeblock(time_block_val)
                if time_block is None:
                    continue

                scheduled_date = item.get("Date") or item.get("date") or item.get("scheduled_at")
                # Parse string date if necessary
                if isinstance(scheduled_date, str):
                    try:
                        clean_date = scheduled_date.strip()
                        if "-" in clean_date:
                            scheduled_date = datetime.strptime(clean_date, "%d-%m-%Y")
                        else:
                            scheduled_date = datetime.strptime(clean_date, "%d/%m/%Y")
                    except ValueError:
                         logger.warning(f"Failed to parse date string: {scheduled_date}")
                         continue

                if not isinstance(scheduled_date, datetime):
                    logger.warning("Invalid or missing Date field")
                    continue
                
                # Update item with parsed datetime object to ensure consistency downstream
                item["Date"] = scheduled_date

                # Calculate date difference for routing
                today_date = datetime.now().date()
                record_date = scheduled_date.date()
                day_diff = (today_date - record_date).days
                
                # Lookup existing record
                existing_record = None
                if existing_records_map:
                     d_start = scheduled_date.replace(hour=0, minute=0, second=0, microsecond=0)
                     existing_record = existing_records_map.get((d_start, time_block, market_type))

                # Route based on date rules
                if day_diff >= 3:
                    # Historical only - process for historical collection
                    logger.debug(f"Routing exchange record to historical only: {record_date}")
                    historical_entity = self._build_exchange_historical_entity_with_mapping(
                        raw_data=item,
                        time_block=time_block,
                        metadata=metadata,
                        provider_config=provider_config,
                        mapping_config=mapping_config,
                        market_type=market_type,
                        px_name=px_name
                    )
                    if historical_entity:
                        historical_entities.append(historical_entity)
                elif day_diff == 1:
                    # Yesterday - both serve and historical
                    logger.debug(f"Routing exchange record to both serve and historical: {record_date}")
                    # Process for serve
                    updated_flag = await self._determine_updated_flag_with_mapping(
                        scheduled_date, time_block, market_type, item, mapping_config, existing_record
                    )
                    if updated_flag is not None:
                        exchange_entity = await self._build_exchange_record_with_mapping(
                            raw_data=item,
                            time_block=time_block,
                            metadata=metadata,
                            provider_config=provider_config,
                            mapping_config=mapping_config,
                            updated_flag=updated_flag,
                            market_type=market_type, 
                            px_name=px_name
                        )
                        exchange_entities.append(exchange_entity)
                    
                    # Also add to historical
                    historical_entity = self._build_exchange_historical_entity_with_mapping(
                        raw_data=item,
                        time_block=time_block,
                        metadata=metadata,
                        provider_config=provider_config,
                        mapping_config=mapping_config,
                        market_type=market_type,
                        px_name=px_name
                    )
                    if historical_entity:
                        historical_entities.append(historical_entity)
                else:
                    # Today or tomorrow - serve only
                    logger.debug(f"Routing exchange record to serve only: {record_date}")
                    updated_flag = await self._determine_updated_flag_with_mapping(
                        scheduled_date, time_block, market_type, item, mapping_config, existing_record
                    )
                    if updated_flag is not None:
                        exchange_entity = await self._build_exchange_record_with_mapping(
                            raw_data=item,
                            time_block=time_block,
                            metadata=metadata,
                            provider_config=provider_config,
                            mapping_config=mapping_config,
                            updated_flag=updated_flag,
                            market_type=market_type,
                            px_name=px_name
                        )
                        exchange_entities.append(exchange_entity)

            except Exception as entity_error:
                logger.error("Error processing exchange record for item %s: %s", 
                           item.get("Date", "unknown"), str(entity_error), exc_info=True)
                continue

        return exchange_entities, historical_entities
    
    def _get_provider_mapping_config(self, provider_config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """
        Extract and organize mapping configuration from provider config.
        If mapping is empty, auto-generates identity mapping from provider fields
        (excluding structural fields like scheduled_at, time_block, market_type, px_name).
        
        Args:
            provider_config: Provider configuration containing mapping
            
        Returns:
            Dictionary with serve_field as key and mapping details as value
        """
        mapping_config = {}
        
        # Get mapping from provider configuration
        mapping = provider_config.get("mapping", [])
        
        if mapping:
            for field_map in mapping:
                serve_field = field_map.get("serve_field")
                provider_field = field_map.get("provider_field")
                transformation = field_map.get("transformation")
                
                if serve_field and provider_field:
                    mapping_config[serve_field] = {
                        "provider_field": provider_field,
                        "transformation": transformation,
                        "field_type": self._determine_field_type(serve_field)
                    }
        else:
            # Auto-generate identity mapping from provider fields when mapping is empty
            # Exclude structural/key fields that are not data fields
            structural_fields = {"scheduled_at", "time_block", "market_type", "px_name", "date", "Date"}
            fields = provider_config.get("fields", [])
            
            for field_def in fields:
                field_name = field_def.get("field_name", "")
                if field_name and field_name not in structural_fields:
                    mapping_config[field_name] = {
                        "provider_field": field_name,
                        "transformation": None,
                        "field_type": self._determine_field_type(field_name)
                    }
            
            if mapping_config:
                logger.info("Auto-generated %d field mappings from provider fields (mapping was empty)", len(mapping_config))
        
        logger.debug("Extracted %d field mappings from provider configuration", len(mapping_config))
        return mapping_config
    
    def _determine_field_type(self, serve_field: str) -> str:
        """Determine the data type for a serve field based on field name patterns"""
        if serve_field in ["buy_bid", "sell_bid", "mcv", "mcp", "acp", "acv", "final_scheduled_volume"]:
            # These fields can now be float or dict (object)
            return "mixed"
        elif serve_field in ["time_block"]:
            return "int"
        elif serve_field in ["market_type", "px_name", "data_type", "source"]:
            return "string"
        else:
            return "string"  # Default to string
    
    def _is_summary_record(self, item: Dict[str, Any]) -> bool:
        """Check if record is a summary record with robust detection"""
        # Check multiple indicators for summary records
        summary_indicators = ["Summary", "Total", "Aggregate", "Grand Total"]
        
        for key, value in item.items():
            if isinstance(value, str):
                for indicator in summary_indicators:
                    if indicator.lower() in value.lower():
                        return True

        return False
    
    async def _process_timeblock(self, time_block_input: Any) -> Optional[int]:
        """Process timeblock with comprehensive validation and error handling"""
        if isinstance(time_block_input, int):
            if self._is_valid_timeblock(time_block_input):
                return time_block_input
            logger.error("Invalid integer timeblock: %d", time_block_input)
            return None

        if not time_block_input or not isinstance(time_block_input, str):
            logger.error("Invalid timeblock input: %s (type: %s)", time_block_input, type(time_block_input))
            return None

        time_block_str = time_block_input.strip()

        if not time_block_str:
            logger.warning("Empty timeblock string")
            return None

        time_block = await self._get_timeblock_number(time_block_str)

        if time_block == 0:
            time_block = await self._parse_timeblock_alternative(time_block_str)

        if not self._is_valid_timeblock(time_block):
            logger.error("Invalid timeblock '%s' -> %d (must be %d-%d)",
                         time_block_str, time_block, MIN_TIMEBLOCK, MAX_TIMEBLOCK)
            return None

        return time_block

    async def _parse_timeblock_alternative(self, time_block_str: str) -> int:
        """Alternative parsing for time ranges like '00:00-00:15'"""
        if "-" not in time_block_str:
            return 0

        try:
            time_parts = time_block_str.split("-", 1)
            if len(time_parts) != 2:
                return 0

            start_time, end_time = time_parts[0].strip(), time_parts[1].strip()
            timeblock_data = await self.get_timeblock_by_time_range(start_time, end_time)
            return timeblock_data.get("timeblock", 0) if timeblock_data else 0
        except Exception as e:
            logger.error("Error parsing timeblock range '%s': %s", time_block_str, str(e))
            return 0
    
    def _is_valid_timeblock(self, time_block: int) -> bool:
        """Validate timeblock is within valid range"""
        return isinstance(time_block, int) and MIN_TIMEBLOCK <= time_block <= MAX_TIMEBLOCK
    
    async def _determine_updated_flag_with_mapping(
        self, 
        scheduled_date: datetime, 
        time_block: int, 
        market_type: str, 
        new_item: Dict[str, Any], 
        mapping_config: Dict[str, Dict[str, Any]],
        existing_record: Optional[Dict[str, Any]] = None
    ) -> Optional[UpdatedFlag]:
        """
        Determine the appropriate updated flag based on existing record and data changes.
        """
        # Fallback query if not provided provided (e.g. legacy calls)
        if existing_record is None:
            existing_record = await self.repository.find_by_schedule_timeblock_market(
                scheduled_date, time_block, market_type
            )
        
        if existing_record:
            # Check if data has changed using provider mapping
            if self._has_exchange_data_changed_with_mapping(existing_record, new_item, mapping_config):
                # Data changed - mark as UPDATED (not REPLACED - REPLACED is only for explicit provider flags)
                logger.debug("Data changed for market_type: %s - will update with UPDATED flag", market_type)
                return UpdatedFlag.UPDATED
            else:
                # Data not changed - check if flag needs to change from UPDATED to OLD
                if existing_record.get("updated_flag") == UpdatedFlag.UPDATED:
                    logger.debug("Data unchanged, changing flag to OLD for market_type: %s", market_type)
                    return UpdatedFlag.OLD
                else:
                    # If already OLD or other status, skip upsert
                    logger.debug("Data unchanged and flag already appropriate for market_type: %s - skipping", market_type)
                    return None
        else:
            # New record
            logger.debug("New record for market_type: %s - will insert with UPDATED flag", market_type)
            return UpdatedFlag.UPDATED

    def _has_exchange_data_changed_with_mapping(self, existing_record: Dict[str, Any], new_record: Dict[str, Any], 
                                               mapping_config: Dict[str, Dict[str, Any]]) -> bool:
        """
        Check if exchange data has changed using provider mapping configuration
        
        Args:
            existing_record: Existing exchange record from database
            new_record: New exchange record from raw data
            mapping_config: Provider mapping configuration
            
        Returns:
            bool: True if data has changed
        """
        # Compare fields based on provider mapping configuration
        for serve_field, mapping_info in mapping_config.items():
            provider_field = mapping_info["provider_field"]
            field_type = mapping_info["field_type"]
            
            # Get existing value from database record
            existing_value = existing_record.get(serve_field)
            
            # Get new value from raw data using provider field
            new_raw_value = new_record.get(provider_field)
            
            # Convert new value based on field type
            new_value = self._convert_value_by_type(new_raw_value, field_type)
            
            # Compare values with appropriate tolerance
            if not self._compare_values_with_tolerance(existing_value, new_value, field_type, serve_field):
                logger.debug("Field '%s' changed: %s -> %s (provider_field: %s)", 
                           serve_field, existing_value, new_value, provider_field)
                return True
        
        return False
    
    def _convert_value_by_type(self, value: Any, field_type: str) -> Any:
        """Convert value to appropriate type based on field type"""
        if field_type == "float":
            return ExchangeServeEntity._safe_float(value)
        elif field_type == "int":
            try:
                return int(ExchangeServeEntity._safe_float(value))
            except (ValueError, TypeError):
                return 0
        elif field_type == "mixed":
            # For mixed types, if it's a dict, return as is
            # If it's a number/string, try to float it
            if isinstance(value, dict):
                return value
            else:
                return ExchangeServeEntity._safe_float(value)
        else:
            return value
    
    def _compare_values_with_tolerance(self, existing_value: Any, new_value: Any, field_type: str, serve_field: str = "") -> bool:
        """Compare values with appropriate tolerance based on field type"""
        try:
            if field_type == "float":
                existing_float = float(existing_value or 0)
                new_float = float(new_value or 0)
                # Use different tolerances for price vs volume fields
                if "price" in serve_field.lower() or "mcp" in serve_field.lower() or "acp" in serve_field.lower():
                    tolerance = PRICE_COMPARISON_TOLERANCE
                else:
                    tolerance = FLOAT_COMPARISON_TOLERANCE
                return abs(existing_float - new_float) <= tolerance
            elif field_type == "int":
                existing_int = int(float(existing_value or 0))
                new_int = int(float(new_value or 0))
                return existing_int == new_int
            elif field_type == "string":
                return str(existing_value or "").strip() == str(new_value or "").strip()
            elif field_type == "mixed":
                # Handle mixed comparison (dict vs float vs etc)
                if isinstance(existing_value, dict) and isinstance(new_value, dict):
                    # For dicts, we could do deep comparison
                    # Simple JSON-like equality for now
                    return existing_value == new_value
                elif isinstance(existing_value, (int, float)) and isinstance(new_value, (int, float)):
                    return abs(float(existing_value) - float(new_value)) <= FLOAT_COMPARISON_TOLERANCE
                else:
                    # Fallback for distinct types
                    return existing_value == new_value
            else:
                return existing_value == new_value
        except (ValueError, TypeError) as e:
            logger.warning("Error comparing values: %s", str(e))
            return False
    
    async def _persist_exchange_entities(self, exchange_entities: List[ExchangeServeEntity], 
                                  provider_config: Dict[str, Any]) -> bool:
        """Persist exchange entities with atomic upsert operations"""
        if not exchange_entities:
            logger.info("No exchange entities to process (all records unchanged or invalid)")
            return True
        
        try:
            # Prepare entity records with validation
            entity_records = self._prepare_entity_records(exchange_entities, provider_config)
            if not entity_records:
                logger.warning("No valid entity records to upsert")
                return False
            
            # Perform atomic upsert with transaction safety
            upsert_result = await self.repository.bulk_upsert_exchange_records(entity_records)
            
            if upsert_result.get("success"):
                self._log_upsert_results(upsert_result)
                return True
            else:
                logger.error("Bulk upsert failed: %s", upsert_result.get("error", "Unknown error"))
                return False
                
        except Exception as e:
            logger.error("Error persisting exchange entities: %s", str(e), exc_info=True)
            return False
    
    def _prepare_entity_records(self, entities: List[ExchangeServeEntity], 
                               provider_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Prepare entity records with validation"""
        valid_records = []
        
        for entity in entities:
            try:
                if hasattr(entity, 'model_dump_for_db'):
                    record = entity.model_dump_for_db(provider_config)
                else:
                    record = entity.model_dump(by_alias=True)
                
                # Validate record before adding
                if self._validate_upsert_record(record):
                    valid_records.append(record)
                else:
                    logger.warning("Skipping invalid entity record")
                    
            except Exception as e:
                logger.error("Error preparing entity record: %s", str(e))
                continue
        
        return valid_records
    
    def _validate_upsert_record(self, record: Dict[str, Any]) -> bool:
        """Validate record has required fields for upsert"""
        required_fields = ["scheduled_at", "time_block", "market_type"]
        return all(field in record and record[field] is not None for field in required_fields)
    
    def _log_upsert_results(self, result: Dict[str, Any]) -> None:
        """Log upsert operation results"""
        total_operations = result.get("inserted_count", 0) + result.get("replaced_count", 0)
        logger.info("Atomically processed %d exchange serve entities (%d inserted, %d replaced)", 
                   total_operations, result.get("inserted_count", 0), result.get("replaced_count", 0))
    
    async def _build_exchange_record_with_mapping(self, raw_data: Dict[str, Any], time_block: int, 
                                           metadata: Dict[str, Any], provider_config: Dict[str, Any],
                                           mapping_config: Dict[str, Dict[str, Any]], updated_flag: UpdatedFlag,
                                           market_type: Optional[str] = None, px_name: Optional[str] = None) -> ExchangeServeEntity:
        """
        Build exchange record entity using provider mapping configuration
        
        Args:
            raw_data: Raw exchange data item
            time_block: Processed time block number (already converted to integer 1-96)
            metadata: Processing metadata (now includes raw_data_id)
            provider_config: Provider configuration
            mapping_config: Provider mapping configuration
            updated_flag: Updated flag to set
            market_type: Explicit market type
            px_name: Explicit PX name
            
        Returns:
            ExchangeServeEntity: Built exchange entity
        """
        # Ensure time_block is an integer (should already be processed by caller)
        if not self._is_valid_timeblock(time_block):
            logger.error("Invalid time_block value: %s (type: %s)", time_block, type(time_block))
            raise ValueError(f"time_block must be an integer between {MIN_TIMEBLOCK}-{MAX_TIMEBLOCK}, got: {time_block}")
        
        # Create mapped raw data using provider mapping
        mapped_raw_data = self._apply_provider_mapping(raw_data, mapping_config)
        
        # Add processed time_block to mapped data for entity creation
        mapped_raw_data["time_block"] = time_block
        
        # Extract source revision number from raw data
        source_revision = raw_data.get("revision_number") or raw_data.get("Revision") or raw_data.get("version")
        
        # Determine system revision based on whether this is new or update
        system_revision = 1  # Default for new records
        existing_record = None
        
        # If updating, fetch existing record and increment (handles migration: 0 -> 1 for old records)
        if updated_flag in [UpdatedFlag.UPDATED, UpdatedFlag.OLD, UpdatedFlag.REPLACED]:
            scheduled_date = raw_data.get("Date")
            if not isinstance(scheduled_date, datetime):
                raise ValueError("Invalid or missing Date in raw_data")
            # market_type is passed as argument now
            if not market_type:
                market_type = self._extract_market_type(provider_config)
            existing_record = await self.repository.find_by_schedule_timeblock_market(
                scheduled_date, time_block, market_type
            )
            if existing_record:
                existing_system_revision = existing_record.get("system_revision_number", 0)
                system_revision = existing_system_revision + 1
        
        # If not received in raw data, source revision = system revision
        if source_revision is None:
            if existing_record:
                # Check if existing record has source revision, otherwise use new system revision
                existing_source_revision = existing_record.get("source_revision_number")
                if existing_source_revision is not None:
                    source_revision = existing_source_revision
                else:
                    source_revision = system_revision
            else:
                source_revision = system_revision
        
        # Create entity using the from_raw_data class method
        entity = ExchangeServeEntity.from_raw_data(
            raw_data=mapped_raw_data,
            metadata=metadata,
            provider_config=provider_config,
            market_type=market_type,
            px_name=px_name
        )
        
        # Override the updated_flag with the determined value
        entity.updated_flag = updated_flag
        
        # Store raw_data_id instead of execution_id in the entity
        entity.raw_data_id = metadata.get("raw_data_id")
        
        # Set revision tracking
        entity.source_revision_number = source_revision
        entity.system_revision_number = system_revision
        
        return entity
    
    def _apply_provider_mapping(self, raw_data: Dict[str, Any], mapping_config: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """
        Apply provider mapping to raw data
        
        Args:
            raw_data: Original raw data (already mapped to serve_field by ingestion if scraped)
            mapping_config: Provider mapping configuration
            
        Returns:
            Mapped raw data with serve field names
        """
        mapped_data = {}
        
        # In the new ingestion flow, raw_data presumably already has serve_field keys.
        # However, we still need to apply type conversion and transformations if not already done.
        # Also, for backward compatibility or direct calls, we should check if mapping is needed.
        
        # Check if raw_data keys match serve_fields
        is_already_mapped = any(field in raw_data for field in mapping_config.keys())
        
        for serve_field, mapping_info in mapping_config.items():
            provider_field = mapping_info["provider_field"]
            transformation = mapping_info["transformation"]
            field_type = mapping_info["field_type"]
            
            value = None
            if is_already_mapped:
                # If already mapped, look for serve_field
                if serve_field in raw_data:
                    value = raw_data[serve_field]
            else:
                # Fallback to provider_field (old behavior)
                if provider_field in raw_data:
                    value = raw_data[provider_field]
            
            if value is not None:
                # Apply transformation if specified
                if transformation:
                    value = ExchangeServeEntity._apply_transformation(value, transformation)
                
                # Convert to appropriate type
                if field_type == "float":
                    value = ExchangeServeEntity._safe_float(value)
                elif field_type == "int":
                    try:
                        value = int(ExchangeServeEntity._safe_float(value))
                    except (ValueError, TypeError):
                        value = 0
                
                # Use serve field name in mapped data
                mapped_data[serve_field] = value
        
        # Add any unmapped fields that might be needed (like metadata)
        # We exclude provider fields if we are in legacy mode, but if already mapped, we just keep unknown keys
        known_keys = set(mapping_config.keys()) | {mapping_info["provider_field"] for mapping_info in mapping_config.values()}
        
        for key, value in raw_data.items():
            if key not in known_keys or (is_already_mapped and key not in mapping_config):
                 mapped_data[key] = value
        
        # logger.debug("Applied provider mapping: %d fields processed", len(mapping_config))
        return mapped_data
    
    def _build_exchange_historical_entity_with_mapping(self, raw_data: Dict[str, Any], time_block: int, 
                                                      metadata: Dict[str, Any], provider_config: Dict[str, Any],
                                                      mapping_config: Dict[str, Dict[str, Any]],
                                                      market_type: Optional[str] = None, px_name: Optional[str] = None) -> Optional[ExchangeHistoricalEntity]:
        """
        Build exchange historical entity using provider mapping configuration
        
        Args:
            raw_data: Raw exchange data item
            time_block: Processed time block number
            metadata: Processing metadata
            provider_config: Provider configuration
            mapping_config: Provider mapping configuration
            market_type: Explicit market type
            px_name: Explicit PX name
            
        Returns:
            ExchangeHistoricalEntity: Built historical entity or None if invalid
        """
        try:
            # Ensure time_block is an integer
            if not self._is_valid_timeblock(time_block):
                logger.error("Invalid time_block value: %s (type: %s)", time_block, type(time_block))
                return None
            
            # Create mapped raw data using provider mapping
            mapped_raw_data = self._apply_provider_mapping(raw_data, mapping_config)
            
            # Add processed time_block to mapped data for entity creation
            mapped_raw_data["time_block"] = time_block
            
            # Get current time and use passed market_type or extract
            current_time = datetime.now()
            if not market_type:
                market_type = self._extract_market_type(provider_config)
            
            # Get px_name from args or provider metadata
            if not px_name:
                px_name = self._extract_px_name(provider_config)
            
            # Add required fields for ExchangeHistoricalEntity
            mapped_raw_data["market_type"] = market_type
            mapped_raw_data["px_name"] = px_name
            mapped_raw_data["inserted_at"] = current_time
            mapped_raw_data["updated_at"] = current_time
            mapped_raw_data["created_at"] = current_time
            mapped_raw_data["timestamp"] = current_time

            # Ensure scheduled_at is present (raw data uses "Date" or "date")
            if "scheduled_at" not in mapped_raw_data:
                date_val = raw_data.get("Date") or raw_data.get("date") or raw_data.get("scheduled_at")
                if isinstance(date_val, datetime):
                    mapped_raw_data["scheduled_at"] = date_val.date()
                elif isinstance(date_val, date):
                    mapped_raw_data["scheduled_at"] = date_val

            # Create historical entity from mapped data
            historical_entity = ExchangeHistoricalEntity(**mapped_raw_data)
            
            # Set metadata fields
            historical_entity.execution_id = metadata.get("execution_id")
            historical_entity.raw_data_id = metadata.get("raw_data_id")
            historical_entity.updated_flag = UpdatedFlag.UPDATED
            
            return historical_entity
            
        except Exception as e:
            logger.error("Error building exchange historical entity: %s", str(e))
            return None
    
    async def _persist_exchange_entities_with_routing(self, exchange_entities: List[ExchangeServeEntity], 
                                               historical_entities: List[ExchangeHistoricalEntity], 
                                               provider_config: Dict[str, Any]) -> bool:
        """Persist exchange entities with atomic upsert operations and historical routing"""
        serve_success = True
        historical_success = True
        
        # Process serve entities
        if exchange_entities:
            try:
                # Prepare entity records with validation
                entity_records = self._prepare_entity_records(exchange_entities, provider_config)
                if entity_records:
                    # Perform atomic upsert with transaction safety
                    upsert_result = await self.repository.bulk_upsert_exchange_records(entity_records)
                    
                    if upsert_result.get("success"):
                        self._log_upsert_results(upsert_result)
                        serve_success = True
                    else:
                        logger.error("Bulk upsert failed: %s", upsert_result.get("error", "Unknown error"))
                        serve_success = False
                else:
                    logger.warning("No valid entity records to upsert")
                    serve_success = False
            except Exception as e:
                logger.error("Error persisting exchange entities: %s", str(e), exc_info=True)
                serve_success = False
        else:
            logger.info("No exchange serve entities to process (all records unchanged or invalid)")
        
        # Process historical entities
        if historical_entities:
            try:
                # Convert historical entities to records
                historical_records = []
                for entity in historical_entities:
                    try:
                        record = entity.model_dump(by_alias=True)
                        # Fix date encoding error for MongoDB
                        if isinstance(record.get("scheduled_at"), date) and not isinstance(record.get("scheduled_at"), datetime):
                            record["scheduled_at"] = datetime.combine(record["scheduled_at"], datetime.min.time())
                        historical_records.append(record)
                    except Exception as e:
                        logger.error("Error converting historical entity to record: %s", str(e))
                        continue
                
                if historical_records:
                    # Insert into historical collection with robust error handling
                    try:
                        # Use ordered=False to continue processing remaining records on error
                        await self.repository.historical_collection.insert_many(historical_records, ordered=False)
                        logger.info("Processed and stored %d exchange historical entities", len(historical_records))
                    except Exception as e:
                        # Handle specific errors without failing the whole batch
                        from pymongo.errors import BulkWriteError, DuplicateKeyError
                        
                        if isinstance(e, BulkWriteError):
                            logger.warning(f"Bulk write error inserting historical records (likely duplicates): {str(e)}")
                        elif isinstance(e, DuplicateKeyError):
                            logger.warning(f"Duplicate key error inserting historical records: {str(e)}")
                        else:
                            logger.error("Error inserting historical exchange entities: %s", str(e))
                            # Consider historical processing as failed only for unknown errors, 
                            # or just log and proceed to not block serve data update
                            historical_success = False
                else:
                    logger.warning("No valid historical records to insert")
                    historical_success = False
            except Exception as e:
                logger.error("Error inserting historical exchange entities: %s", str(e))
                historical_success = False
        else:
            logger.info("No exchange historical entities to process")
        
        return serve_success and historical_success

    async def execute_archival_job(self) -> Dict[str, Any]:
        """
        Execute exchange data archival as a separate cron job with its own correlation ID
        Moves data older than 4 days to historical collection
        """
        job_type = "exchange_serve_archival"
        job_name = "Exchange Serve Data Archival"
        schedule = "0 0 * * *"  # Daily at 12 AM
        start_time = datetime.now()
        
        # Generate and record job start to get execution_id (correlation ID)
        execution_id = await self._record_job_execution(job_type, job_name, schedule, start_time, is_start=True)
        if not execution_id:
            logger.error("Failed to record job start for %s", job_type)
            return {
                "status": "error",
                "message": "Failed to record job start",
                "execution_time": 0.0,
                "data_type": "exchange"
            }
        
        # Set execution_id as correlation ID using base processor method
        self.set_correlation_id(execution_id)
        
        logger.info("=== EXECUTING EXCHANGE DATA ARCHIVAL JOB ===")
        
        try:
            # Archive old exchange data to keep a 4-day active window (day<=D-3 archived)
            archive_result = await self.repository.archive_old_exchange_data()
            
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            
            if archive_result.get("error"):
                error_result = {
                    "status": "error",
                    "message": f"Exchange data archival error: {archive_result['error']}",
                    "execution_time": execution_time,
                    "data_type": "exchange",
                    "correlation_id": execution_id
                }
                
                await self._record_job_execution(
                    job_type=job_type,
                    job_name=job_name,
                    schedule=schedule,
                    execution_start=start_time,
                    execution_end=end_time,
                    execution_id=execution_id,
                    status="error",
                    success=False,
                    result=error_result,
                    error_message=archive_result["error"],
                    is_start=False
                )
                
                logger.error("Exchange data archival failed: %s", archive_result["error"])
                return error_result
            else:
                archived_count = archive_result.get("archived_count", 0)
                success_result = {
                    "status": "success",
                    "message": f"Archived {archived_count} exchange records",
                    "execution_time": execution_time,
                    "archived_count": archived_count,
                    "cutoff_date": archive_result.get("cutoff_date"),
                    "data_type": "exchange",
                    "correlation_id": execution_id
                }
                
                await self._record_job_execution(
                    job_type=job_type,
                    job_name=job_name,
                    schedule=schedule,
                    execution_start=start_time,
                    execution_end=end_time,
                    execution_id=execution_id,
                    status="success",
                    success=True,
                    result=success_result,
                    is_start=False
                )
                
                logger.info("Exchange data archival completed: %d records archived in %.2fs", 
                           archived_count, execution_time)
                return success_result
                
        except Exception as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            error_result = {
                "status": "error",
                "message": f"Exchange data archival error: {str(e)}",
                "execution_time": execution_time,
                "data_type": "exchange",
                "correlation_id": execution_id
            }
            
            await self._record_job_execution(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=end_time,
                execution_id=execution_id,
                status="error",
                success=False,
                result=error_result,
                error_message=str(e),
                is_start=False
            )
            
            logger.error("Error executing exchange data archival job: %s", str(e), exc_info=True)
            return error_result
        finally:
            # Clear correlation ID after job completion
            self.clear_correlation_id()


