"""
Raw Data Entity - Schema-Driven Universal Storage
Supports any data type based on schema configuration
"""

from datetime import datetime
from typing import Optional, Any, Dict, List
from pydantic import BaseModel, Field, ConfigDict
from bson import ObjectId


class RawDataEntity(BaseModel):
    """
    Raw Data Entity - Time Series Optimized Storage
    
    This entity stores the actual raw data in time series format.
    Processing status is tracked separately in RawDataProcessingStatusEntity.
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
                "data_type": "demand",
                "provider_id": "6547890543456",
                "raw_data": [
                    {
                        "timeblock": 1,
                        "value": 150.5,
                        "schedule_date": "2025-01-15T10:00:00Z",
                        "zone": "North Zone"
                    }
                ],
                "metadata": {
                    "total_records": 1,
                    "validation_status": "passed"
                },
                "created_at": "2025-01-15T10:00:00",
                "updated_at": "2025-01-15T10:00:00"
            }
        }
    )
    
    # MongoDB ID
    id: Optional[Any] = Field(default_factory=ObjectId, alias="_id")
    
    # Data type - can be ANY type: demand, supply, weather, pricing, forecast, etc.
    data_type: str = Field(..., description="Type of data - schema-driven, can be any type")
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.now, description="Creation timestamp")
    updated_at: datetime = Field(default_factory=datetime.now, description="Last update timestamp")
    
    # Universal raw data storage - structure defined by schema in metadata
    raw_data: List[Dict[str, Any]] = Field(default_factory=list, description="Schema-validated raw data")
    
    # Schema-driven metadata - contains schema definition and validation info
    metadata: Dict[str, Any] = Field(
        default_factory=dict, 
        description="Schema-driven metadata including data_schema, provider_type, validation_result"
    )
    
    # Provider information (for filtering and organization)
    provider_id: str = Field(..., description="Provider ID")
    
    # Link to the blind integration log
    integration_apilog_id: Optional[Any] = Field(default=None, description="ID(s) of the original integration log entry")
    
