"""
Timeblock Entity Models
Defines the data structures for timeblock master collection
"""

from datetime import datetime, time
from typing import Optional, Any, Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator
from bson import ObjectId


class TimeblockEntity(BaseModel):
    """
    Timeblock Entity - Master timeblock data
    
    This entity represents the master timeblock data that defines the 15-minute
    intervals throughout a 24-hour period. Each timeblock represents a 15-minute
    interval, with 96 timeblocks per day (4 per hour).
    """
    
    model_config = ConfigDict(
        validate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={
            ObjectId: str,
            datetime: lambda v: v.isoformat(),
            time: lambda v: v.strftime("%H:%M")
        },
        json_schema_extra={
            "example": {
                "timeblock": 54,
                "starttime": "13:30",
                "endtime": "13:45",
                "fulltime": 14,
            }
        }
    )
    
    # MongoDB ID
    id: Optional[Any] = Field(default_factory=ObjectId, alias="_id")
    
    # Core timeblock fields
    timeblock: int = Field(..., description="Timeblock number (1-96)", ge=1, le=96)
    starttime: str = Field(..., description="Start time in HH:MM format")
    endtime: str = Field(..., description="End time in HH:MM format")
    fulltime: int = Field(..., description="Fulltime number (1-24)", ge=1, le=24)
    
    # Additional metadata# Database Index Management
def ensure_timeblock_indexes(collection):
    """
    Ensure database indexes for timeblock collection
    
    Args:
        collection: MongoDB collection instance
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        # Primary timeblock number index (most common query)
        collection.create_index("timeblock", unique=True, background=True)
        
        # Time-based indexes for time range queries
        collection.create_index("starttime", background=True)
        collection.create_index("endtime", background=True)
        
        # Compound index for time range queries
        collection.create_index([
            ("starttime", 1),
            ("endtime", 1)
        ], background=True)
        
        # Full time index for exact time matching
        collection.create_index("fulltime", background=True)
        
        # Compound index for timeblock + time queries
        collection.create_index([
            ("timeblock", 1),
            ("starttime", 1)
        ], background=True)
        
        logger.debug("✅ Timeblock collection indexes ensured")
        
    except Exception as e:
        logger.warning(f"⚠️ Failed to create timeblock indexes: {e}")
