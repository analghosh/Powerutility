"""
Supply Serve Entity Models
Defines the data structures for supply serve and historical collections
"""

from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field, ConfigDict, field_validator
from bson import ObjectId
from libs.enum.updated_flag import UpdatedFlag


class SupplyServeEntity(BaseModel):
    """
    Supply Serve Entity - Active time series data (4-day window)
    
    This entity represents the processed supply data that is actively served
    to the frontend applications. It contains a 4-day rolling window of data.
    """
    
    model_config = ConfigDict(
        validate_by_name=True,
        arbitrary_types_allowed=True,
        use_enum_values=True,
        json_encoders={
            ObjectId: str,
            datetime: lambda v: v.isoformat()
        },
        json_schema_extra={
            "example": {
                "scheduled_at": "2025-01-15T10:00:00",
                "plant_name": "Thermal Plant A",
                "dc_sg": "DC",
                "volume": 500.0,
                "fc_price": 2.5,
                "px_price": 3.2,
                "vc_price": 1.8,
                "category": "Thermal",
                "technology": "Coal",
                "price_group": "A",
                "c_s": "Central",
                "archived_at": "2025-01-19T10:00:00"
            }
        }
    )
    
    # MongoDB ID
    id: Optional[Any] = Field(default_factory=ObjectId, alias="_id")
    # Time-related fields
    scheduled_at: datetime = Field(..., description="Schedule date and time")
    time_block: Optional[int] = Field(None, description="Time block identifier")
    
    # Plant and supply information
    plant_id: Optional[str] = Field(None, description="Plant ID")
    dc_sg: Optional[str] = Field(None, description="DC/SG type (DC/SG)")
    volume: Optional[float] = Field(None, description="Supply volume")
    sg_beforelosses: Optional[float] = Field(None, description="SG Before Losses")
    
    # Price information
    fc_price: Optional[float] = Field(None, description="FC (Fixed Cost) price")
    px_price: Optional[float] = Field(None, description="PX (Power Exchange) price")
    vc_price: Optional[float] = Field(None, description="VC (Variable Cost) price")
    
    # Categorization fields
    price_group: Optional[str] = Field(None, description="Price group identifier")
    
    # Additional fields
    dam_tm: float = Field(default=0.0, description="DAM Technical Minimum value (55% of DC volume)")
    rtm_tm: Optional[float] = Field(default=None, description="RTM Technical Minimum value (55% of DC volume)")
    multiplier: float = Field(default=0.0, description="Multiplier value")
    ramping: str = Field(default="", description="Ramping information")
    ramp_up: Optional[float] = Field(None, description="Ramp up value")
    ramp_down: Optional[float] = Field(None, description="Ramp down value")
    dc_afterloss: Optional[float] = Field(None, description="DC after loss value")
    entire_shared_onbar_ent_amount: Optional[float] = Field(None, description="Entire Shared Onbar Entitlement Amount")
    entire_shared_offbar_ent_amount: Optional[float] = Field(None, description="Entire Shared Offbar Entitlement Amount")
    rtm_max: Optional[float] = Field(None, description="RTM Max value (SG of tomorrow at 10:10 AM, locked once set)")
    rtm_max_revision_no: Optional[int] = Field(None, description="RLDC revision number when rtm_max was stored")
    rtm_tm_revision_no: Optional[int] = Field(None, description="RLDC revision number when rtm_tm was stored")

    # Max MW DC for SG records (locked value from 08:00-08:14 tomorrow data)
    max_mw_dc: Optional[float] = Field(None, description="Max MW DC value (copied from volume for SG on tomorrow's 08:00-08:14 data)")
    eightThirtyValue: Optional[float] = Field(None, description="8:30 value for SG records (copied from volume for SG on tomorrow's 08:00-08:14 data)")
    # Source tracking
    source: Optional[str] = Field(None, description="Data source identifier")
    
    # Processing timestamps
    inserted_at: datetime = Field(default_factory=datetime.now, description="Insertion timestamp")
    updated_at: datetime = Field(default_factory=datetime.now, description="Last update timestamp")
    created_at: datetime = Field(default_factory=datetime.now, description="Creation timestamp")
    processed_at: datetime = Field(default_factory=datetime.now, description="Processing timestamp")
    timestamp: datetime = Field(default_factory=datetime.now, description="Time series indexing timestamp")
    updated_flag: UpdatedFlag = Field(default=UpdatedFlag.UPDATED, description="Flag indicating if data was updated")
    
    # Revision tracking
    source_revision_number: Optional[int] = Field(None, description="Revision number from source data")
    system_revision_number: int = Field(default=1, description="System revision counter (increments on each update)")


