"""
Domain Entities for Ingestion Service
Pure domain objects without infrastructure dependencies
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, Optional, List
from enum import Enum

from libs.enum.raw_data_status import RawDataStatus


class UploadStatus(str, Enum):
    """Upload status enumeration"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class UploadEntity:
    """Domain entity for file uploads"""
    upload_id: str
    uploaded_by: str
    file_name: str
    file_size: int
    data_type: str
    provider_type: str
    status: RawDataStatus
    status_message: str = ""
    retry_count: int = 0  # Track number of processing attempts
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None
    integration_apilog_id: Optional[str] = None
    
    def mark_as_processing(self) -> None:
        """Mark upload as processing"""
        self.status = RawDataStatus.PROCESSING
        self.status_message = "Processing file data"
        self.updated_at = datetime.now()
    
    def mark_as_completed(self, message: str = "Processing completed successfully") -> None:
        """Mark upload as completed"""
        self.status = RawDataStatus.COMPLETED
        self.status_message = message
        self.completed_at = datetime.now()
        self.updated_at = datetime.now()
    
    def mark_as_failed(self, error_message: str, increment_retry: bool = True) -> None:
        """Mark upload as failed"""
        self.status = RawDataStatus.FAILED
        self.status_message = error_message
        self.failed_at = datetime.now()
        self.updated_at = datetime.now()
        if increment_retry:
            self.retry_count += 1
    
    def is_processable(self, max_retries: int = 3) -> bool:
        """Check if upload can be processed"""
        return self.status == RawDataStatus.PENDING and self.retry_count < max_retries
    
    def has_exceeded_retries(self, max_retries: int = 3) -> bool:
        """Check if upload has exceeded maximum retry attempts"""
        return self.retry_count >= max_retries
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "upload_id": self.upload_id,
            "file_name": self.file_name,
            "file_size": self.file_size,
            "data_type": self.data_type,
            "provider_type": self.provider_type,
            "status": self.status.value,
            "status_message": self.status_message,
            "retry_count": self.retry_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "failed_at": self.failed_at,
            "integration_apilog_id": self.integration_apilog_id
        }


@dataclass
class ProviderEntity:
    """Domain entity for data providers"""
    provider_id: Optional[str] = None
    name: str = ""
    code: str = ""
    data_type: str = ""
    ingestion_type: str = ""
    enabled: bool = True
    description: str = ""
    schema: Optional[List[Dict[str, Any]]] = None
    fields: Optional[List[Dict[str, Any]]] = None  # Add fields parameter
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    def validate(self) -> List[str]:
        """Validate provider entity"""
        errors = []
        
        if not self.name or not self.name.strip():
            errors.append("Provider name is required")
        
        if not self.code or not self.code.strip():
            errors.append("Provider code is required")
        
        if not self.data_type:
            errors.append("Data type is required")
        
        if not self.ingestion_type:
            errors.append("Ingestion type is required")
        
        return errors
    
    def is_valid(self) -> bool:
        """Check if provider is valid"""
        return len(self.validate()) == 0
    
    def can_process_uploads(self) -> bool:
        """Check if provider can process file uploads"""
        return self.enabled and self.ingestion_type.upper() == "UPLOAD"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "provider_id": self.provider_id,
            "name": self.name,
            "code": self.code,
            "data_type": self.data_type,
            "ingestion_type": self.ingestion_type,
            "enabled": self.enabled,
            "description": self.description,
            "schema": self.schema,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }


@dataclass
class FileUploadRequest:
    """Domain value object for file upload requests"""
    file_content: bytes
    file_name: str
    file_size: int
    data_type: str
    provider_type: str
    
    def validate(self) -> List[str]:
        """Validate upload request"""
        errors = []
        
        if not self.file_content:
            errors.append("File content is required")
        
        if not self.file_name or not self.file_name.strip():
            errors.append("File name is required")
        
        if self.file_size <= 0:
            errors.append("File size must be greater than 0")
        
        if not self.data_type:
            errors.append("Data type is required")
        
        if not self.provider_type:
            errors.append("Provider type is required")
        
        # File size validation (100MB limit)
        max_size = 100 * 1024 * 1024  # 100MB in bytes
        if self.file_size > max_size:
            errors.append(f"File size exceeds maximum limit of {max_size // (1024*1024)}MB")
        
        # File extension validation
        if not self.file_name.lower().endswith('.csv'):
            errors.append("Only CSV files are supported")
        
        return errors
    
    def is_valid(self) -> bool:
        """Check if upload request is valid"""
        return len(self.validate()) == 0


@dataclass
class UploadResult:
    """Domain value object for upload results"""
    upload_id: str
    tracking_id: str
    file_name: str
    file_size: int
    status: str
    status_message: str
    processing_time_ms: float
    upload_timestamp: datetime
    success: bool
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API response"""
        return {
            "upload_id": self.upload_id,
            "tracking_id": self.tracking_id,
            "file_name": self.file_name,
            "file_size": self.file_size,
            "status": self.status,
            "status_message": self.status_message,
            "processing_time_ms": self.processing_time_ms,
            "upload_timestamp": self.upload_timestamp.isoformat(),
            "success": self.success
        }