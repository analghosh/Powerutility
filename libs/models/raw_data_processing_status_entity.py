"""
Raw Data Processing Status Entity
Tracks the processing status of raw data records
"""

from datetime import datetime
from typing import Optional, Any, Dict
from pydantic import BaseModel, Field, ConfigDict
from bson import ObjectId
from libs.enum.raw_data_status import RawDataStatus


class RawDataProcessingStatusEntity(BaseModel):
    """
    Entity to track processing status of raw data
    Separate from the actual raw data for better performance and organization
    """
    
    model_config = ConfigDict(
        validate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={
            ObjectId: str,
            datetime: lambda v: v.isoformat()
        },
        json_schema_extra={
            "example": {
                "raw_data_id": "507f1f77bcf86cd799439011",
                "upload_id": "uuid-string",
                "status": "pending",
                "validation_result": {
                    "valid": True,
                    "errors": [],
                    "warnings": []
                },
                "processing_metadata": {
                    "total_records": 100,
                    "processed_records": 0
                }
            }
        }
    )
    
    # MongoDB ID
    id: Optional[Any] = Field(default_factory=ObjectId, alias="_id")
    
    # Reference to raw data document
    raw_data_id: str = Field(..., description="Reference to raw data document")
    provider_id: str = Field(..., description="Reference to provider document")
        
    # Processing status
    status: RawDataStatus = Field(default=RawDataStatus.PENDING, description="Processing status")
        
    # Processing timestamps
    created_at: datetime = Field(default_factory=datetime.now, description="Creation timestamp")
    updated_at: datetime = Field(default_factory=datetime.now, description="Last update timestamp")
    processing_started_at: Optional[datetime] = Field(None, description="When processing started")
    processing_completed_at: Optional[datetime] = Field(None, description="When processing completed")
    
    # Error handling
    error_message: Optional[str] = Field(None, description="Error message if processing failed")
    retry_count: int = Field(default=0, description="Number of retry attempts")
    
    # Processing metadata
    processing_metadata: Dict[str, Any] = Field(
        default_factory=dict, 
        description="Additional processing metadata (records count, etc.)"
    )
    
    def mark_processing_started(self):
        """Mark processing as started"""
        self.status = RawDataStatus.PROCESSING
        self.processing_started_at = datetime.now()
        self.updated_at = datetime.now()
    
    def mark_completed(self, processing_metadata: Optional[Dict[str, Any]] = None):
        """Mark processing as completed"""
        self.status = RawDataStatus.COMPLETED
        self.processing_completed_at = datetime.now()
        self.updated_at = datetime.now()
        if processing_metadata:
            self.processing_metadata.update(processing_metadata)
    
    def mark_failed(self, error_message: str, increment_retry: bool = True):
        """Mark processing as failed"""
        self.status = RawDataStatus.FAILED
        self.processing_completed_at = datetime.now()
        self.updated_at = datetime.now()
        self.error_message = error_message
        if increment_retry:
            self.retry_count += 1
    
    def can_retry(self, max_retries: int = 3) -> bool:
        """Check if processing can be retried"""
        return self.status == RawDataStatus.FAILED and self.retry_count < max_retries
# Database Index Management
def ensure_raw_data_processing_status_indexes(collection):
    """
    Ensure database indexes for raw_data_processing_status collection
    
    Args:
        collection: MongoDB collection instance
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        # Primary raw_data_id index (most common query)
        collection.create_index("raw_data_id", background=True)
        
        # Status index for filtering by processing status
        collection.create_index("status", background=True)
        
        # Provider code index for provider-specific queries
        collection.create_index("provider_code", background=True)
        
        # Compound index for status + provider queries (very common)
        collection.create_index([
            ("status", 1),
            ("provider_code", 1)
        ], background=True)
        
        # Compound index for raw_data_id + status
        collection.create_index([
            ("raw_data_id", 1),
            ("status", 1)
        ], background=True)
        
        # Time-based indexes for processing tracking
        collection.create_index("created_at", background=True)
        collection.create_index("updated_at", background=True)
        collection.create_index("started_at", background=True)
        collection.create_index("completed_at", background=True)
        
        # Compound index for time-based status queries
        collection.create_index([
            ("created_at", 1),
            ("status", 1)
        ], background=True)
        
        # Index for error tracking
        collection.create_index("error_message", background=True)
        
        logger.debug("✅ Raw data processing status collection indexes ensured")
        
    except Exception as e:
        logger.warning(f"⚠️ Failed to create raw data processing status indexes: {e}")
