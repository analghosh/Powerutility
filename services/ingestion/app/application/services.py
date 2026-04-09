"""
Application Services for Ingestion Use Cases
Orchestrates business logic without infrastructure dependencies
"""

import uuid
import time
from datetime import datetime
from typing import Dict, Any, List, Optional

from ..domain.entities import (
    UploadEntity, ProviderEntity, FileUploadRequest, UploadResult
)
from ..interfaces.repositories import (
    IUploadTrackingRepository, IProviderRepository, IFileStorageRepository
)
from libs.enum.raw_data_status import RawDataStatus


from libs.services.raw_data_service import RawDataService



class FileUploadApplicationService:
    """Application service for file upload use cases"""
    
    def __init__(
        self,
        upload_repository: IUploadTrackingRepository,
        provider_repository: IProviderRepository,
        file_storage: IFileStorageRepository
    ):
        self._upload_repository = upload_repository
        self._provider_repository = provider_repository
        self._file_storage = file_storage
        
        # Initialize RawDataService for detailed status lookups
        from ..core.config import settings
        self._raw_data_service = RawDataService(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE
        )

    
    async def upload_file(self, upload_request: FileUploadRequest, user_id: str) -> UploadResult:
        """
        Execute file upload use case
        
        Args:
            upload_request: Domain object containing upload data
            
        Returns:
            UploadResult: Result of the upload operation
            
        Raises:
            ValueError: If validation fails
            RuntimeError: If upload processing fails
        """
        start_time = time.time()
        
        # 1. Validate upload request
        validation_errors = upload_request.validate()
        if validation_errors:
            raise ValueError(f"Upload validation failed: {', '.join(validation_errors)}")
        
        # 2. Validate provider exists and supports uploads
        provider = await self._provider_repository.get_provider_by_code_and_type(
            upload_request.provider_type,
            upload_request.data_type
        )
        
        if not provider:
            raise ValueError(
                f"Provider '{upload_request.provider_type}' not found for data type '{upload_request.data_type}'"
            )
        
        provider_entity = ProviderEntity(**provider)
        if not provider_entity.can_process_uploads():
            raise ValueError(
                f"Provider '{upload_request.provider_type}' does not support file uploads or is disabled"
            )
        
        # 3. Validate time format fields in CSV content
        time_validation_errors = self._validate_time_format_fields(upload_request.file_content)
        if time_validation_errors:
            raise ValueError(f"Time format validation failed: {', '.join(time_validation_errors)}")
        


        # 3. Generate upload ID and create upload entity
        upload_id = str(uuid.uuid4())
        upload_entity = UploadEntity(
            upload_id=upload_id,
            uploaded_by=user_id,
            file_name=upload_request.file_name,
            file_size=upload_request.file_size,
            data_type=upload_request.data_type,
            provider_type=upload_request.provider_type,
            status=RawDataStatus.PENDING,
            status_message="File uploaded successfully, processing queued",
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        
        # 4. Save file to storage
        try:
            file_path = await self._file_storage.save_file(
                upload_request.file_content,
                upload_request.file_name,
                upload_id
            )
        except Exception as e:
            raise RuntimeError(f"Failed to save file: {str(e)}") from e
        
        # 5. Create upload tracking record
        try:
            tracking_id = await self._upload_repository.create_upload_record(
                upload_id=upload_id,
                file_name=upload_request.file_name,
                file_path=file_path,
                file_size=upload_request.file_size,
                data_type=upload_request.data_type,
                provider_type=upload_request.provider_type,
                status=RawDataStatus.PENDING
            )
        except Exception as e:
            # Cleanup file if database operation fails
            await self._file_storage.delete_file(file_path)
            raise RuntimeError(f"Failed to create upload record: {str(e)}") from e
        
        # 6. Calculate processing time and return result
        processing_time = (time.time() - start_time) * 1000
        
        return UploadResult(
            upload_id=upload_id,
            tracking_id=tracking_id,
            file_name=upload_request.file_name,
            file_size=upload_request.file_size,
            status="pending",
            status_message="File uploaded successfully, processing queued",
            processing_time_ms=processing_time,
            upload_timestamp=upload_entity.created_at or datetime.now(), 
            success=True
        )
    
    async def get_upload_status(self, upload_id: str) -> Dict[str, Any]:
        """
        Get upload status use case
        
        Args:
            upload_id: Upload identifier
            
        Returns:
            Dict containing upload status information
            
        Raises:
            ValueError: If upload not found
        """
        # We need a logger here, but it's not injected. We'll use print for now or try to get logger if available
        # Ideally we should inject logger or use a global one.
        # Let's import it locally to avoid circular imports or just use print for debugging as requested
        from ..core.logger import get_ingestion_logger
        logger = get_ingestion_logger("file_upload_service")
        
        logger.info(f"Service: Fetching upload status for ID: {upload_id}")
        
        upload_data = await self._upload_repository.get_upload_by_id(upload_id)
        
        if not upload_data:
            logger.warning(f"Service: Upload ID {upload_id} not found in repository")
            raise ValueError(f"Upload with ID '{upload_id}' not found")
        
        # Remove file_path from API response for security/cleanliness
        upload_data.pop('file_path', None)
        
        # Convert datetime objects to ISO format strings for JSON serialization
        datetime_fields = ['created_at', 'updated_at', 'completed_at', 'failed_at']
        for field in datetime_fields:
            if field in upload_data and upload_data[field] is not None:
                if isinstance(upload_data[field], datetime):
                    upload_data[field] = upload_data[field].isoformat() + 'Z'
        
        return upload_data

    async def get_raw_processing_status(self, upload_id: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed raw data processing status
        
        Args:
            upload_id: Upload tracking ID
            
        Returns:
            Dict[str, Any]: Processing status record or None
        """
        # Get upload record first to determine data_type
        upload_data = await self._upload_repository.get_upload_by_id(upload_id)
        data_type = upload_data.get("data_type") if upload_data else None
        
        status_record = await self._raw_data_service.get_processing_status_by_upload_id(upload_id, data_type=data_type)
        if not status_record:
            return None
        
        # Convert datetime objects to ISO format strings
        for key, value in status_record.items():
            if isinstance(value, datetime):
                status_record[key] = value.isoformat() + 'Z'
        
        return status_record
    
    async def get_uploads_paginated(
        self,
        skip: int = 0,
        limit: int = 20,
        status_filter: Optional[str] = None,
        sort_by: str = "created_at",
        sort_order: int = -1
    ) -> Dict[str, Any]:
        """
        Get paginated uploads use case
        
        Args:
            skip: Number of records to skip
            limit: Maximum number of records to return
            status_filter: Optional status filter (pending, processing, completed, failed)
            sort_by: Field to sort by (default: created_at)
            sort_order: Sort order (1 for ascending, -1 for descending)
            
        Returns:
            Dict containing paginated uploads and metadata
        """
        result = await self._upload_repository.get_uploads_paginated(
            skip=skip,
            limit=limit,
            status_filter=status_filter,
            sort_by=sort_by,
            sort_order=sort_order
        )
        
        # Convert datetime objects to ISO format strings for JSON serialization
        for upload in result["uploads"]:
            # Remove file_path from API response for security/cleanliness
            upload.pop('file_path', None)
            
            datetime_fields = ['created_at', 'updated_at', 'completed_at', 'failed_at']
            for field in datetime_fields:
                if field in upload and upload[field] is not None:
                    if isinstance(upload[field], datetime):
                        upload[field] = upload[field].isoformat() + 'Z'
        
        return result

    async def get_csv_format(self, data_type: str, provider_code: str) -> Dict[str, Any]:
        """
        Get CSV format for specific data type and provider code use case
        
        Args:
            data_type: Type of data ('demand', 'supply', etc.)
            provider_code: Provider code
            
        Returns:
            Dict containing CSV format information
            
        Raises:
            ValueError: If provider not found or validation fails
        """
        # 1. Validate data type
        from libs.models.provider_models import DataType
        try:
            DataType(data_type.lower())
        except ValueError:
            valid_types = [dt.value for dt in DataType]
            raise ValueError(f"Invalid data type '{data_type}'. Valid types: {valid_types}")
        
        # 2. Get provider by code and data type
        provider_data = await self._provider_repository.get_provider_by_code_and_type(
            provider_code.upper(),
            data_type.lower()
        )
        
        if not provider_data:
            raise ValueError(f"No provider found with code '{provider_code}' for data type '{data_type}'")
        
        # 3. Check if provider is enabled
        if not provider_data.get('enabled', False):
            raise ValueError(f"Provider '{provider_code}' is disabled for data type '{data_type}'")
        
        # 4. Check if provider has schema defined
        if not provider_data.get('fields') or len(provider_data['fields']) == 0:
            raise ValueError(f"Provider '{provider_code}' does not have a defined schema for data type '{data_type}'")
        
        # 5. Build CSV format information
        fields = provider_data['fields']
        required_fields = []
        optional_fields = []
        field_specifications = []
        
        for field in fields:
            field_name = field.get('field_name', '')
            field_type = field.get('field_type', 'string')
            is_required = field.get('is_required', False)
            description = field.get('description', '')
            validation_rules = field.get('validation', [])
            
            if is_required:
                required_fields.append(field_name)
            else:
                optional_fields.append(field_name)
            
            # Convert validation rules to a more readable format
            formatted_rules = []
            for rule in validation_rules:
                formatted_rules.append({
                    "rule": rule.get('rule', ''),
                    "value": rule.get('value', ''),
                    "message": rule.get('message', '')
                })
            
            field_specifications.append({
                "field_name": field_name,
                "field_type": field_type,
                "is_required": is_required,
                "description": description,
                "example": self._get_field_example(field_name, field_type),
                "validation_rules": formatted_rules
            })
        
        # 6. Generate CSV template with headers only (no sample data)
        csv_template = self._generate_csv_template_headers_only(fields)
        
        # 7. Build response
        result = {
            "provider_info": {
                "provider_code": provider_data.get('code', ''),
                "provider_name": provider_data.get('name', ''),
                "data_type": provider_data.get('data_type', ''),
                "ingestion_type": provider_data.get('ingestion_type', ''),
                "enabled": provider_data.get('enabled', False),
                "description": provider_data.get('description', '')
            },
            "csv_format": {
                "headers": [field.get('field_name', '') for field in fields],
                "required_fields": required_fields,
                "optional_fields": optional_fields,
                "field_specifications": field_specifications
            },
            "csv_template": csv_template,
            "file_requirements": {
                "max_size_mb": 100,
                "encoding": "utf-8",
                "delimiter": ",",
                "has_header": True,
                "line_ending": "\\n"
            }
        }
        
        return result
    
    async def download_file(self, upload_id: str) -> Dict[str, Any]:
        """
        Download file use case
        
        Args:
            upload_id: Upload identifier
            
        Returns:
            Dict containing file path and metadata
            
        Raises:
            ValueError: If upload or file not found
        """
        # 1. Get upload record
        upload_data = await self._upload_repository.get_upload_by_id(upload_id)
        if not upload_data:
            raise ValueError(f"Upload with ID '{upload_id}' not found")
            
        # 2. Get file path
        file_path = await self._file_storage.get_file_path(upload_id)
        if not file_path:
            # Try to get from upload record if not found in storage map (legacy support)
            file_path = upload_data.get('file_path')
            
        if not file_path:
            raise ValueError(f"File path not found for upload ID '{upload_id}'")
            
        # 3. Check if file exists
        if not await self._file_storage.file_exists(file_path):
            raise ValueError(f"File not found at path: {file_path}")
            
        return {
            "file_path": file_path,
            "file_name": upload_data.get("file_name", "download.csv"),
            "data_type": upload_data.get("data_type", "unknown"),
            "provider_type": upload_data.get("provider_type", "unknown")
        }

    def _get_field_example(self, field_name: str, field_type: str) -> str:
        """Generate example value for a field based on its type"""
        examples = {
            "string": "example_value",
            "integer": "123",
            "float": "123.45",
            "datetime": "2024-01-15",
            "boolean": "true",
            "date": "2024-01-15",
            "time": "14:30:00"
        }
        
        # Special cases for common field names
        if "date" in field_name.lower():
            return "2024-01-15"
        elif "time" in field_name.lower():
            return "00:00-01:00" if "block" in field_name.lower() else "14:30:00"
        elif "demand" in field_name.lower() or "supply" in field_name.lower():
            return "1250.5"
        elif "price" in field_name.lower():
            return "45.67"
        elif "id" in field_name.lower():
            return "12345"
        
        return examples.get(field_type.lower(), "example_value")
    
    def _generate_sample_data(self, fields: list) -> list:
        """Generate sample data for CSV template"""
        sample_records = []
        
        # Generate 2 sample records
        for i in range(2):
            record = {}
            for field in fields:
                field_name = field.get('field_name', '')
                field_type = field.get('field_type', 'string')
                example_value = self._get_field_example(field_name, field_type)
                
                # Modify second record slightly
                if i == 1:
                    if field_type in ['integer', 'float']:
                        # Only try to convert to float if it's a numeric string
                        try:
                            numeric_value = float(example_value)
                            example_value = str(numeric_value + 100)
                        except (ValueError, TypeError):
                            # If conversion fails, just use the original value
                            pass
                    elif field_type == 'datetime':
                        example_value = "2024-01-16"
                    elif field_type == 'date':
                        example_value = "2024-01-16"
                    elif "time" in field_name.lower() and "block" in field_name.lower():
                        example_value = "01:00-02:00"
                
                record[field_name] = example_value
            
            sample_records.append(record)
        
        return sample_records
    
    def _generate_csv_template(self, fields: list, sample_data: list) -> str:
        """Generate CSV template with headers and sample data"""
        if not fields:
            return ""
        
        # Get field names
        headers = [field.get('field_name', '') for field in fields]
        
        # Create CSV content
        csv_lines = [','.join(headers)]
        
        for record in sample_data:
            csv_line = ','.join([str(record.get(header, '')) for header in headers])
            csv_lines.append(csv_line)
        
        return '\n'.join(csv_lines)
    
    def _generate_csv_template_headers_only(self, fields: list) -> str:
        """Generate CSV template with headers only (no sample data)"""
        if not fields:
            return ""
        
        # Get field names
        headers = [field.get('field_name', '') for field in fields]
        
        # Return only headers
        return ','.join(headers)
    
    def _validate_time_format_fields(self, file_content: bytes) -> List[str]:
        """
        Validate time format fields in CSV content.
        Checks if fields like time_start, time_end, etc. are in HH:MM format.
        
        Args:
            file_content: Raw file content as bytes
            
        Returns:
            List[str]: List of validation error messages (empty if valid)
        """
        import csv
        import io
        import re
        
        errors = []
        # Strict HH:MM format (00:00 to 23:59) or 24:00 (special case for end of day)
        time_pattern = re.compile(r'^(([0-1][0-9]|2[0-3]):[0-5][0-9]|24:00)$')
        time_field_patterns = ['time_start', 'time_end', 'start_time', 'end_time']
        # Exclude patterns that should NOT be validated as time formats
        exclude_patterns = ['time_block', 'timeblock', 'block']
        
        try:
            # Decode file content
            try:
                content_str = file_content.decode('utf-8')
            except UnicodeDecodeError:
                try:
                    content_str = file_content.decode('utf-8-sig')
                except UnicodeDecodeError:
                    content_str = file_content.decode('utf-8', errors='replace')
            
            # Parse CSV
            csv_reader = csv.DictReader(io.StringIO(content_str))
            headers = csv_reader.fieldnames
            
            if not headers:
                return errors
            
            # Find time-related columns
            time_columns = []
            for header in headers:
                header_lower = header.lower().strip()
                # Check if it matches time patterns but exclude time_block and similar
                if (any(pattern in header_lower for pattern in time_field_patterns) and 
                    not any(exclude_pattern in header_lower for exclude_pattern in exclude_patterns)):
                    time_columns.append(header)
            
            # If no time columns found, no validation needed
            if not time_columns:
                return errors
            
            # Validate each row
            for row_num, row in enumerate(csv_reader, start=2):  # start=2 because row 1 is headers
                for col in time_columns:
                    value = row.get(col, '').strip()
                    
                    # Skip empty values
                    if not value:
                        continue
                    
                    # Validate format
                    if not time_pattern.match(value):
                        errors.append(
                            f"Row {row_num}, Column '{col}': Invalid time format '{value}'. "
                            f"Expected HH:MM format (e.g., 09:30, 14:45)"
                        )
                        
                        # Limit error messages to avoid overwhelming response
                        if len(errors) >= 10:
                            errors.append("... and more errors. Please fix the above issues first.")
                            return errors
            
            return errors
            
        except Exception as e:
            # If CSV parsing fails, let it be handled by the main parser
            # Don't block upload for parsing errors here
            return []


class ProviderManagementApplicationService:
    """Application service for provider management use cases"""
    
    def __init__(self, provider_repository: IProviderRepository):
        self._provider_repository = provider_repository
    
    async def create_provider(self, provider_data: Dict[str, Any]) -> str:
        """
        Create provider use case
        
        Args:
            provider_data: Provider information
            
        Returns:
            str: Created provider ID
            
        Raises:
            ValueError: If validation fails
        """
        # Create domain entity for validation
        provider_entity = ProviderEntity(
            provider_id=None,
            name=provider_data.get("name", ""),
            code=provider_data.get("code", ""),
            data_type=provider_data.get("data_type", ""),
            ingestion_type=provider_data.get("ingestion_type", ""),
            enabled=provider_data.get("enabled", True),
            description=provider_data.get("description", ""),
            schema=provider_data.get("schema"),
            created_at=datetime.now()
        )
        
        # Validate entity
        validation_errors = provider_entity.validate()
        if validation_errors:
            raise ValueError(f"Provider validation failed: {', '.join(validation_errors)}")
        
        # Check if provider with same code and data type already exists
        existing_provider = await self._provider_repository.get_provider_by_code_and_type(
            provider_entity.code,
            provider_entity.data_type
        )
        
        if existing_provider:
            raise ValueError(
                f"Provider with code '{provider_entity.code}' and data type '{provider_entity.data_type}' already exists"
            )
        
        # Create provider
        return await self._provider_repository.create_provider(provider_entity.to_dict())
    
    async def update_provider(self, provider_id: str, update_data: Dict[str, Any]) -> bool:
        """
        Update provider use case
        
        Args:
            provider_id: Provider identifier
            update_data: Data to update
            
        Returns:
            bool: True if updated successfully
            
        Raises:
            ValueError: If provider not found or validation fails
        """
        # Add updated timestamp
        update_data["updated_at"] = datetime.now()
        
        # Update provider
        success = await self._provider_repository.update_provider(provider_id, update_data)
        
        if not success:
            raise ValueError(f"Provider with ID '{provider_id}' not found")
        
        return success
    
    async def delete_provider(self, provider_id: str) -> bool:
        """
        Delete provider use case
        
        Args:
            provider_id: Provider identifier
            
        Returns:
            bool: True if deleted successfully
            
        Raises:
            ValueError: If provider not found
        """
        success = await self._provider_repository.delete_provider(provider_id)
        
        if not success:
            raise ValueError(f"Provider with ID '{provider_id}' not found")
        
        return success
    
    async def get_providers_list(
        self,
        skip: int = 0,
        limit: int = 100,
        filters: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Get providers list use case
        
        Args:
            skip: Number of records to skip
            limit: Maximum number of records to return
            filters: Optional filters to apply
            
        Returns:
            Dict containing providers list and metadata
        """
        return await self._provider_repository.get_providers_list(skip, limit, filters)