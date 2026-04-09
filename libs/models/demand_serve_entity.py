"""
Demand Serve Entity Models
Defines the data structures for demand serve and historical collections
"""

from datetime import datetime
from typing import Optional, Any, Literal
from pydantic import BaseModel, Field, ConfigDict
from bson import ObjectId
from libs.enum.updated_flag import UpdatedFlag


class DemandServeEntity(BaseModel):
    """
    Demand Serve Entity - Active time series data (4-day window)
    
    This entity represents the processed demand data that is actively served
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
                "type": "IDF",
                "forecast": 1500.0,
                "actual": 1480.0,
                "rostering": 20.0,
                "mape": 1.33,
                "mpe": -1.33,
                "zone": "North",
                "udm": 1500.0
            }
        }
    )
    
    # MongoDB ID
    id: Optional[Any] = Field(default_factory=ObjectId, alias="_id")
    
    # Time-related fields
    scheduled_at: datetime = Field(..., description="Schedule date and time")
    time_block: Optional[int] = Field(None, description="Time block identifier")
    
    # Processing timestamps
    inserted_at: datetime = Field(default_factory=datetime.now, description="Insertion timestamp")
    updated_at: datetime = Field(default_factory=datetime.now, description="Last update timestamp")
    created_at: datetime = Field(default_factory=datetime.now, description="Creation timestamp")
    timestamp: datetime = Field(default_factory=datetime.now, description="Time series indexing timestamp")
    
    # Market and type fields
    type: Optional[Literal["IDF", "DAF"]] = Field(None, description="Market type (IDF/DAF)")
    
    # Demand values
    forecast: float = Field(default=0.0, description="Forecasted demand value")
    actual: Optional[float] = Field(None, description="Actual demand value")
    schedule: Optional[float] = Field(None, description="Scheduled demand value")
    drawal: Optional[float] = Field(None, description="Drawal value")
    frequency: Optional[float] = Field(None, description="Raw frequency value")
    
    # Rostering and calculations
    rostering: float = Field(default=0.0, description="Rostering value")
    mape: float = Field(default=0.0, description="Mean Absolute Percentage Error")
    mpe: float = Field(default=0.0, description="Mean Percentage Error")
    
    # Additional fields
    remarks: str = Field(default="", description="Additional remarks")
    source_actual: str = Field(default="", description="Source of actual data")
    source_forecast: str = Field(default="", description="Source of forecast data")
    source_rostering: str = Field(default="", description="Source of rostering data")
    
    # Zone and area
    zone: Optional[str] = Field(None, description="Zone/area identifier")
    
    # Source tracking
    source: Optional[str] = Field(None, description="Data source identifier")
    
    # DSM related fields
    comp_act_demand: float = Field(default=0.0, description="Computed actual demand")
    net_dsm_amount: Optional[float] = Field(None, description="Net DSM amount")
    dsm_rate: Optional[float] = Field(None, description="DSM rate")
    
    # UDM (Unified Demand Management)
    udm: float = Field(default=0.0, description="Unified Demand Management value")
    comp_udm: float = Field(default=0.0, description="Computed UDM value")
    forecasted_frequency: float = Field(default=0.0, description="Forecasted frequency value")

    # New fields for Demand Multiplier and Drawal Before Loss
    demand_multiplier: float = Field(default=0.0, description="Average of valid plant multipliers")
    drawal_before_loss: Optional[float] = Field(None, description="Drawal divided by demand_multiplier")
    
    # New fields for Demand Multiplier and Drawal Before Loss
    demand_multiplier: float = Field(default=0.0, description="Average of valid plant multipliers")
    drawal_before_loss: Optional[float] = Field(None, description="Drawal divided by demand_multiplier")
    
    # Update flag
    updated_flag: UpdatedFlag = Field(default=UpdatedFlag.UPDATED, description="Flag indicating if data was updated")
    
    # Revision tracking
    source_revision_number: Optional[int] = Field(None, description="Revision number from source data")
    system_revision_number: int = Field(default=1, description="System revision counter (increments on each update)")

class DemandServeHistoricalEntity(BaseModel):
    """
    Demand Serve Historical Entity - Archived time series data
    
    This entity represents the historical demand data that has been archived
    from the active demand_serve collection. It preserves the original _id
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
                "type": "IDF",
                "forecast": 1500.0,
                "actual": 1480.0,
                "rostering": 20.0,
                "mape": 1.33,
                "mpe": -1.33,
                "zone": "North",
                "udm": 1500.0,
                "archived_at": "2025-01-19T10:00:00"
            }
        }
    )
    
    # MongoDB ID (preserved from original)
    id: Optional[Any] = Field(default_factory=ObjectId, alias="_id")
    
    # All fields from DemandServeEntity
    scheduled_at: datetime = Field(..., description="Schedule date and time")
    time_block: Optional[int] = Field(None, description="Time block identifier")
    
    # Market and type fields
    type: Optional[Literal["IDF", "DAF"]] = Field(None, description="Market type (IDF/DAF)")
    
    # Demand values
    forecast: float = Field(default=0.0, description="Forecasted demand value")
    actual: Optional[float] = Field(None, description="Actual demand value")
    schedule: Optional[float] = Field(None, description="Scheduled demand value")
    drawal: Optional[float] = Field(None, description="Drawal value")
    frequency: Optional[float] = Field(None, description="Raw frequency value")
    
    # Rostering and calculations
    rostering: float = Field(default=0.0, description="Rostering value")
    mape: float = Field(default=0.0, description="Mean Absolute Percentage Error")
    mpe: float = Field(default=0.0, description="Mean Percentage Error")
    
    # Additional fields
    remarks: str = Field(default="", description="Additional remarks")
    source_actual: str = Field(default="", description="Source of actual data")
    source_forecast: str = Field(default="", description="Source of forecast data")
    source_rostering: str = Field(default="", description="Source of rostering data")
    
    # Zone and area
    zone: Optional[str] = Field(None, description="Zone/area identifier")
    
    # DSM related fields
    comp_act_demand: float = Field(default=0.0, description="Computed actual demand")
    net_dsm_amount: Optional[float] = Field(None, description="Net DSM amount")
    dsm_rate: Optional[float] = Field(None, description="DSM rate")
    forecasted_frequency: float = Field(default=0.0, description="Forecasted frequency value")

    # New fields for Demand Multiplier and Drawal Before Loss
    demand_multiplier: float = Field(default=0.0, description="Average of valid plant multipliers")
    drawal_before_loss: Optional[float] = Field(None, description="Drawal divided by demand_multiplier")
    
    # UDM (Unified Demand Management)
    udm: float = Field(default=0.0, description="Unified Demand Management value")
    comp_udm: float = Field(default=0.0, description="Computed UDM value")
    
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

