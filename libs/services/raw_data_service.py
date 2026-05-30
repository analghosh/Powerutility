"""
Raw Data Service - Schema-Driven Universal Storage
Handles all raw data types with metadata-based differentiation and schema validation
"""

import csv
import io
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

from bson import ObjectId

from libs.models.raw_data_entity import RawDataEntity
from libs.models.raw_data_processing_status_entity import RawDataProcessingStatusEntity
from libs.repository.raw_data_repository import RawDataRepository
from libs.repository.raw_data_processing_status_repository import RawDataProcessingStatusRepository
from libs.enum.raw_data_status import RawDataStatus

# Provider-based schema validation - no metadata_config needed

logger = logging.getLogger(__name__)


class RawDataService:
    """
    Schema-driven raw data service for universal data storage
    Supports any data type based on metadata and schema configuration
    """
    
    def __init__(self, mongodb_uri: str, database_name: str):
        """
        Initialize raw data service
        
        Args:
            mongodb_uri: MongoDB connection URI
            database_name: Database name
        """
        self.mongodb_uri = mongodb_uri
        self.database_name = database_name
        # Cache for dynamic repositories: key=data_type, value=(raw_repo, status_repo)
        self._repository_cache = {}
        
        # Default legacy repositories (fallback)
        self.repository = RawDataRepository(mongodb_uri, database_name)
        self.processing_status_repository = RawDataProcessingStatusRepository(mongodb_uri, database_name)

    def _get_repositories(self, data_type: Optional[str] = None):
        """
        Get repositories for specific data type
        
        Args:
            data_type: Data type (e.g., 'demand', 'supply')
            
        Returns:
            Tuple[RawDataRepository, RawDataProcessingStatusRepository]: Component repositories
        """
        if not data_type:
            return self.repository, self.processing_status_repository
            
        data_type = data_type.lower()
        if data_type in self._repository_cache:
            return self._repository_cache[data_type]
            
        # Create new repositories for this data type
        raw_repo = RawDataRepository(self.mongodb_uri, self.database_name, data_type)
        status_repo = RawDataProcessingStatusRepository(self.mongodb_uri, self.database_name, data_type)
        
        self._repository_cache[data_type] = (raw_repo, status_repo)
        return raw_repo, status_repo
    
    async def store_raw_data(
        self,
        raw_data: List[Dict[str, Any]],
        provider: Any,
        additional_metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Store raw data with provided schema and provider details
        """
        try:
            # Get repositories for this data type
            data_type = provider.data_type
            raw_repo, status_repo = self._get_repositories(data_type)
            
            logger.info("Processing raw data: type=%s, provider=%s, records=%d", 
                       provider.data_type, provider.code, len(raw_data))
            schema = provider.fields
            # Step 1: Use provided schema from caller (provider collection)
            is_schema_optional = False
            if hasattr(provider, 'data_type') and provider.data_type:
                data_type_str = str(provider.data_type).lower()
                is_schema_optional = "exchange_trade" in data_type_str or "exchange_dsm" in data_type_str
                
            if not schema and not is_schema_optional:
                raise ValueError("Schema must be provided from provider collection")
            
            # Step 2: Data is already validated by caller - focus on storage
            validation_result = {
                "valid": True,
                "errors": [],
                "warnings": [],
                "validated_records": len(raw_data),
                "total_records": len(raw_data),
                "schema_compliance": "validated_by_caller"
            }
            
            # Step 3: Create comprehensive metadata with validation results and provider details
            metadata = self._create_metadata(
                total_records=len(raw_data),
                validation_result=validation_result,
                additional_metadata=additional_metadata or {},
                provider=provider
            )
            
            # Step 4: Create raw data entity
            integration_apilog_id = additional_metadata.get('integration_apilog_id') if additional_metadata else None
            
            entity = RawDataEntity(
                data_type=provider.data_type.lower(),
                provider_id=str(provider.id),
                raw_data=raw_data,
                metadata=metadata,
                created_at=datetime.now(),
                updated_at=datetime.now(),
                integration_apilog_id=integration_apilog_id
            )
            
            # Step 5: Store in database (time series collection)
            document_id = await raw_repo.create_raw_data(entity)
            
            # Step 6: Create processing status record (normal collection) - DUAL COLLECTION ARCHITECTURE
            processing_status_id = None
            try:
                processing_status_entity = RawDataProcessingStatusEntity(
                    raw_data_id=document_id,
                    provider_id=str(provider.id),
                    status=RawDataStatus.PENDING,  # Always start as PENDING when stored
                    processing_started_at=None,  # Not started yet
                    processing_completed_at=None,  # Not completed yet
                    error_message=None,  # No error initially
                    processing_metadata={
                        "total_records": len(raw_data)
                    },
                    retry_count=0
                )
                
                processing_status_id = await status_repo.create_processing_status(processing_status_entity)
                logger.info("Created processing status record: %s for raw_data_id: %s (collection: %s)", 
                           processing_status_id, document_id, status_repo.collection_name)
                
            except Exception as status_error:
                logger.error("Failed to create processing status record for raw_data_id %s: %s",
                           document_id, status_error, exc_info=True)
                # Continue even if status record creation fails - raw data is still stored
            
            logger.info("Successfully stored validated raw data: document_id=%s, processing_status_id=%s, records=%d", 
                       document_id, processing_status_id, len(raw_data))
            
            return {
                "success": True,
                "document_id": document_id,
                "processing_status_id": processing_status_id,
                "total_records": len(raw_data)
            }
            
        except Exception as e:
            logger.error("Error processing raw data: %s", e, exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_raw_data_by_ids(self, raw_data_ids: List[str], data_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get raw data records by their IDs
        """
        raw_repo, _ = self._get_repositories(data_type)
        return await raw_repo.get_raw_data_by_ids(raw_data_ids)

    async def get_processing_status_by_upload_id(self, upload_id: str, data_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Get processing status by upload tracking ID
        
        Args:
            upload_id: Upload tracking ID
            data_type: Optional data type to target specific collection
            
        Returns:
            Dict[str, Any]: Processing status record or None
        """
        logger.info("Fetching processing status for upload_id: %s (type: %s)", upload_id, data_type)
        
        raw_repo, status_repo = self._get_repositories(data_type)
        
        # 1. Find raw data record by upload_id in metadata
        raw_data = await raw_repo.get_by_upload_id(upload_id)
        if not raw_data:
            logger.warning("No raw data record found for upload_id: %s in %s", upload_id, raw_repo.collection_name)
            return None
        
        # 2. Get processing status record by raw_data_id
        raw_data_id = raw_data.get("_id")
        logger.info("Found raw_data record with ID: %s for upload_id: %s", raw_data_id, upload_id)
        
        status_record = await status_repo.get_by_raw_data_id(raw_data_id)
        if not status_record:
            logger.warning("No processing status record found for raw_data_id: %s in %s", 
                          raw_data_id, status_repo.collection_name)
            return None
            
        logger.info("Found processing status record for raw_data_id: %s", raw_data_id)
        return status_record
    
    
    async def get_by_id(self, record_id: str) -> Optional[Dict[str, Any]]:
        """Get raw data by document ID"""
        return await self.repository.get_by_id(record_id)
        
    def _parse_csv_content(self, csv_content: str) -> List[Dict[str, Any]]:
        """
        Parse CSV content into list of dictionaries
        
        Args:
            csv_content: Raw CSV content
            
        Returns:
            List[Dict[str, Any]]: Parsed CSV data
        """
        try:
            csv_file = io.StringIO(csv_content)
            reader = csv.DictReader(csv_file)
            
            data = []
            for row in reader:
                # Convert values based on basic type inference
                converted_row = {}
                for key, value in row.items():
                    converted_row[key] = self._convert_value(value)
                data.append(converted_row)
            
            return data
            
        except (ValueError, TypeError, OSError, RuntimeError) as e:
            logger.error("Error parsing CSV content: %s", e)
            return []
    
    def _convert_value(self, value: str) -> Any:
        """
        Convert string value to appropriate type
        
        Args:
            value: String value from CSV
            
        Returns:
            Any: Converted value
        """
        if not value or value.strip() == "":
            return None
        
        value = value.strip()
        
        # Try integer
        try:
            return int(value)
        except ValueError:
            pass
        
        # Try float
        try:
            return float(value)
        except ValueError:
            pass
        
        # Try boolean
        if value.lower() in ("true", "false"):
            return value.lower() == "true"
        
        # Return as string
        return value
    
    
    
    def _create_metadata(
        self,
        total_records: int,
        validation_result: Dict[str, Any],
        additional_metadata: Optional[Dict[str, Any]] = None,
        provider: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Create metadata for raw data entity
        
        Returns:
            Dict[str, Any]: Metadata dictionary
        """
        metadata = {
            "total_records": total_records,
            "validation_result": validation_result,
            "validation_status": "passed" if validation_result["valid"] else "failed"
        }
        
        # Add source from provider metadata if available
        if provider and hasattr(provider, 'metadata') and provider.metadata:
            source = provider.metadata.get('source')
            if source:
                metadata["source"] = source
                logger.debug("Added source to metadata: %s", source)
        
        # Add additional metadata if provided
        if additional_metadata:
            metadata.update(additional_metadata)
        
        return metadata
