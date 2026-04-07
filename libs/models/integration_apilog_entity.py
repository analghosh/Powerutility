from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, ConfigDict
from bson import ObjectId

class IntegrationApiLogEntity(BaseModel):
    """
    Integration API Log Entity - Blind Write Storage
    
    Stores raw data directly from providers before any validation.
    Dynamic collection based on data_type: {data_type}_integration_apilog
    """
    
    model_config = ConfigDict(
        validate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={
            ObjectId: str,
            datetime: lambda v: v.isoformat()
        }
    )
    
    # MongoDB ID
    id: Optional[Any] = Field(default_factory=ObjectId, alias="_id")
    
    provider_id: str = Field(..., description="Provider ID for indexing")
    raw_data: Any = Field(..., description="Raw data received from provider")
    timestamp: datetime = Field(default_factory=datetime.now, description="Timestamp of receipt")
    
    # Metadata for additional context if needed
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
