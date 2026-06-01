"""
Limit Values Data Processor
Handles the processing of limit values data through an approval workflow.
Files are uploaded through ingestion service and staged for approval before being applied to limit_values collection.

This processor is designed for limit values data that requires approval before being stored
in the limit_values collection.

Example Provider Configuration:
{
    "code": "limit_values_upload_provider",
    "name": "Limit Values Upload Data Provider",
    "data_type": "LIMIT_VALUES",
    "ingestion_type": "UPLOAD",
    "enabled": true,
    "fields": [
        {
            "field_name": "scheduled_at",
            "field_type": "string",
            "is_required": true
        },
        {
            "field_name": "block",
            "field_type": "integer",
            "is_required": true
        },
        {
            "field_name": "id_type",
            "field_type": "string",
            "is_required": true
        },
        {
            "field_name": "values",
            "field_type": "float",
            "is_required": false
        },
        {
            "field_name": "market",
            "field_type": "string",
            "is_required": false
        },
        {
            "field_name": "category",
            "field_type": "string",
            "is_required": false
        },
        {
            "field_name": "area",
            "field_type": "string",
            "is_required": false
        },
        {
            "field_name": "unit",
            "field_type": "string",
            "is_required": false
        },
        {
            "field_name": "action",
            "field_type": "string",
            "is_required": false
        },
        {
            "field_name": "comment",
            "field_type": "string",
            "is_required": false
        }
    ],
    "mapping": [
        {
            "provider_field": "scheduled_at",
            "serve_field": "scheduled_at"
        },
        {
            "provider_field": "block",
            "serve_field": "block"
        },
        {
            "provider_field": "id_type",
            "serve_field": "id_type"
        },
        {
            "provider_field": "values",
            "serve_field": "values"
        },
        {
            "provider_field": "market",
            "serve_field": "market"
        },
        {
            "provider_field": "category",
            "serve_field": "category"
        },
        {
            "provider_field": "area",
            "serve_field": "area"
        },
        {
            "provider_field": "unit",
            "serve_field": "unit"
        },
        {
            "provider_field": "action",
            "serve_field": "action"
        },
        {
            "provider_field": "comment",
            "serve_field": "comment"
        }
    ]
}

Limit Values Configuration Options:
- data_type: Must be "LIMIT_VALUES"
- ingestion_type: UPLOAD
- fields: Limit values field definitions with validation rules
- mapping: Field mappings from provider fields to limit values fields
- All limit values fields are supported: scheduled_at, block, id_type, values, market, category, area, unit, action, comment

Validation Rules:
- Limit values providers MUST have data_type = "LIMIT_VALUES"
- scheduled_at, block, id_type fields are required
- block must be between 1-96 (15-minute intervals for 24 hours)
- action must be UPSERT or DELETE
- values is required for UPSERT action
- scheduled_at must be in YYYY-MM-DD format
"""

import asyncio
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from io import StringIO
import csv
import json

from ..domain.entities import ProviderEntity
from ..services.provider_service import ProviderManagementService
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '../../..'))

from services.processing.app.v2.repository.limit_workflow_repository import limit_workflow_repository
from services.processing.app.core.config import settings as processing_settings
from libs.audit_queue.simple_logger import AuditLogger

logger = logging.getLogger(__name__)


class LimitValuesRepository:
    """
    Repository for limit values approval workflow operations
    """
    
    def __init__(self):
        self.flow_repo = limit_workflow_repository
        self.approval_collection = processing_settings.MONGODB_LIMIT_APPROVAL_COLLECTION
        self.audit_collection = processing_settings.MONGODB_LIMIT_AUDIT_COLLECTION

    def create_approval_request(self, approval_doc: Dict[str, Any]) -> Any:
        """
        Create approval request in limit_approval collection
        
        Args:
            approval_doc: Approval document to create
            
        Returns:
            ObjectId of created approval request
        """
        try:
            return self.flow_repo.create_approval(self.approval_collection, approval_doc)
        except Exception as e:
            logger.error(f"Error creating approval request: {e}")
            raise

    def write_audit_log(self, audit_doc: Dict[str, Any]) -> None:
        """
        Write audit log entry
        
        Args:
            audit_doc: Audit document to write
        """
        try:
            self.flow_repo.write_audit(self.audit_collection, audit_doc)
        except Exception as e:
            logger.error(f"Error writing audit log: {e}")
            raise


