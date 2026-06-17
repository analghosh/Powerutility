"""
Base Raw Data Processor
Contains common functionality for all raw data processors including schema validation,
data mapping, job tracking, and error handling.
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional

from libs.core.job_entities import JobDetail
from libs.utils.csv_date_parse import parse_ingestion_date
from libs.core.job_factory import create_job_data_repository, create_job_detail_repository
from libs.models.provider_models import ProviderEntity
from libs.services.provider_service import ProviderService
from libs.services.raw_data_service import RawDataService

from services.ingestion.app.core.config import settings

logger = logging.getLogger("base_raw_data_processor")


class BaseRawDataProcessor(ABC):
    """
    Base class for all raw data processors providing common functionality
    """

    def __init__(self):
        """Initialize base raw data processor with common services"""
        # Initialize unified raw data service
        self.raw_data_service = RawDataService(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE
        )

        self.provider_service = ProviderService(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE
        )

        # Initialize job repositories for audit logging
        try:
            self.job_data_repository = create_job_data_repository(
                getattr(settings, 'AUDIT_DB_URI', settings.MONGODB_URI),
                getattr(settings, 'AUDIT_DB_NAME', 'audit_db'),
                getattr(settings, 'JOB_DATA_COLLECTION', 'job_data')
            )
            self.job_detail_repository = create_job_detail_repository(
                getattr(settings, 'AUDIT_DB_URI', settings.MONGODB_URI),
                getattr(settings, 'AUDIT_DB_NAME', 'audit_db'),
                getattr(settings, 'JOB_DETAIL_COLLECTION', 'job_details')
            )
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.warning("Could not initialize job repositories for audit logging: %s", str(e))
            self.job_data_repository = None
            self.job_detail_repository = None

    def validate_data_schema(self, data: List[Dict[str, Any]], provider: ProviderEntity) -> Dict[str, Any]:
        """
        Validate data against provider schema
        
        Args:
            data: List of data records to validate
            provider: Provider entity containing schema definition
            
        Returns:
            Dict[str, Any]: Validation result with success status and any errors
        """
        try:
            if not provider.fields:
                logger.warning("No schema defined for provider %s, skipping validation", provider.code)
                return {"success": True, "validated_records": len(data), "validated_data": data, "errors": []}

            schema = provider.fields
            required_fields = {field.field_name.lower() for field in schema if field.is_required}
            field_types = {field.field_name.lower(): field.field_type for field in schema}
            field_name_mapping = {field.field_name.lower(): field.field_name for field in schema}
            expected_fields = {field.field_name.lower() for field in schema}

            # Create mapping lookup (source_field_lower -> serve_field_lower)
            mapping_lookup = {}
            if provider.mapping:
                for m in provider.mapping:
                    mapping_lookup[m.provider_field.lower()] = m.serve_field.lower()

            validation_errors = []
            validated_data = []

            for record_num, record in enumerate(data, start=1):
                try:
                    # Create case-insensitive record mapping
                    case_insensitive_record = {key.lower(): value for key, value in record.items()}

                    # Check required fields
                    # We need to account for mapping: a required field might be present under its source name
                    present_fields = set(case_insensitive_record.keys())
                    # Add mapped names to present_fields
                    for key in case_insensitive_record:
                        if key in mapping_lookup:
                            present_fields.add(mapping_lookup[key])

                    missing_fields = required_fields - present_fields
                    if missing_fields:
                        missing_fields_original = {field_name_mapping.get(field, field) for field in missing_fields}
                        missing_fields_filtered = [f for f in missing_fields_original if f is not None]
                        validation_errors.append(f"Record {record_num}: Missing required fields: {', '.join(missing_fields_filtered)}")
                        continue

                    # Check for unexpected fields (warn but don't fail)
                    # Note: We now allow fields that are mapping sources
                    unexpected_fields = set()
                    for key in case_insensitive_record:
                        if key not in expected_fields and key not in mapping_lookup:
                            unexpected_fields.add(key)
                            
                    if unexpected_fields:
                        # Optional: logger.debug("Record %d: Unexpected fields found: %s", record_num, ', '.join(unexpected_fields))
                        pass

                    # Convert and validate field types
                    converted_record = {}
                    
                    # We iterate schema fields to ensure we pick up the correct values regardless of source name
                    for field in schema:
                        field_name = field.field_name
                        field_name_lower = field_name.lower()
                        
                        value = None
                        found = False

                        # 1. Try direct match (target name in record)
                        if field_name_lower in case_insensitive_record:
                            value = case_insensitive_record[field_name_lower]
                            found = True
                        
                        # 2. If not found, try mapping (source name in record)
                        if not found:
                            # Find source field for this target field
                            source_field = None
                            if provider.mapping:
                                for m in provider.mapping:
                                    if m.serve_field.lower() == field_name_lower:
                                        source_field = m.provider_field.lower()
                                        break
                            
                            if source_field and source_field in case_insensitive_record:
                                value = case_insensitive_record[source_field]
                                found = True

                        if found:
                            try:
                                # Type conversion
                                converted_record[field_name] = self._convert_value_with_type(value, field.field_type)
                            except (ValueError, TypeError) as e:
                                validation_errors.append(f"Record {record_num}: Invalid type for field '{field_name}': {str(e)}")
                                break
                    else:
                        # Only add record if loop completed without break (no validation errors)
                        validated_data.append(converted_record)

                except Exception as e:
                    validation_errors.append(f"Record {record_num}: Validation error: {str(e)}")

            success = len(validation_errors) == 0
            result = {
                "success": success,
                "validated_records": len(validated_data),
                "total_records": len(data),
                "errors": validation_errors,
                "validated_data": validated_data if success else []
            }

            if not success:
                logger.error("Data validation failed: %s", "; ".join(validation_errors[:5]))  # Log first 5 errors

            return result

        except Exception as e:
            logger.error("Error during schema validation: %s", str(e))
            return {
                "success": False,
                "validated_records": 0,
                "total_records": len(data),
                "errors": [f"Schema validation error: {str(e)}"],
                "validated_data": []
            }

    def _convert_value_with_type(self, value: Any, field_type: str) -> Any:
        """
        Convert value to specified type
        
        Args:
            value: Value to convert
            field_type: Target type (string, integer, float, boolean, datetime)
            
        Returns:
            Any: Converted value
            
        Raises:
            ValueError: If conversion fails
            TypeError: If value type is incompatible
        """
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return None

        if isinstance(value, str):
            value = value.strip()
            # Handle common "no data" placeholder values
            no_data_values = {'-', '--', 'n/a', 'na', 'null', 'none', '-/-', 'n.a.', 'n.a'}
            if value.lower() in no_data_values:
                return None

        try:
            if field_type.lower() in ("string", "str", "text"):
                return str(value)
            elif field_type.lower() in ("integer", "int", "number"):
                return int(float(value))  # Handle "123.0" -> 123
            elif field_type.lower() in ("float", "decimal", "double"):
                return float(value)
            elif field_type.lower() in ("boolean", "bool"):
                if isinstance(value, bool):
                    return value
                if isinstance(value, str):
                    if value.lower() in ("true", "1", "yes", "on"):
                        return True
                    elif value.lower() in ("false", "0", "no", "off"):
                        return False
                    else:
                        raise ValueError(f"Cannot convert '{value}' to boolean")
                return bool(value)
            elif field_type.lower() in ("datetime", "timestamp", "date"):
                # If the parser already produced a datetime object, preserve it
                # as-is instead of re-parsing and potentially stripping the time.
                if isinstance(value, datetime):
                    return value
                return self._parse_scheduled_at_datetime(value)
            elif field_type.lower() in ("object", "dict", "list", "array", "json"):
                # Return complex types as-is
                return value
            else:
                # Unknown type, return as string
                logger.warning("Unknown field type '%s', treating as string", field_type)
                return str(value)

        except (ValueError, TypeError) as e:
            raise ValueError(f"Cannot convert '{value}' to {field_type}: {str(e)}")

    def _parse_scheduled_at_datetime(self, value: Any) -> datetime:
        """Parse scheduled_at for API/scrape/FTP ingestion (all standard formats)."""
        return parse_ingestion_date(value)

    def map_data_to_schema(self, data: List[Dict[str, Any]], provider: ProviderEntity) -> List[Dict[str, Any]]:
        """
        Map raw data to provider schema format
        
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

            # Get field mappings from provider.mapping field (correct implementation)
            # Create mapping lookup (serve_field -> provider_field) and reverse lookup (field_name -> serve_field)
            field_mappings = {}
            field_name_to_serve_field = {}
            
            # Populate mappings from provider config
            if provider.mapping:
                for mapping_obj in provider.mapping:
                    field_mappings[mapping_obj.serve_field] = mapping_obj.provider_field
            
            # Also create mapping from Schema Field Name to Serve Field Name
            # This logic assumes that for each field in provider.fields, there is a corresponding mapping entry
            # If not, we fall back to a snake_case version of the field name or the field_name itself
            for field in provider.fields:
                field_name = field.field_name
                found_serve_field = None
                
                # Check if this field name matches a serve_field directly (if they are same)
                if field_name in field_mappings:
                    found_serve_field = field_name
                else:
                    # Look for mapping where mapping.serve_field corresponds to this field
                    # Note: The matching logic here depends on how fields and mappings are related.
                    # Typically, mapping.serve_field SHOULD equal field.field_name if specific internal name is desired.
                    # However, if field.field_name is "Purchase Bid (MW)" (Display Name) and mapping.serve_field is "buy_bid" (Internal Name),
                    # we need a way to link them.
                    # The current BaseRawDataProcessor design seems to assume field.field_name is the target key.
                    # With this refactor, we want the target key to be the serve_field "buy_bid".
                    
                    # We will find the mapping entry that corresponds to this field.
                    # This relies on the convention that we are iterating fields to extract data.
                    # But actually, we should iterate the MAPPINGS to construct the output record if we want serve_fields.
                    pass

            mapped_data = []
            for record in data:
                mapped_record = {}
                
                # Iterate through configured mappings to build the record with serve_field keys
                if provider.mapping:
                    for mapping_obj in provider.mapping:
                        serve_field = mapping_obj.serve_field
                        provider_field = mapping_obj.provider_field
                        
                        val = None
                        if provider_field in record:
                            val = record[provider_field]
                        elif serve_field in record:
                            # If validation already renamed it to serve_field (because field_name=serve_field)
                            val = record[serve_field]
                        else:
                            # Try case-insensitive provider_field match
                            provider_field_lower = provider_field.lower()
                            for key, value in record.items():
                                if key.lower() == provider_field_lower:
                                    val = value
                                    break
                        
                        if val is not None:
                            mapped_record[serve_field] = val
                else:
                    # If no mapping provided, fallback to using schema field names
                    for field in provider.fields:
                        if field.field_name in record:
                           mapped_record[field.field_name] = record[field.field_name]
                
                mapped_data.append(mapped_record)

            logger.info("Mapped %d records to schema format", len(mapped_data))
            return mapped_data

        except Exception as e:
            logger.error("Error mapping data to schema: %s", str(e))
            return data  # Return original data if mapping fails

    def create_job_detail(self, job_type: str, job_name: str, schedule: str, 
                         execution_start: datetime, execution_id: str, job_id: str) -> JobDetail:
        """
        Create a job detail record for audit logging
        
        Args:
            job_type: Type of job (e.g., 'upload_processing', 'scrape_processing')
            job_name: Name of the job
            schedule: Schedule pattern
            execution_start: When the job started
            execution_id: Unique execution identifier
            job_id: Unique job identifier
        Returns:
            JobDetail: Created job detail record
        """
        return JobDetail(
            job_id=job_id,
            execution_id=execution_id,
            job_name=job_name,
            execution_start=execution_start,
            execution_end=None,
            duration_seconds=None,
            status="running",
            success=False,
            error_message=None,
            error_details=None,
            result_data=None,
            service="ingestion",
            metadata={"job_type": job_type, "schedule": schedule}
        )

    async def record_job_completion(self, job_type: str, job_name: str, schedule: str,
                             execution_start: datetime, execution_end: datetime, execution_id: str,
                             status: str, success: bool, job_id: str, result: Optional[Dict[str, Any]] = None,
                             error_message: Optional[str] = None) -> None:
        """
        Record job completion for audit logging
        
        Args:
            job_type: Type of job
            job_name: Name of the job
            schedule: Schedule pattern
            execution_start: When the job started
            execution_end: When the job ended
            execution_id: Unique execution identifier
            status: Final status
            success: Whether the job succeeded
            result: Optional result data
            error_message: Optional error message
        """
        if not self.job_detail_repository:
            logger.warning("Job detail repository not available, skipping audit logging")
            return

        try:
            # Prepare update data for the existing job detail record
            update_data = {
                "execution_end": execution_end,
                "duration_seconds": (execution_end - execution_start).total_seconds() if execution_end and execution_start else None,
                "status": status,
                "success": success,
                "error_message": error_message,
                "result_data": result,
            }
            
            # Update the existing job detail record
            updated = await self.job_detail_repository.update_execution(execution_id, update_data)
            
            if updated:
                logger.info("Updated job completion: %s - %s (execution_id: %s)", job_type, job_name, execution_id)
            else:
                logger.warning("Failed to update job execution %s, creating new record", execution_id)
                # Fallback: create new record if update failed
                job_detail = JobDetail(
                    job_id=job_id,
                    execution_id=execution_id,
                    job_name=job_name,
                    execution_start=execution_start,
                    execution_end=execution_end,
                    duration_seconds=(execution_end - execution_start).total_seconds() if execution_end and execution_start else None,
                    status=status,
                    success=success,
                    error_message=error_message,
                    error_details=None,
                    result_data=result,
                    service="ingestion",
                    metadata={"job_type": job_type, "schedule": schedule}
                )
                
                await self.job_detail_repository.log_execution(job_detail)
                logger.info("Created new job completion record: %s - %s", job_type, job_name)

        except Exception as e:
            logger.error("Failed to record job completion for %s: %s", job_type, str(e))

    async def record_job_start(self, job_type: str, job_name: str, schedule: str, execution_start: datetime) -> Optional[str]:
        """
        Record job start in audit database
        
        Args:
            job_type: Type of job
            job_name: Name of job
            schedule: Cron schedule
            execution_start: Start time
            
        Returns:
            Optional[str]: Execution ID if successful, None otherwise
        """
        if not self.job_data_repository or not self.job_detail_repository:
            logger.debug("Job repositories not available for audit logging")
            return None

        try:
            # Get the job_id (ObjectId) from job_data collection using job_type
            job_data = await self.job_data_repository.get_job(job_type)
            if not job_data:
                logger.error("Job data not found for job_type: %s", job_type)
                return None

            # Generate unique execution ID
            execution_id = f"{job_type}_{execution_start.strftime('%Y%m%d_%H%M%S')}"

            job_detail = self.create_job_detail(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=execution_start,
                execution_id=execution_id,
                job_id=str(job_data.get('_id'))
            )

            await self.job_detail_repository.log_execution(job_detail)
            logger.info("Recorded job start for %s (job_type: %s, execution_id: %s) in audit database",
                       job_name, job_type, execution_id)

            return execution_id

        except (ValueError, TypeError, OSError, RuntimeError) as e:
            logger.error("Failed to record job start for %s: %s", job_type, str(e))
            return None

    @abstractmethod
    async def execute_cron_job(self) -> Dict[str, Any]:
        """
        Abstract method for executing cron jobs - must be implemented by subclasses
        
        Returns:
            Dict[str, Any]: Execution result
        """