class SupplyServeHistoricalEntity(BaseModel):
    """
    Supply Serve Historical Entity - Archived time series data
    
    This entity represents the historical supply data that has been archived
    from the active supply_serve collection. It preserves the original _id
    for tracking purposes.
    """
    
    model_config = ConfigDict(
        validate_by_name=True,
        arbitrary_types_allowed=True,
        use_enum_values=True,
        json_encoders={
            ObjectId: str,
            datetime: lambda v: v.isoformat()
        },
        json_schema_extra={
            "example": {
                "scheduled_at": "2025-01-15T10:00:00",
                "plant_name": "Thermal Plant A",
                "dc_sg": "DC",
                "volume": 500.0,
                "fc_price": 2.5,
                "px_price": 3.2,
                "vc_price": 1.8,
                "category": "Thermal",
                "technology": "Coal",
                "price_group": "A",
                "c_s": "Central",
                "archived_at": "2025-01-19T10:00:00"
            }
        }
    )
    
    # MongoDB ID (preserved from original)
    id: Optional[Any] = Field(default_factory=ObjectId, alias="_id")
    # All fields from SupplyServeEntity
    scheduled_at: datetime = Field(..., description="Schedule date and time")
    time_block: Optional[int] = Field(None, description="Time block identifier")
    
    # Plant and supply information
    plant_id: Optional[str] = Field(None, description="Plant ID")
    dc_sg: Optional[str] = Field(None, description="DC/SG type (DC/SG)")
    volume: Optional[float] = Field(None, description="Supply volume")
    sg_beforelosses: Optional[float] = Field(None, description="SG Before Losses")
    
    # Price information
    fc_price: Optional[float] = Field(None, description="FC (Fixed Cost) price")
    px_price: Optional[float] = Field(None, description="PX (Power Exchange) price")
    vc_price: Optional[float] = Field(None, description="VC (Variable Cost) price")
    
    # Categorization fields
    price_group: Optional[str] = Field(None, description="Price group identifier")
    
    # Additional fields
    dam_tm: float = Field(default=0.0, description="DAM Technical Minimum value (55% of DC volume)")
    rtm_tm: float = Field(default=0.0, description="RTM Technical Minimum value (55% of DC volume)")
    multiplier: float = Field(default=0.0, description="Multiplier value")
    ramping: str = Field(default="", description="Ramping information")
    ramp_up: Optional[float] = Field(None, description="Ramp up value")
    ramp_down: Optional[float] = Field(None, description="Ramp down value")
    dc_afterloss: Optional[float] = Field(None, description="DC after loss value")
    entire_shared_onbar_ent_amount: Optional[float] = Field(None, description="Entire Shared Onbar Entitlement Amount")
    entire_shared_offbar_ent_amount: Optional[float] = Field(None, description="Entire Shared Offbar Entitlement Amount")
    rtm_max: Optional[float] = Field(None, description="RTM Max value (SG of tomorrow at 10:10 AM, locked once set)")
    rtm_max_revision_no: Optional[int] = Field(None, description="RLDC revision number when rtm_max was stored")
    rtm_tm_revision_no: Optional[int] = Field(None, description="RLDC revision number when rtm_tm was stored")

    # Original processing timestamps (preserved)
    inserted_at: datetime = Field(..., description="Original insertion timestamp")
    updated_at: datetime = Field(..., description="Original last update timestamp")
    created_at: datetime = Field(..., description="Original creation timestamp")
    processed_at: datetime = Field(..., description="Original processing timestamp")
    timestamp: datetime = Field(..., description="Original time series indexing timestamp")
    updated_flag: UpdatedFlag = Field(..., description="Original updated flag")
    
    # Revision tracking
    source_revision_number: Optional[int] = Field(None, description="Revision number from source data")
    system_revision_number: int = Field(default=1, description="System revision counter (increments on each update)")
    
    # Archive-specific timestamps
    archived_at: datetime = Field(default_factory=datetime.now, description="Archive timestamp")
    archived_timestamp: datetime = Field(default_factory=datetime.now, description="Archive timestamp for time series indexing")# Database Index Management
def ensure_supply_serve_indexes(collection):
    """
    Ensure database indexes for supply_serve collection
    
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
        
        # Plant ID index for plant-specific queries
        collection.create_index("plant_id", background=True)
        
        # Plant name index for plant lookups
        collection.create_index("plant_name", background=True)
        
        # Compound index for time + plant queries (very common)
        collection.create_index([
            ("scheduled_at", 1),
            ("plant_id", 1)
        ], background=True)
        
        # Compound index for time block + plant
        collection.create_index([
            ("time_block", 1),
            ("plant_id", 1)
        ], background=True)
        
        # Index for updated flag filtering
        collection.create_index("updated_flag", background=True)
        
        # Index for processing timestamps
        collection.create_index("inserted_at", background=True)
        collection.create_index("updated_at", background=True)
        
        # Index for supply type filtering
        collection.create_index("supply_type", background=True)
        
        logger.debug("✅ Supply serve collection indexes ensured")
        
    except Exception as e:
        logger.warning(f"⚠️ Failed to create supply serve indexes: {e}")

def ensure_supply_serve_historical_indexes(collection):
    """
    Ensure database indexes for supply_serve_historical collection
    
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
        
        # Plant indexes for historical analysis
        collection.create_index("plant_id", background=True)
        collection.create_index("plant_name", background=True)
        
        # Compound index for historical plant-based queries
        collection.create_index([
            ("scheduled_at", 1),
            ("plant_id", 1)
        ], background=True)
        
        # Time block index for historical data
        collection.create_index("time_block", background=True)
        
        # TTL index for automatic cleanup (optional - 1 year retention)
        # collection.create_index("archived_at", expireAfterSeconds=31536000, background=True)
        
        logger.debug("✅ Supply serve historical collection indexes ensured")
        
    except Exception as e:
        logger.warning(f"⚠️ Failed to create supply serve historical indexes: {e}")