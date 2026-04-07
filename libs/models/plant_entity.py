"""
Plant Entity Model
Represents power plant information
"""

from datetime import datetime
from typing import Optional, Any, List, Dict
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator
from bson import ObjectId


class PlantEntity(BaseModel):
    """Plant entity model"""
    
    id: Optional[Any] = Field(default_factory=ObjectId, alias="_id")
    # Plant information
    plant_name: str = Field(..., description="Name of the power plant")
    c_s: Optional[str] = Field(default=None, description="Central/State")
    category: Optional[str] = Field(default=None, description="Category")
    technology: Optional[str] = Field(default=None, description="Technology")
    price_group: Optional[str] = Field(default=None, description="Price group")
    ramping: Optional[float] = Field(default=None, description="Ramping")
    tm: Optional[float] = Field(default=None, description="TM")
    fc_price: Optional[float] = Field(default=None, description="FC price")
    px_price: Optional[float] = Field(default=None, description="PX price")
    vc_price: Optional[float] = Field(default=None, description="VC price")
    multiplier: Optional[float] = Field(default=None, description="Multiplier")
    volume: Optional[float] = Field(default=None, description="Volume")
    ppa: Optional[float] = Field(default=None, description="PPA")
    plant_id: Optional[str] = Field(default=None, description="Plant ID")
    dispatch_type: Optional[str] = Field(default=None, description="Dispatch type")
    approval_no: Optional[str] = Field(default=None, description="Approval Number")
    min_up_time: Optional[int] = Field(default=None, description="Minimum up time")
    min_shut_down: Optional[int] = Field(default=None, description="Minimum shut down time")
    max_shut_down: Optional[int] = Field(default=None, description="Maximum shut down time")
    # Processing timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Creation timestamp")
    updated_at: datetime = Field(default_factory=datetime.utcnow, description="Last update timestamp")
    
    # Model configuration
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={
            ObjectId: str,
            datetime: lambda v: v.isoformat()
        },
        json_schema_extra={
            "example": {
                "plant_name": "Sample Power Plant",
                "c_s": "Central",
                "category": "Thermal",
                "technology": "Coal",
                "price_group": "A",
                "ramping": 100.0,
                "tm": 50.0,
                "fc_price": 2.5,
                "px_price": 3.2,
                "vc_price": 1.8,
                "multiplier": 1.0,
                "volume": 500.0,
                "ppa": "PPA123",
                "plant_id": "SPP",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z"
            }
        }
    )# Database Index Management
def ensure_plant_indexes(collection):
    """
    Ensure database indexes for plant_master collection
    
    Args:
        collection: MongoDB collection instance
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        # Primary plant ID index (most common query)
        collection.create_index("plant_id", unique=True, background=True)
        
        # Plant name index for name-based lookups
        collection.create_index("plant_name", background=True)
        
        # Plant type index for filtering by type
        collection.create_index("plant_type", background=True)
        
        # Fuel type index for fuel-based queries
        collection.create_index("fuel_type", background=True)
        
        # Capacity index for capacity-based filtering
        collection.create_index("capacity", background=True)
        
        # Status index for active/inactive plants
        collection.create_index("status", background=True)
        
        # Location-based indexes
        collection.create_index("state", background=True)
        collection.create_index("region", background=True)
        
        # Compound index for type + fuel queries
        collection.create_index([
            ("plant_type", 1),
            ("fuel_type", 1)
        ], background=True)
        
        # Compound index for location queries
        collection.create_index([
            ("state", 1),
            ("region", 1)
        ], background=True)
        
        # Index for processing timestamps
        collection.create_index("created_at", background=True)
        collection.create_index("updated_at", background=True)
        
        logger.debug("✅ Plant master collection indexes ensured")
        
    except Exception as e:
        logger.warning(f"⚠️ Failed to create plant indexes: {e}")