"""
Provider Management Service
Ingestion-specific provider management operations
"""

import logging
from typing import Any, Dict, List, Optional

from ..core.config import settings
from libs.models.provider_models import (
    ProviderEntity, CreateProviderRequest, UpdateProviderRequest, 
    ProviderResponse, ProviderListResponse, ProviderSummary, IngestionType, DataType
)
from libs.services.provider_service import ProviderService as BaseProviderService
from ..repositories.timeblock_repository import TimeblockRepository


logger = logging.getLogger(__name__)


class ProviderManagementService:
    """Service for provider management operations (ingestion-specific)"""
    
    def __init__(self):
        """Initialize the provider management service"""
        try:
            # Use the shared provider service for read operations
            self.provider_service = BaseProviderService(
                mongodb_uri=settings.MONGODB_URI,
                database_name=settings.MONGODB_DATABASE
            )
            
            self.timeblock_repository = TimeblockRepository(
                mongodb_uri=settings.MONGODB_URI,
                database_name=settings.MONGODB_DATABASE
            )
            
            # Direct access to base service attributes
            self.serve_collection_columns = self.provider_service.serve_collection_columns
            self.repository = self.provider_service.repository
            
            logger.info("ProviderManagementService initialized successfully")
            
        except Exception as e:
            logger.error("Failed to initialize ProviderManagementService: %s", e)
            raise
        
        
    
    def _validate_schema(self, schema: List, data_type: Optional[DataType] = None, ingestion_type: Optional[IngestionType] = None) -> None:
        """Validate schema fields with proper FieldType and ValidationRule validations"""
        if not schema:
            # Allow empty schema for API Exchange Trade and Exchange DSM providers
            if ingestion_type == IngestionType.API and data_type in [DataType.EXCHANGE_TRADE, DataType.EXCHANGE_DSM]:
                logger.info("Skipping empty schema validation for API %s provider", data_type.value)
                return
            raise ValueError("Schema cannot be empty")
        
        schema_field_names = set()
        
        for i, field in enumerate(schema):
            # Validate field name
            if not hasattr(field, 'field_name') or not field.field_name:
                raise ValueError(f"Schema field at index {i} must have a field_name")
            
            field_name = field.field_name.strip()
            if not field_name:
                raise ValueError(f"Schema field at index {i} field_name cannot be empty")
            
            # Check for duplicate field names
            if field_name in schema_field_names:
                raise ValueError(f"Duplicate field name '{field_name}' found in schema")
            schema_field_names.add(field_name)
            
            # Validate field type
            if not hasattr(field, 'field_type'):
                raise ValueError(f"Field '{field_name}' must have a field_type")
            
            # Validate field type is valid enum value
            from libs.models.provider_models import FieldType
            try:
                FieldType(field.field_type)
            except ValueError:
                valid_types = [t.value for t in FieldType]
                raise ValueError(f"Field '{field_name}' has invalid field_type '{field.field_type}'. Valid types: {valid_types}")

            # Validate validation rules if present
            if hasattr(field, 'validation') and field.validation:
                self._validate_field_validation_rules(field_name, field.validation, field.field_type)
    
    def _validate_field_mappings(self, schema: List, mappings: List, data_type: DataType, ingestion_type: Optional[IngestionType] = None) -> None:
        """Validate field mappings against schema and serve collection columns with 1:1 mapping enforcement"""
        if not mappings:
            return
        
        # Skip serve table validation for API providers since they only store in raw data
        if ingestion_type == IngestionType.API:
            logger.info("Skipping serve table validation for API provider - only raw data storage")
            return
        
        # Skip serve table validation for plants data type since it goes directly to plant_master collection
        if data_type == DataType.PLANTS:
            logger.info("Skipping serve table validation for plants data type - direct storage in plant_master collection")
            return
        
        schema_field_names = {field.field_name for field in schema}
        serve_columns = set(self.get_serve_columns_for_data_type(data_type))
        mapping_combinations = set()
        used_source_fields = set()
        used_target_fields = set()
        
        for i, mapping in enumerate(mappings):
            # Validate required fields
            if not hasattr(mapping, 'provider_field') or not mapping.provider_field:
                raise ValueError(f"Mapping {i} must have a provider_field")
            
            if not hasattr(mapping, 'serve_field') or not mapping.serve_field:
                raise ValueError(f"Mapping {i} must have a serve_field")
            
            # Check if provider field exists in schema
            if mapping.provider_field not in schema_field_names:
                raise ValueError(f"Mapping {i}: Provider field '{mapping.provider_field}' not found in schema")
            
            # Check if serve field exists in serve collection columns
            if mapping.serve_field not in serve_columns:
                valid_columns = sorted(list(serve_columns))
                raise ValueError(f"Mapping {i}: Serve field '{mapping.serve_field}' not found in {data_type.value} serve collection. Valid columns: {valid_columns}")
            
            
            # Create mapping combination key
            mapping_key = (mapping.provider_field, mapping.serve_field)
            
            # Check for duplicate mappings
            if mapping_key in mapping_combinations:
                raise ValueError(f"Duplicate mapping found: provider_field '{mapping.provider_field}' -> serve_field '{mapping.serve_field}'")
            
            # Check for 1:1 mapping violations (provider field used multiple times)
            if mapping.provider_field in used_source_fields:
                raise ValueError(f"1:1 mapping violation: Provider field '{mapping.provider_field}' is mapped to multiple serve fields")
            
            # Check for 1:1 mapping violations (serve field used multiple times)
            if mapping.serve_field in used_target_fields:
                raise ValueError(f"1:1 mapping violation: Serve field '{mapping.serve_field}' is mapped from multiple provider fields")
            
            mapping_combinations.add(mapping_key)
            used_source_fields.add(mapping.provider_field)
            used_target_fields.add(mapping.serve_field)
    
    def _validate_field_validation_rules(self, field_name: str, validation_rules: List, field_type: str) -> None:
        """Validate field validation rules"""
        from libs.models.provider_models import ValidationRuleType, FieldType
        
        for i, rule in enumerate(validation_rules):
            # Validate rule type
            if not hasattr(rule, 'rule'):
                raise ValueError(f"Validation rule {i} for field '{field_name}' must have a rule")
            
            try:
                ValidationRuleType(rule.rule)
            except ValueError:
                valid_rules = [r.value for r in ValidationRuleType]
                raise ValueError(f"Field '{field_name}' validation rule {i} has invalid rule '{rule.rule}'. Valid rules: {valid_rules}")
            
            # Validate rule value based on rule type
            if not hasattr(rule, 'value'):
                raise ValueError(f"Validation rule {i} for field '{field_name}' must have a value")
            
            self._validate_rule_value(field_name, rule.rule, rule.value, field_type, i)
    
    def _validate_rule_value(self, field_name: str, rule_type: str, rule_value: Any, field_type: str, rule_index: int) -> None:
        """Validate rule value based on rule type and field type"""
        from libs.models.provider_models import ValidationRuleType, FieldType
        
        field_type_enum = FieldType(field_type)
        rule_type_enum = ValidationRuleType(rule_type)
        
        # Validate based on rule type
        if rule_type_enum == ValidationRuleType.REQUIRED:
            if not isinstance(rule_value, bool):
                raise ValueError(f"Field '{field_name}' rule {rule_index}: 'required' rule value must be boolean, got {type(rule_value).__name__}")
        
        elif rule_type_enum in [ValidationRuleType.MIN_LENGTH, ValidationRuleType.MAX_LENGTH]:
            if not isinstance(rule_value, int) or rule_value < 0:
                raise ValueError(f"Field '{field_name}' rule {rule_index}: '{rule_type}' rule value must be non-negative integer")
            
            # Length rules only apply to string fields
            if field_type_enum != FieldType.STRING:
                raise ValueError(f"Field '{field_name}' rule {rule_index}: '{rule_type}' rule can only be applied to string fields")
        
        elif rule_type_enum in [ValidationRuleType.MIN_VALUE, ValidationRuleType.MAX_VALUE]:
            if not isinstance(rule_value, (int, float)):
                raise ValueError(f"Field '{field_name}' rule {rule_index}: '{rule_type}' rule value must be numeric")
            
            # Value rules only apply to numeric fields
            if field_type_enum not in [FieldType.INTEGER, FieldType.FLOAT]:
                raise ValueError(f"Field '{field_name}' rule {rule_index}: '{rule_type}' rule can only be applied to numeric fields")
        
        elif rule_type_enum == ValidationRuleType.PATTERN:
            if not isinstance(rule_value, str):
                raise ValueError(f"Field '{field_name}' rule {rule_index}: 'pattern' rule value must be string")
            
            # Pattern rules only apply to string fields
            if field_type_enum != FieldType.STRING:
                raise ValueError(f"Field '{field_name}' rule {rule_index}: 'pattern' rule can only be applied to string fields")
        
        elif rule_type_enum == ValidationRuleType.EMAIL:
            if not isinstance(rule_value, bool):
                raise ValueError(f"Field '{field_name}' rule {rule_index}: 'email' rule value must be boolean")
            
            # Email rules only apply to string fields
            if field_type_enum != FieldType.STRING:
                raise ValueError(f"Field '{field_name}' rule {rule_index}: 'email' rule can only be applied to string fields")
        
        elif rule_type_enum == ValidationRuleType.URL:
            if not isinstance(rule_value, bool):
                raise ValueError(f"Field '{field_name}' rule {rule_index}: 'url' rule value must be boolean")
            
            # URL rules only apply to string fields
            if field_type_enum != FieldType.STRING:
                raise ValueError(f"Field '{field_name}' rule {rule_index}: 'url' rule can only be applied to string fields")
        
        elif rule_type_enum == ValidationRuleType.CUSTOM:
            if not isinstance(rule_value, str):
                raise ValueError(f"Field '{field_name}' rule {rule_index}: 'custom' rule value must be string")
    
    def _validate_ftp_metadata(self, metadata: Optional[Dict[str, Any]]) -> None:
        """
        Validate FTP provider metadata requirements
        
        Args:
            metadata: Provider metadata dictionary
            
        Raises:
            ValueError: If required FTP metadata is missing or invalid
        """
        if not metadata:
            raise ValueError("FTP providers must have metadata configuration")
        
        if not isinstance(metadata, dict):
            raise ValueError("FTP provider metadata must be a dictionary")
        
        # Check if using date-based filename
        use_date_based = metadata.get('use_date_based_filename', False)
        
        if use_date_based:
            # For date-based filenames, validate date format
            date_format = metadata.get('date_format', 'dd-mm-yyyy')
            valid_date_formats = ['dd-mm-yyyy', 'yyyy-mm-dd', 'ddmmyyyy', 'yyyymmdd']
            
            if date_format not in valid_date_formats:
                raise ValueError(f"FTP date_format '{date_format}' is not supported. Valid formats: {valid_date_formats}")
            
            logger.info("FTP provider configured for date-based filenames with format: %s", date_format)
        else:
            # For fixed filenames, validate required fields
            required_fields = {
                'ftp_host': 'FTP server hostname or IP address',
                'ftp_username': 'FTP username',
                'ftp_file_name': 'Complete filename (e.g., data.csv, report_2024.csv)'
            }
            
            # Check required fields
            for field, description in required_fields.items():
                if field not in metadata:
                    raise ValueError(f"FTP provider metadata missing required field '{field}': {description}")
                
                value = metadata[field]
                if not value or (isinstance(value, str) and not value.strip()):
                    raise ValueError(f"FTP provider metadata field '{field}' cannot be empty: {description}")
            
            # Validate filename is complete (not a pattern) - do this after checking required fields
            file_name = metadata.get('ftp_file_name')
            if file_name:
                # Check if filename contains wildcards (patterns)
                if '*' in file_name or '?' in file_name or '[' in file_name:
                    raise ValueError("FTP file_name must be a complete filename, not a pattern. Use specific filename like 'data.csv'")
                
                # Check if filename has proper extension
                if '.' not in file_name:
                    raise ValueError("FTP file_name must include file extension (e.g., 'data.csv')")
                
                # Check if it's a CSV file
                if not file_name.lower().endswith('.csv'):
                    raise ValueError("FTP file_name must be a CSV file (must end with .csv)")
        
        # Always validate host and username
        if not metadata.get('ftp_host'):
            raise ValueError("FTP provider metadata missing required field 'ftp_host': FTP server hostname or IP address")
        
        if not metadata.get('ftp_username'):
            raise ValueError("FTP provider metadata missing required field 'ftp_username': FTP username")
        
        # Validate file format is CSV
        file_format = metadata.get('file_format', 'csv').lower()
        if file_format != 'csv':
            raise ValueError(f"FTP providers only support CSV file format, got '{file_format}'")
        
        # Validate FTP port if provided
        ftp_port = metadata.get('ftp_port')
        if ftp_port is not None:
            if not isinstance(ftp_port, int) or ftp_port <= 0 or ftp_port > 65535:
                raise ValueError("FTP port must be a valid integer between 1 and 65535")
        
        # Validate timeout if provided
        ftp_timeout = metadata.get('ftp_timeout')
        if ftp_timeout is not None:
            if not isinstance(ftp_timeout, int) or ftp_timeout <= 0:
                raise ValueError("FTP timeout must be a positive integer (seconds)")
        
        # Validate boolean fields
        boolean_fields = ['ftp_passive_mode', 'ftp_use_tls', 'ftp_use_sftp', 'ftp_delete_after_download', 'csv_has_header']
        for field in boolean_fields:
            value = metadata.get(field)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"FTP metadata field '{field}' must be boolean (true/false)")
        
        # Validate CSV delimiter
        csv_delimiter = metadata.get('csv_delimiter')
        if csv_delimiter is not None:
            if not isinstance(csv_delimiter, str) or len(csv_delimiter) != 1:
                raise ValueError("CSV delimiter must be a single character string")
        
        # Validate file encoding
        file_encoding = metadata.get('file_encoding')
        if file_encoding is not None:
            if not isinstance(file_encoding, str) or not file_encoding.strip():
                raise ValueError("File encoding must be a non-empty string (e.g., 'utf-8')")
        
        logger.info("FTP metadata validation passed for host: %s, filename: %s", 
                   metadata['ftp_host'], metadata.get('ftp_file_name', 'N/A'))
    
    def get_serve_table_for_data_type(self, data_type: DataType) -> str:
        """Get the serve table name for a data type"""
        return self.provider_service.get_serve_table_for_data_type(data_type)
    
    def get_serve_columns_for_data_type(self, data_type: DataType) -> List[str]:
        """Get the serve collection columns for a data type"""
        return self.provider_service.get_serve_columns_for_data_type(data_type)
    
    
    def get_valid_timeblocks(self) -> List[int]:
        """
        Get list of valid time_block values from time_block collection
        
        Returns:
            List[int]: List of valid time_block numbers
        """
        try:
            valid_timeblocks = self.timeblock_repository.get_valid_timeblock_numbers()
            return sorted(list(valid_timeblocks))
        except Exception as e:
            logger.error("Error getting valid timeblocks: %s", e)
            # Return fallback range if database query fails
            return list(range(1, 97))  # 1-96
    
    
    def _validate_time_block_mapping(self, source_field_name: str, target_field_name: str) -> tuple[bool, str]:
        """
        Validate that source field is appropriate for mapping to time_block target
        
        Args:
            source_field_name: Name of the source field in provider schema
            target_field_name: Name of the target field (should be 'time_block')
            
        Returns:
            tuple[bool, str]: (is_valid, error_message)
        """
        if target_field_name != "time_block":
            return True, ""  # Only validate time_block mappings
        
        # Check if source field name suggests it's a time_block field
        time_block_indicators = ["time_block", "timeblock", "block", "tb"]
        if not any(indicator in source_field_name.lower() for indicator in time_block_indicators):
            return False, f"Source field '{source_field_name}' doesn't appear to be a time_block field. Consider using a field with 'time_block', 'block', or 'tb' in the name"
        
        return True, ""
    
    def create_provider(self, request: CreateProviderRequest, created_by: Optional[str] = None) -> str:
        """
        Create a new provider
        
        Args:
            request: Create provider request
            created_by: User creating the provider
            
        Returns:
            str: Created provider ID
            
        Raises:
            ValueError: If provider code+ingestion_type+data_type combination already exists or validation fails
        """
        try:
            # Check if code+ingestion_type+data_type combination already exists
            if self.provider_service.repository.check_provider_combination_exists(
                request.code, request.ingestion_type, request.data_type
            ):
                raise ValueError(
                    f"Provider with code '{request.code}', ingestion_type '{request.ingestion_type}', "
                    f"and data_type '{request.data_type}' already exists"
                )
            
            # Validate schema fields
            self._validate_schema(request.fields, getattr(request, 'data_type', None), getattr(request, 'ingestion_type', None))
            
            # Validate field mappings
            # self._validate_field_mappings(request.fields, request.mapping, request.data_type, request.ingestion_type)
            
            # Validate FTP-specific requirements
            if request.ingestion_type == IngestionType.FTP:
                self._validate_ftp_metadata(request.metadata)
            
            # Create provider entity
            provider = ProviderEntity(
                name=request.name,
                code=request.code,
                enabled=request.enabled,
                ingestion_type=request.ingestion_type,
                data_type=request.data_type,
                fields=request.fields,
                unique_columns=request.unique_columns,
                mapping=request.mapping,
                description=request.description,
                created_by=created_by,
                metadata=request.metadata
            )
            
            # Save to database
            provider_id = self.provider_service.repository.create_provider(provider)
            
            logger.info("Created provider: %s (%s) by user: %s", 
                       provider_id, request.code, created_by)
            
            return provider_id
            
        except Exception as e:
            logger.error("Error creating provider: %s", e)
            raise
    
    def get_provider_by_id(self, provider_id: str) -> Optional[ProviderResponse]:
        """Get provider by ID"""
        return self.provider_service.get_provider_by_id(provider_id)
    
    def get_all_providers(
        self,
        ingestion_type: Optional[IngestionType] = None,
        data_type: Optional[DataType] = None,
        enabled: Optional[bool] = None,
        skip: int = 0,
        limit: int = 100
    ) -> ProviderListResponse:
        """Get all providers with optional filters"""
        return self.provider_service.get_all_providers(
            ingestion_type=ingestion_type,
            data_type=data_type,
            enabled=enabled,
            skip=skip,
            limit=limit
        )
    
    def _entity_to_response(self, entity: ProviderEntity) -> ProviderResponse:
        """Convert ProviderEntity to ProviderResponse"""
        return ProviderResponse(
            id=str(entity.id),
            name=entity.name,
            code=entity.code,
            enabled=entity.enabled,
            ingestion_type=entity.ingestion_type,
            data_type=entity.data_type,
            fields=entity.fields,
            unique_columns=entity.unique_columns,
            mapping=entity.mapping,
            description=entity.description,
            version=entity.version,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            created_by=entity.created_by
        )
    
    def _entity_to_summary(self, entity: ProviderEntity) -> ProviderSummary:
        """Convert ProviderEntity to ProviderSummary"""
        return ProviderSummary(
            id=str(entity.id),
            name=entity.name,
            code=entity.code,
            enabled=entity.enabled,
            ingestion_type=entity.ingestion_type,
            data_type=entity.data_type,
        )
    
    def update_provider(self, provider_id: str, request: UpdateProviderRequest) -> bool:
        """
        Update provider
        
        Args:
            provider_id: Provider ID
            request: Update provider request
            
        Returns:
            bool: True if updated successfully
            
        Raises:
            ValueError: If provider not found or validation fails
        """
        try:
            # Check if provider exists
            existing_provider = self.provider_service.repository.get_provider_by_id(provider_id)
            if not existing_provider:
                raise ValueError(f"Provider with ID '{provider_id}' not found")
            
            # Prepare updates
            updates = {}
            
            if request.name is not None:
                updates["name"] = request.name
            
            
            if request.enabled is not None:
                updates["enabled"] = request.enabled
            
            if request.ingestion_type is not None:
                # Check if new combination would be unique
                new_ingestion_type = request.ingestion_type
                new_data_type = request.data_type if request.data_type is not None else existing_provider.data_type
                if self.provider_service.repository.check_provider_combination_exists(
                    existing_provider.code, new_ingestion_type, new_data_type, exclude_id=provider_id
                ):
                    raise ValueError(
                        f"Provider with code '{existing_provider.code}', ingestion_type '{new_ingestion_type}', "
                        f"and data_type '{new_data_type}' already exists"
                    )
                updates["ingestion_type"] = request.ingestion_type.value
            
            if request.data_type is not None:
                # Check if new combination would be unique
                new_data_type = request.data_type
                new_ingestion_type = request.ingestion_type if request.ingestion_type is not None else existing_provider.ingestion_type
                if self.provider_service.repository.check_provider_combination_exists(
                    existing_provider.code, new_ingestion_type, new_data_type, exclude_id=provider_id
                ):
                    raise ValueError(
                        f"Provider with code '{existing_provider.code}', ingestion_type '{new_ingestion_type}', "
                        f"and data_type '{new_data_type}' already exists"
                    )
                updates["data_type"] = request.data_type.value
            
            if request.fields is not None:
                data_type_to_validate = request.data_type if request.data_type is not None else existing_provider.data_type
                ingestion_type_to_validate = request.ingestion_type if request.ingestion_type is not None else existing_provider.ingestion_type
                self._validate_schema(request.fields, data_type_to_validate, ingestion_type_to_validate)
                updates["fields"] = [field.model_dump() for field in request.fields]
            
            if request.unique_columns is not None:
                updates["unique_columns"] = request.unique_columns
            
            if request.mapping is not None:
                # Validate field mappings if schema is also being updated
                schema_to_validate = request.fields if request.fields else existing_provider.fields
                data_type_to_validate = request.data_type if request.data_type else existing_provider.data_type
                ingestion_type_to_validate = request.ingestion_type if request.ingestion_type else existing_provider.ingestion_type
                self._validate_field_mappings(schema_to_validate, request.mapping, data_type_to_validate, ingestion_type_to_validate)
                updates["mapping"] = [mapping.model_dump() for mapping in request.mapping]
            
            if request.description is not None:
                updates["description"] = request.description

            if request.metadata is not None:
                # Validate FTP metadata if this is an FTP provider
                ingestion_type_to_validate = request.ingestion_type if request.ingestion_type else existing_provider.ingestion_type
                if ingestion_type_to_validate == IngestionType.FTP:
                    self._validate_ftp_metadata(request.metadata)
                updates["metadata"] = request.metadata
            
            # Update provider
            success = self.provider_service.repository.update_provider(provider_id, updates)
            
            if success:
                logger.info("Updated provider: %s", provider_id)
            
            return success
            
        except Exception as e:
            logger.error("Error updating provider: %s", e)
            raise
    
    def delete_provider(self, provider_id: str) -> bool:
        """
        Delete provider
        
        Args:
            provider_id: Provider ID
            
        Returns:
            bool: True if deleted successfully
            
        Raises:
            ValueError: If provider not found
        """
        try:
            # Check if provider exists
            existing_provider = self.provider_service.repository.get_provider_by_id(provider_id)
            if not existing_provider:
                raise ValueError(f"Provider with ID '{provider_id}' not found")
            
            # Delete provider
            success = self.provider_service.repository.delete_provider(provider_id)
            
            if success:
                logger.info("Deleted provider: %s (%s)", provider_id, existing_provider.code)
            
            return success
            
        except Exception as e:
            logger.error("Error deleting provider: %s", e)
            raise
    
    def enable_disable_provider(self, provider_id: str, enabled: bool) -> bool:
        """
        Enable or disable provider
        
        Args:
            provider_id: Provider ID
            enabled: Whether to enable or disable
            
        Returns:
            bool: True if updated successfully
            
        Raises:
            ValueError: If provider not found
        """
        try:
            # Check if provider exists
            existing_provider = self.provider_service.repository.get_provider_by_id(provider_id)
            if not existing_provider:
                raise ValueError(f"Provider with ID '{provider_id}' not found")
            
            # Update enabled status
            success = self.provider_service.repository.enable_disable_provider(provider_id, enabled)
            
            if success:
                action = "enabled" if enabled else "disabled"
                logger.info("%s provider: %s (%s)", action, provider_id, existing_provider.code)
            
            return success
            
        except Exception as e:
            logger.error("Error enabling/disabling provider: %s", e)
            raise
    
    def validate_provider_type_data_type_combination(self, provider_type: str, data_type: str) -> Dict[str, Any]:
        """
        Validate that a provider type and data type combination exists and is enabled
        
        Args:
            provider_type: Provider type
            data_type: Data type    
            
        Returns:
            Dict[str, Any]: Validation result with success status and details
        """
        try:
            # Convert data_type to lowercase to match enum values
            data_type_lower = data_type.lower()
            
            # Validate data type is supported
            try:
                DataType(data_type_lower)
            except ValueError:
                valid_types = [dt.value for dt in DataType]
                return {
                    "valid": False,
                    "error": f"Invalid data type '{data_type}'. Valid types: {valid_types}",
                    "provider_type": provider_type,
                    "data_type": data_type
                }
            
            # Find provider by code and data type
            provider = self.provider_service.repository.get_provider_by_code_and_data_type(provider_type, data_type_lower)
            
            if not provider:
                return {
                    "valid": False,
                    "error": f"No provider found with code '{provider_type}' for data type '{data_type}'",
                    "provider_type": provider_type,
                    "data_type": data_type
                }
            
            # Check if provider is enabled
            if not provider.enabled:
                return {
                    "valid": False,
                    "error": f"Provider '{provider_type}' is disabled for data type '{data_type}'",
                    "provider_type": provider_type,
                    "data_type": data_type,
                    "provider_id": str(provider.id)
                }
            
            # Check if provider supports UPLOAD ingestion type
            if provider.ingestion_type != IngestionType.UPLOAD:
                return {
                    "valid": False,
                    "error": f"Provider '{provider_type}' does not support file upload. Ingestion type: {provider.ingestion_type}",
                    "provider_type": provider_type,
                    "data_type": data_type,
                    "provider_id": str(provider.id),
                    "ingestion_type": provider.ingestion_type
                }
            
            logger.info("Provider validation successful: %s/%s (ID: %s)", 
                       provider_type, data_type, provider.id)
            
            return {
                "valid": True,
                "provider_id": str(provider.id),
                "provider_name": provider.name,
                "provider_type": provider_type,
                "data_type": data_type,
                "ingestion_type": provider.ingestion_type,
                "schema_fields": [field.field_name for field in provider.fields],
                "unique_columns": provider.unique_columns
            }
            
        except Exception as e:
            logger.error("Error validating provider type and data type combination: %s", e)
            return {
                "valid": False,
                "error": f"Validation error: {str(e)}",
                "provider_type": provider_type,
                "data_type": data_type
            }

    # Async adapter methods for application layer/adapters
    async def get_provider_by_code_and_type(self, provider_code: str, data_type: str) -> Optional[Dict[str, Any]]:
        """Async: Get provider by code and data type, mapped for domain layer usage"""
        try:
            provider = self.provider_service.repository.get_provider_by_code_and_data_type(
                provider_code, data_type.lower() if isinstance(data_type, str) else data_type
            )
            if not provider:
                return None

            # Map libs.models ProviderEntity -> dict expected by domain ProviderEntity
            # Convert fields to the format expected by the application service
            fields_data = []
            if provider.fields:
                for field in provider.fields:
                    if hasattr(field, "model_dump"):
                        field_dict = field.model_dump()
                    else:
                        # Handle case where field is already a dict
                        field_dict = field if isinstance(field, dict) else {
                            "field_name": getattr(field, "field_name", ""),
                            "field_type": getattr(field, "field_type", "string"),
                            "is_required": getattr(field, "is_required", False),
                            "description": getattr(field, "description", ""),
                            "validation": getattr(field, "validation", [])
                        }
                    fields_data.append(field_dict)

            return {
                "provider_id": str(provider.id) if getattr(provider, "id", None) is not None else None,
                "name": provider.name,
                "code": provider.code,
                "data_type": provider.data_type.value if hasattr(provider.data_type, "value") else str(provider.data_type),
                "ingestion_type": provider.ingestion_type.value if hasattr(provider.ingestion_type, "value") else str(provider.ingestion_type),
                "enabled": bool(provider.enabled),
                "description": provider.description or "",
                "fields": fields_data,  # Use 'fields' instead of 'schema' to match application service expectation
                "created_at": getattr(provider, "created_at", None),
                "updated_at": getattr(provider, "updated_at", None),
            }
        except Exception as e:
            logger.error("Error fetching provider by code/type: %s", e)
            return None