class LimitValuesProcessor:
    """
    Limit Values Data Processor
    Processes limit values data through approval workflow before storing in limit_values collection
    """

    def __init__(self, audit_logger: AuditLogger):
        """Initialize limit values processor"""
        self.audit_logger = audit_logger
        self.provider_service = ProviderManagementService()
        
        self.limit_values_repository = LimitValuesRepository()
        
        # Initialize job repositories for audit logging
        try:
            from libs.core.job_factory import create_job_data_repository, create_job_detail_repository
            self.job_data_repository = create_job_data_repository(
                getattr(processing_settings, 'AUDIT_DB_URI', processing_settings.MONGODB_URI),
                getattr(processing_settings, 'AUDIT_DB_NAME', 'audit_db'),
                getattr(processing_settings, 'JOB_DATA_COLLECTION', 'job_data')
            )
            self.job_detail_repository = create_job_detail_repository(
                getattr(processing_settings, 'AUDIT_DB_URI', processing_settings.MONGODB_URI),
                getattr(processing_settings, 'AUDIT_DB_NAME', 'audit_db'),
                getattr(processing_settings, 'JOB_DETAIL_COLLECTION', 'job_details')
            )
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.warning("Could not initialize job repositories for audit logging: %s", str(e))
            self.job_data_repository = None
            self.job_detail_repository = None

    def validate_limit_values_data(self, data: List[Dict[str, Any]], provider: ProviderEntity) -> Dict[str, Any]:
        """
        Validate limit values data against schema and business rules
        
        Args:
            data: List of limit values data records
            provider: Provider entity containing schema and mapping configuration
            
        Returns:
            Dict containing validation results with validated_data and errors
        """
        try:
            logger.info("Validating %d limit values records for provider %s", len(data), provider.code)
            
            validated_data = []
            errors = []
            
            # Get required fields from provider configuration
            required_fields = [field.field_name for field in provider.fields if field.is_required]
            allowed_actions = {"UPSERT", "DELETE"}
            
            for i, record in enumerate(data):
                row_errors = []
                
                # Check required fields
                for field in required_fields:
                    if field not in record or record[field] is None or record[field] == "":
                        row_errors.append(f"Missing required field: {field}")
                
                # Business rule validations
                try:
                    # Validate scheduled_at format
                    if "scheduled_at" in record and record["scheduled_at"]:
                        datetime.strptime(str(record["scheduled_at"]), "%Y-%m-%d")
                    
                    # Validate block range (1-96 for 15-minute intervals for 24 hours)
                    if "block" in record and record["block"]:
                        block = int(record["block"])
                        if not (1 <= block <= 96):
                            row_errors.append(f"Block must be between 1-96, got {block}")
                    
                    # Validate action
                        # Normalize action to uppercase
                        action = str(record["action"]).upper()
                        # Map INSERT to UPSERT
                        if action == "INSERT":
                            action = "UPSERT"
                            # Update the record with normalized action
                            record["action"] = action
                            
                        if action not in allowed_actions:
                            row_errors.append(f"Invalid action '{action}'. Must be UPSERT or DELETE")
                    
                    # Validate values for UPSERT
                    if record.get("action", "UPSERT").upper() == "UPSERT":
                        if "values" not in record or record["values"] is None:
                            row_errors.append("Values field is required for UPSERT action")
                        elif record["values"] is not None:
                            try:
                                float(record["values"])
                                if float(record["values"]) < 0:
                                    row_errors.append("Values must be non-negative")
                            except (ValueError, TypeError):
                                row_errors.append("Values must be a valid number")
                    
                    # Validate id_type
                    if "id_type" in record and record["id_type"]:
                        # Removed strict validation against fixed list {PLANT, ZONE, STATE, REGION}
                        # to allow dynamic types like ocfPremiumDiscountPrice
                        pass
                
                except Exception as e:
                    row_errors.append(f"Validation error: {str(e)}")
                
                if row_errors:
                    errors.append({
                        "row": i + 1,
                        "data": record,
                        "errors": row_errors
                    })
                else:
                    # Add default values
                    record_with_defaults = {
                        "market": record.get("market", "RTM"),
                        "category": record.get("category", "Limits"),
                        "area": record.get("area", ""),
                        "action": record.get("action", "UPSERT").upper(),
                        **record
                    }
                    validated_data.append(record_with_defaults)
            
            validation_result = {
                "success": len(errors) == 0,
                "validated_data": validated_data,
                "errors": errors,
                "total_records": len(data),
                "valid_records": len(validated_data),
                "invalid_records": len(errors)
            }
            
            logger.info("Validation completed: %d valid, %d invalid records", 
                       len(validated_data), len(errors))
            
            return validation_result
            
        except Exception as e:
            logger.error("Error validating limit values data: %s", str(e))
            return {
                "success": False,
                "validated_data": [],
                "errors": [{"message": f"Validation error: {str(e)}"}],
                "total_records": len(data),
                "valid_records": 0,
                "invalid_records": len(data)
            }

    def map_data_to_schema(self, data: List[Dict[str, Any]], provider: ProviderEntity) -> List[Dict[str, Any]]:
        """
        Map provider data to internal schema format
        
        Args:
            data: List of validated data records
            provider: Provider entity containing mapping configuration
            
        Returns:
            List of mapped data records
        """
        try:
            logger.info("Mapping %d limit values records to schema format", len(data))
            
            mapped_data = []
            
            for record in data:
                mapped_record = {}
                
                # Apply field mappings if available
                if provider.mapping:
                    for mapping in provider.mapping:
                        # Handle both dictionary and object (Pydantic model) access
                        if isinstance(mapping, dict):
                            provider_field = mapping.get("provider_field")
                            serve_field = mapping.get("serve_field")
                        else:
                            provider_field = getattr(mapping, "provider_field", None)
                            serve_field = getattr(mapping, "serve_field", None)
                        
                        if provider_field in record and serve_field:
                            mapped_record[serve_field] = record[provider_field]
                        elif provider_field in record:
                            # If no serve_field specified, use provider_field as is
                            mapped_record[provider_field] = record[provider_field]
                else:
                    # If no mapping specified, use data as-is
                    mapped_record = record.copy()
                
                
                # Ensure 'id' field is populated from 'id_type' if missing
                # DB schema uses 'id' but provider sends 'id_type'
                if "id" not in mapped_record and "id_type" in mapped_record:
                    mapped_record["id"] = mapped_record["id_type"]
                
                mapped_data.append(mapped_record)
            
            logger.info("Mapped %d limit values records to schema format", len(mapped_data))
            return mapped_data
            
        except Exception as e:
            logger.error("Error mapping limit values data to schema: %s", str(e))
            return data  # Return original data if mapping fails

    async def process_limit_values_data(self, data: List[Dict[str, Any]], provider: ProviderEntity, 
                                      upload_id: str, file_name: str, actor: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process limit values data through approval workflow
        
        Args:
            data: List of limit values data records
            provider: Provider entity containing schema and mapping configuration
            upload_id: Upload identifier for tracking
            file_name: Original file name
            actor: User information for audit logging
            
        Returns:
            Dict[str, Any]: Processing result with approval request ID
        """
        try:
            logger.info("Processing %d limit values records for provider %s", len(data), provider.code)
            
            # Validate data against schema
            validation_result = self.validate_limit_values_data(data, provider)
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
            
            # Process each record individually to create granular approval requests
            now = datetime.now()
            # Generate a batch_id for this upload session
            # using bson.ObjectId if available, else standard uuid
            try:
                from bson import ObjectId
                batch_id = str(ObjectId())
            except ImportError:
                import uuid
                batch_id = str(uuid.uuid4())

            actor_id = actor.get("user_id", "anonymous")
            actor_username = actor.get("username", "anonymous")
            correlation_id = actor.get("correlation_id", "unknown")
            
            success_count = 0
            error_count = 0
            
            # Helper to manage upserts using the repository
            # effectively mimicking _stage_items logic from limit_values_services.py
            
            for item in mapped_data:
                try:
                    # Clean item structure
                    # Ensure 'values' is used as 'value' if service expects it, or keep 'values'
                    # The service code uses: "values": approval.get("value", approval.get("values"))
                    # So both are fine. We will stick to 'values' as per schema unless mapping changed.
                    
                    # Ensure unique composite key fields are present
                    # UNIQUE_KEY_FIELDS = ["block", "id", "market", "category", "area", "scheduled_at"]
                    
                    # Normalize date
                    if "scheduled_at" in item:
                        # Ensure it's a datetime object or proper string
                        # ideally datetime object for MongoDB
                        if isinstance(item["scheduled_at"], str):
                            try:
                                item["scheduled_at"] = datetime.strptime(item["scheduled_at"], "%Y-%m-%d")
                            except ValueError:
                                pass # Keep as is if parse fails, validation likely caught it or it's different format
                    
                    # Prepare filter doc for upsert (finding existing pending request)
                    filter_doc = {
                        "block": int(item.get("block")),
                        "id": str(item.get("id")),
                        "market": item.get("market", "DAM"),
                        "category": item.get("category", "Limits"),
                        "area": item.get("area", "Puducherry"), # defaulting based on sample
                        "scheduled_at": item.get("scheduled_at")
                    }
                    
                    # Build approval doc
                    # Top level fields required: status, request_type, title, etc.
                    # AND the data fields at top level: block, id, values, etc.
                    
                    update_payload = {
                        "$set": {
                            "status": "PENDING",
                            "request_type": "FILE_UPLOAD",
                            "title": file_name,
                            "description": f"Uploaded via ingestion service by {actor_username}",
                            "last_edited_at": now,
                            "last_edited_by": actor_id,
                            "batch_id": batch_id,
                            "file": {
                                "filename": file_name,
                                "upload_id": upload_id,
                                "provider_code": provider.code
                            },
                            "correlation_id": correlation_id,
                            # Data fields
                            "block": int(item.get("block")),
                            "id": str(item.get("id")),
                            "market": item.get("market"),
                            "category": item.get("category"),
                            "area": item.get("area"),
                            "values": item.get("values"), # Store as values
                            "value": item.get("values"),  # Also store as value for compatibility
                            "scheduled_at": item.get("scheduled_at"),
                            "comment": item.get("comment"),
                            "action": "UPSERT" 
                        },
                        "$setOnInsert": {
                            "requested_at": now,
                            "requested_by": actor_id,
                            "requested_username": actor_username,
                            "created_at": now,
                            "created_by": actor_id,
                        }
                    }
                    
                    # Perform Upsert
                    # We need to access the underlying upsert logic.
                    # LimitValuesRepository.create_approval_request uses .create_approval which is usually an insert.
                    # We need upsert_one.
                    # Assuming self.limit_values_repository.flow_repo has upsert_one (checked via service code)
                    
                    self.limit_values_repository.flow_repo.upsert_one(
                        self.limit_values_repository.approval_collection,
                        filter_doc,
                        update_payload
                    )
                    
                    success_count += 1
                    
                except Exception as ex:
                    logger.error(f"Error processing item row: {ex}")
                    error_count += 1

            
            # Write single audit log for the batch/file
            self.limit_values_repository.write_audit_log({
                "ts": now,
                "actor_id": actor_id,
                "actor_username": actor_username,
                "action": "UPLOAD_PROCESSED_BATCH",
                "entity_type": "limit_approval_batch",
                "entity_id": batch_id,
                "operation": "BATCH_UPSERT",
                "correlation_id": correlation_id,
                "upload_id": upload_id,
                "provider_code": provider.code,
                "counts": {
                    "total": len(mapped_data),
                    "success": success_count,
                    "failed": error_count
                },
                "success": True
            })
            
            logger.info("Successfully staged %d records in batch %s", success_count, batch_id)
            
            return {
                "success": True,
                "message": "Limit values data staged for approval",
                "batch_id": batch_id,
                "approval_id": batch_id,
                "total_records": len(data),
                "valid_records": len(mapped_data),
                "staged_records": success_count,
                "failed_records": error_count,
                "invalid_records": error_count,
                "status": "PENDING_APPROVAL"
            }
            
        except Exception as e:
            logger.error("Error processing limit values data: %s", str(e))
            return {
                "success": False,
                "message": f"Processing error: {str(e)}",
                "processed_records": 0
            }

    async def process_limit_values_file(self, file_path: str, provider: ProviderEntity, 
                                      upload_id: str, actor: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process limit values file and create approval request
        
        Args:
            file_path: Path to uploaded file
            provider: Provider entity
            upload_id: Upload identifier
            actor: User information
            
        Returns:
            Processing result
        """
        try:
            logger.info("Processing limit values file: %s", file_path)
            
            # Read file content
            with open(file_path, 'r', encoding='utf-8') as file:
                csv_content = file.read()
            
            # Parse CSV content
            csv_reader = csv.DictReader(StringIO(csv_content))
            data = [dict(row) for row in csv_reader]
            
            if not data:
                return {
                    "success": False,
                    "message": "No data found in file",
                    "processed_records": 0
                }
            
            # Process the data
            return await self.process_limit_values_data(data, provider, upload_id, 
                                                      file_path.split('/')[-1], actor)
            
        except Exception as e:
            logger.error("Error processing limit values file: %s", str(e))
            return {
                "success": False,
                "message": f"File processing error: {str(e)}",
                "processed_records": 0
            }