# Database Index Management
def ensure_demand_serve_indexes(collection):
    """
    Ensure database indexes for demand_serve collection
    
    Args:
        collection: MongoDB collection instance
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        # Primary time-based index (most common query)
        collection.create_index([("scheduled_at", 1)], background=True)
        
        # Time block index for time-based queries
        collection.create_index("time_block", background=True)
        
        # Type index for filtering by demand type
        collection.create_index("type", background=True)
        
        # Zone index for regional queries
        collection.create_index("zone", background=True)
        
        # Compound index for time + type queries (very common)
        collection.create_index([
            ("scheduled_at", 1),
            ("type", 1)
        ], background=True)
        
        # Compound index for time + zone queries
        collection.create_index([
            ("scheduled_at", 1),
            ("zone", 1)
        ], background=True)
        
        # Compound index for time block + scheduled_at
        collection.create_index([
            ("time_block", 1),
            ("scheduled_at", 1)
        ], background=True)
        
        # Index for updated flag filtering
        collection.create_index("updated_flag", background=True)
        
        # Index for processing timestamps
        collection.create_index("inserted_at", background=True)
        collection.create_index("updated_at", background=True)
        
        logger.debug("✅ Demand serve collection indexes ensured")
        
    except Exception as e:
        logger.warning(f"⚠️ Failed to create demand serve indexes: {e}")

def ensure_demand_serve_historical_indexes(collection):
    """
    Ensure database indexes for demand_serve_historical collection
    
    Args:
        collection: MongoDB collection instance
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        # Primary time-based index for historical queries
        collection.create_index([("scheduled_at", 1)], background=True)
        
        # Archive timestamp index
        collection.create_index([("archived_at", -1)], background=True)
        collection.create_index([("archived_timestamp", -1)], background=True)
        
        # Type and zone indexes for historical analysis
        collection.create_index("type", background=True)
        collection.create_index("zone", background=True)
        
        # Compound index for historical time-based queries
        collection.create_index([
            ("scheduled_at", 1),
            ("type", 1)
        ], background=True)
        
        # Time block index for historical data
        collection.create_index("time_block", background=True)
        
        # TTL index for automatic cleanup (optional - 1 year retention)
        # collection.create_index("archived_at", expireAfterSeconds=31536000, background=True)
        
        logger.debug("✅ Demand serve historical collection indexes ensured")
        
    except Exception as e:
        logger.warning(f"⚠️ Failed to create demand serve historical indexes: {e}")