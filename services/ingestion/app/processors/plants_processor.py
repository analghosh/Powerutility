"""
Plants Data Processor
Handles the processing of plants data directly into the plants collection,
bypassing the raw data collection entirely.

This processor is designed for plant master data that needs to be stored
directly in the plant_master collection without going through raw data processing.

Example Provider Configuration:
{
    "code": "plants_upload_provider",
    "name": "Plants Upload Data Provider",
    "data_type": "PLANTS",
    "ingestion_type": "UPLOAD",
    "enabled": true,
    "fields": [
        {
            "field_name": "plant_name",
            "field_type": "string",
            "is_required": true
        },
        {
            "field_name": "c_s",
            "field_type": "string",
            "is_required": false
        },
        {
            "field_name": "category",
            "field_type": "string",
            "is_required": false
        },
        {
            "field_name": "technology",
            "field_type": "string",
            "is_required": false
        },
        {
            "field_name": "price_group",
            "field_type": "string",
            "is_required": false
        },
        {
            "field_name": "ramping",
            "field_type": "float",
            "is_required": false
        },
        {
            "field_name": "tm",
            "field_type": "float",
            "is_required": false
        },
        {
            "field_name": "fc_price",
            "field_type": "float",
            "is_required": false
        },
        {
            "field_name": "px_price",
            "field_type": "float",
            "is_required": false
        },
        {
            "field_name": "vc_price",
            "field_type": "float",
            "is_required": false
        },
        {
            "field_name": "multiplier",
            "field_type": "float",
            "is_required": false
        },
        {
            "field_name": "volume",
            "field_type": "float",
            "is_required": false
        },
        {
            "field_name": "ppa",
            "field_type": "string",
            "is_required": false
        },
        {
            "field_name": "plant_id",
            "field_type": "string",
            "is_required": false
        }
    ],
    "mapping": [
        {
            "provider_field": "plant_name",
            "serve_field": "plant_name"
        },
        {
            "provider_field": "c_s",
            "serve_field": "c_s"
        },
        {
            "provider_field": "category",
            "serve_field": "category"
        },
        {
            "provider_field": "technology",
            "serve_field": "technology"
        },
        {
            "provider_field": "price_group",
            "serve_field": "price_group"
        },
        {
            "provider_field": "ramping",
            "serve_field": "ramping"
        },
        {
            "provider_field": "tm",
            "serve_field": "tm"
        },
        {
            "provider_field": "fc_price",
            "serve_field": "fc_price"
        },
        {
            "provider_field": "px_price",
            "serve_field": "px_price"
        },
        {
            "provider_field": "vc_price",
            "serve_field": "vc_price"
        },
        {
            "provider_field": "multiplier",
            "serve_field": "multiplier"
        },
        {
            "provider_field": "volume",
            "serve_field": "volume"
        },
        {
            "provider_field": "ppa",
            "serve_field": "ppa"
        },
        {
            "provider_field": "plant_id",
            "serve_field": "plant_id"
        }
    ]
}

Plants Configuration Options:
- data_type: Must be "PLANTS"
- ingestion_type: UPLOAD, API, FTP, or SCRAPE
- fields: Plant entity field definitions with validation rules
- mapping: Field mappings from provider fields to plant entity fields
- All plant entity fields are supported: plant_name, c_s, category, technology, 
  price_group, ramping, tm, fc_price, px_price, vc_price, multiplier, volume, ppa, plant_id

Validation Rules:
- Plants providers MUST have data_type = "PLANTS"
- plant_name field is required
- All other fields are optional
- Field types must match plant entity field types
- Field mappings must map to valid plant entity fields
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from libs.core.job_entities import JobDetail
from libs.utils.csv_date_parse import parse_csv_scheduled_at
from libs.core.job_factory import create_job_data_repository, create_job_detail_repository
from libs.models.provider_models import ProviderEntity, DataType
from libs.models.plant_entity import PlantEntity
from libs.services.provider_service import ProviderService
from libs.database.base_repository import BaseRepository
from libs.audit_queue.simple_logger import AuditLogger

from services.ingestion.app.core.config import settings

logger = logging.getLogger("plants_processor")


class PlantsRepository(BaseRepository):
    """Repository for plants collection operations"""
    
    def __init__(self):
        """Initialize plants repository"""
        super().__init__(
            collection_name=settings.PLANT_MASTER_COLLECTION,
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE
        )

    async def create_indexes(self):
        """Create indexes for plants collection"""
        try:
            # Drop existing indexes first to ensure cleaner state if schema changes
            to_drop = [
                "plant_id_1", 
                "plant_id_1_approval_no_1", 
                "unique_plant_id_no_approval", 
                "unique_plant_id_with_approval"
            ]
            for index_name in to_drop:
                try:
                    await self.collection.drop_index(index_name)
                except Exception:
                    pass

            # Create Compound Unique Index on (plant_id, approval_no)
            # This handles both cases:
            # 1. Base Plant (no approval_no): (plant_id, null) must be unique.
            # 2. Variant Plant (with approval_no): (plant_id, approval_no) must be unique.
            await self.collection.create_index(
                [("plant_id", 1), ("approval_no", 1)],
                unique=True,
                background=True,
                name="unique_plant_id_approval"
            )
            
            logger.info("Indexes created/verified for plant_master collection")
        except Exception as e:
            logger.error(f"Failed to create indexes for plant_master: {str(e)}")
    
    async def bulk_upsert_plants(self, plants: List[Dict[str, Any]], filter_criteria: callable) -> Dict[str, Any]:
        """
        Bulk upsert plants data with custom filter criteria
        
        Args:
            plants: List of plant data dictionaries
            filter_criteria: Function to create filter criteria for upsert
            
        Returns:
            Dict[str, Any]: Upsert results with counts
        """
        try:
            inserted_count = 0
            updated_count = 0
            
            for plant_data in plants:
                # Create filter criteria for this plant
                filter_query = filter_criteria(plant_data)
                
                # Add timestamps
                plant_data["updated_at"] = datetime.now()
                if "_id" not in plant_data:
                    plant_data["created_at"] = datetime.now()
                
                # Perform upsert
                result = await self.collection.update_one(
                    filter_query,
                    {"$set": plant_data},
                    upsert=True
                )
                
                if result.upserted_id:
                    inserted_count += 1
                elif result.modified_count > 0:
                    updated_count += 1
            
            return {
                "inserted": inserted_count,
                "updated": updated_count,
                "total": len(plants)
            }
            
        except Exception as e:
            logger.error(f"Error in bulk upsert plants: {e}")
            raise


class PlantsProcessor:
    """
    Plants Data Processor
    Processes plants data directly into the plants collection, bypassing raw data storage
    """

    def __init__(self, audit_logger: AuditLogger):
        """Initialize plants processor"""
        self.audit_logger = audit_logger
        self.provider_service = ProviderService(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE
        )
        
        self.plants_repository = PlantsRepository()
        
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

    def create_plant_upsert_filter(self, plant_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create filter criteria for plant upsert operations
        
        Args:
            plant_data: Plant data dictionary
            
        Returns:
            Dict[str, Any]: Filter criteria for upsert
        """
        # Use plant_name as the unique identifier for upsert
        return {"plant_name": plant_data.get("plant_name")}

    def validate_plants_data(self, data: List[Dict[str, Any]], provider: ProviderEntity) -> Dict[str, Any]:
        """
        Validate plants data against provider schema
        
        Args:
            data: List of plant data records to validate
            provider: Provider entity containing schema definition
            
        Returns:
            Dict[str, Any]: Validation result with success status and any errors
        """
        try:
            if not provider.fields:
                logger.warning("No schema defined for provider %s, skipping validation", provider.code)
                return {"success": True, "validated_records": len(data), "errors": []}

            schema = provider.fields
            required_fields = {field.field_name.lower() for field in schema if field.is_required}
            field_types = {field.field_name.lower(): field.field_type for field in schema}
            field_name_mapping = {field.field_name.lower(): field.field_name for field in schema}
            expected_fields = {field.field_name.lower() for field in schema}

            validation_errors = []
            validated_data = []

            for record_num, record in enumerate(data, start=1):
                try:
                    # Create case-insensitive record mapping
                    case_insensitive_record = {key.lower(): value for key, value in record.items()}

                    # Check required fields
                    missing_fields = required_fields - set(case_insensitive_record.keys())
                    if missing_fields:
                        missing_fields_original = {field_name_mapping.get(field, field) for field in missing_fields}
                        missing_fields_filtered = [f for f in missing_fields_original if f is not None]
                        validation_errors.append(f"Record {record_num}: Missing required fields: {', '.join(missing_fields_filtered)}")
                        continue

                    # Check for unexpected fields (warn but don't fail)
                    unexpected_fields = set(case_insensitive_record.keys()) - expected_fields
                    if unexpected_fields:
                        logger.warning("Record %d: Unexpected fields found: %s", record_num, ', '.join(unexpected_fields))

                    # Convert and validate field types
                    converted_record = {}
                    for key, value in case_insensitive_record.items():
                        if key in field_types:
                            original_key = field_name_mapping.get(key, key)
                            try:
                                converted_record[original_key] = self._convert_value_with_type(value, field_types[key])
                            except (ValueError, TypeError) as e:
                                validation_errors.append(f"Record {record_num}: Invalid type for field '{original_key}': {str(e)}")
                                break
                        elif key in expected_fields:
                            # Field exists in schema but no type specified
                            original_key = field_name_mapping.get(key, key)
                            converted_record[original_key] = value
                    else:
                        # Only add record if no type conversion errors occurred
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
                logger.error("Plants data validation failed: %s", "; ".join(validation_errors[:5]))  # Log first 5 errors

            return result

        except Exception as e:
            logger.error("Error during plants data validation: %s", str(e))
            return {
                "success": False,
                "validated_records": 0,
                "total_records": len(data),
                "errors": [f"Plants data validation error: {str(e)}"],
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
                return parse_csv_scheduled_at(value)
            else:
                # Unknown type, return as string
                logger.warning("Unknown field type '%s', treating as string", field_type)
                return str(value)

        except (ValueError, TypeError) as e:
            raise ValueError(f"Cannot convert '{value}' to {field_type}: {str(e)}")

    def map_data_to_schema(self, data: List[Dict[str, Any]], provider: ProviderEntity) -> List[Dict[str, Any]]:
        """
        Map plants data to provider schema format
        
        Args:
            data: Raw plant data records
            provider: Provider entity containing schema and mapping configuration
            
        Returns:
            List[Dict[str, Any]]: Mapped plant data records
        """
        try:
            if not provider.fields:
                logger.warning("No schema defined for provider %s, returning data as-is", provider.code)
                return data

            # Get field mappings from provider.mapping field
            field_mappings = {}
            if provider.mapping:
                for mapping_obj in provider.mapping:
                    field_mappings[mapping_obj.serve_field] = mapping_obj.provider_field
            
            mapped_data = []
            for record in data:
                mapped_record = {}
                
                for field in provider.fields:
                    field_name = field.field_name
                    
                    # Check if there's a custom mapping for this field
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

            logger.info("Mapped %d plant records to schema format", len(mapped_data))
            return mapped_data

        except Exception as e:
            logger.error("Error mapping plants data to schema: %s", str(e))
            return data  # Return original data if mapping fails

    async def process_plants_data(self, data: List[Dict[str, Any]], provider: ProviderEntity) -> Dict[str, Any]:
        """
        Process plants data directly into the plants collection
        
        Args:
            data: List of plant data records
            provider: Provider entity containing schema and mapping configuration
            
        Returns:
            Dict[str, Any]: Processing result
        """
        try:
            logger.info("Processing %d plant records for provider %s", len(data), provider.code)
            
            # Validate data against schema
            validation_result = self.validate_plants_data(data, provider)
            if not validation_result["success"]:
                return {
                    "success": False,
                    "message": "Data validation failed",
                    "errors": validation_result["errors"],
                    "processed_records": 0
                }
            
            # Map data to schema
            mapped_data = self.map_data_to_schema(validation_result["validated_data"], provider)
            
            if not mapped_data:
                return {
                    "success": False,
                    "message": "No valid data after mapping",
                    "processed_records": 0
                }
            
            # Store directly in plants collection
            result = await self.plants_repository.bulk_upsert_plants(
                plants=mapped_data,
                filter_criteria=self.create_plant_upsert_filter
            )
            
            logger.info("Successfully processed %d plant records: %d inserted, %d updated", 
                       result["total"], result["inserted"], result["updated"])
            
            return {
                "success": True,
                "message": "Plants data processed successfully",
                "processed_records": result["total"],
                "inserted": result["inserted"],
                "updated": result["updated"],
                "provider_code": provider.code
            }
            
        except Exception as e:
            logger.error("Error processing plants data: %s", str(e))
            return {
                "success": False,
                "message": f"Error processing plants data: {str(e)}",
                "processed_records": 0
            }

    def create_job_detail(self, job_type: str, job_name: str, schedule: str, 
                         execution_start: datetime, execution_id: str, job_id: str) -> JobDetail:
        """
        Create a job detail record for audit logging
        
        Args:
            job_type: Type of job (e.g., 'plants_processing')
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

    async def execute_cron_job(self) -> Dict[str, Any]:
        """
        Execute plants processing cron job
        
        Returns:
            Dict[str, Any]: Execution result
        """
        # This method can be implemented for scheduled plants data processing
        # For now, it's a placeholder for future cron job functionality
        logger.info("Plants cron job execution not implemented yet")
        return {
            "success": True,
            "message": "Plants cron job execution not implemented yet",
            "processed_records": 0
        }
