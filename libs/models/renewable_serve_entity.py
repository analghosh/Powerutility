"""
Renewable Energy Serve Entity Models
Defines the data structures for renewable energy serve and historical collections
"""

from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field, ConfigDict
from bson import ObjectId
from libs.enum.updated_flag import UpdatedFlag


class RenewableServeEntity(BaseModel):
    """
    Renewable Energy Serve Entity - Active time series data (4-day window)
    
    This entity represents the processed renewable energy data that is actively served
    to the frontend applications. It contains a 4-day rolling window of data.
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
                "scheduled_at": "2025-01-15T10:00:00",
                "time_block": 1,
                "type": "Solar",
                "plant_id": "SOLAR_001",
                "schedule_volume": 150.5,
                "approval_no": "APR_001",
                "revision_no": 1
            }
        }
    )
    
    # MongoDB ID
    id: Optional[Any] = Field(default_factory=ObjectId, alias="_id")
    
    # Core renewable energy fields
    scheduled_at: datetime = Field(..., description="Schedule date and time")
    time_block: Optional[int] = Field(None, description="Time block identifier")
    type: Optional[str] = Field(None, description="Renewable energy type (Solar, Wind, Hydro, etc.)")
    plant_id: Optional[str] = Field(None, description="Plant identifier")
    schedule_volume: float = Field(default=0.0, description="Scheduled volume/capacity")
    sg_beforelosses: Optional[float] = Field(None, description="SG Before Losses")
    approval_no: Optional[str] = Field(None, description="Approval number")
    revision_no: Optional[int] = Field(None, description="Revision number")
    
    # Processing timestamps
    inserted_at: datetime = Field(default_factory=datetime.now, description="Insertion timestamp")
    updated_at: datetime = Field(default_factory=datetime.now, description="Last update timestamp")
    created_at: datetime = Field(default_factory=datetime.now, description="Creation timestamp")
    timestamp: datetime = Field(default_factory=datetime.now, description="Time series indexing timestamp")
    
    # Update flag
    updated_flag: UpdatedFlag = Field(default=UpdatedFlag.UPDATED, description="Flag indicating if data was updated")

    # Source tracking
    source: Optional[str] = Field(
        None,
        description="Data origin: 'upload', 'api', 'dc_sg_rollover' (rolled over from previous day), or other ingestion type"
    )

    # Revision tracking
    source_revision_number: Optional[int] = Field(None, description="Revision number from source data")
    system_revision_number: int = Field(default=1, description="System revision counter (increments on each update)")


class RenewableServeHistoricalEntity(BaseModel):
    """
    Renewable Energy Serve Historical Entity - Archived time series data
    
    This entity represents the historical renewable energy data that has been archived
    from the active renewable_serve collection. It preserves the original _id
    for tracking purposes.
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
                "scheduled_at": "2025-01-15T10:00:00",
                "time_block": 1,
                "type": "Solar",
                "plant_id": "SOLAR_001",
                "schedule_volume": 150.5,
                "approval_no": "APR_001",
                "revision_no": 1,
                "archived_at": "2025-01-19T10:00:00"
            }
        }
    )
    
    # MongoDB ID (preserved from original)
    id: Optional[Any] = Field(default_factory=ObjectId, alias="_id")
    
    # All fields from RenewableServeEntity
    scheduled_at: datetime = Field(..., description="Schedule date and time")
    time_block: Optional[int] = Field(None, description="Time block identifier")
    type: Optional[str] = Field(None, description="Renewable energy type (Solar, Wind, Hydro, etc.)")
    plant_id: Optional[str] = Field(None, description="Plant identifier")
    schedule_volume: float = Field(default=0.0, description="Scheduled volume/capacity")
    sg_beforelosses: Optional[float] = Field(None, description="SG Before Losses")
    approval_no: Optional[str] = Field(None, description="Approval number")
    revision_no: Optional[int] = Field(None, description="Revision number")
    
    # Update flag
    updated_flag: UpdatedFlag = Field(default=UpdatedFlag.UPDATED, description="Flag indicating if data was updated")
    
    # Revision tracking
    source_revision_number: Optional[int] = Field(None, description="Revision number from source data")
    system_revision_number: int = Field(default=1, description="System revision counter (increments on each update)")
    
    # Original processing timestamps (preserved)
    inserted_at: datetime = Field(..., description="Original insertion timestamp")
    updated_at: datetime = Field(..., description="Original last update timestamp")
    created_at: datetime = Field(..., description="Original creation timestamp")
    timestamp: datetime = Field(..., description="Original time series indexing timestamp")
    
    # Archive-specific timestamps
    archived_at: datetime = Field(default_factory=datetime.now, description="Archive timestamp")
    archived_timestamp: datetime = Field(default_factory=datetime.now, description="Archive timestamp for time series indexing")