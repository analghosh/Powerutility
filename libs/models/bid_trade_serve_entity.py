"""
Bid Trade Serve Entity
Represents processed bid trade data for serving with timeseries support
"""

from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field, ConfigDict
from bson import ObjectId
from libs.enum.updated_flag import UpdatedFlag


class BidTradeServeEntity(BaseModel):
    """
    Bid Trade Serve Entity - Active time series data (4-day window)
    
    This entity represents the processed bid trade data that is actively served
    to the frontend applications. It contains a 4-day rolling window of data.
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
    
    # Time-related fields (for timeseries and 4-day window logic)
    scheduled_at: datetime = Field(..., description="Scheduled date and time for bid trade data")
    time_block: int = Field(..., description="Time block identifier (1-96)")
    
    # Processing timestamps (for timeseries)
    inserted_at: datetime = Field(default_factory=datetime.now, description="Insertion timestamp")
    updated_at: datetime = Field(default_factory=datetime.now, description="Last update timestamp")
    created_at: datetime = Field(default_factory=datetime.now, description="Creation timestamp")
    timestamp: datetime = Field(default_factory=datetime.now, description="Time series indexing timestamp")
    
    # Metadata fields
    data_type: str = Field(default="bid", description="Data type identifier")
    execution_id: Optional[str] = Field(None, description="Job execution ID from audit_db")
    order_id: Optional[str] = Field(None, description="Exchange Order ID")
    
    # Update flag (for change tracking)
    updated_flag: UpdatedFlag = Field(default=UpdatedFlag.UPDATED, description="Flag indicating if data was updated")
    
    # Revision tracking
    source_revision_number: Optional[int] = Field(None, description="Revision number from source data")
    system_revision_number: int = Field(default=1, description="System revision counter (increments on each update)")
    
    # BuySell Flag ('B' or 'S')
    buy_sell_flag: str = Field(..., description="BuySell Flag ('B' or 'S')")
    
    # Bid trade data fields (from raw data)
    type: str = Field(..., description="Bid type (DAM, GDAM, RTM, TAM)")
    bidded: Optional[float] = Field(None, description="Bidded amount")
    traded: float = Field(default=0.0, description="Traded amount")
    
    # Calculated fields
    value: float = Field(default=0.0, description="Calculated value (acp * traded / 4)")
    group: str = Field(..., description="Group classification (D or R)")
    
    # Source tracking
    source: Optional[str] = Field(None, description="Data source identifier")

    # Bid type: 'single' or 'block'
    bid_type: Optional[str] = Field(None, description="Bid type: 'single' or 'block'")
    
    @classmethod
    def from_raw_data(cls, raw_data: dict, metadata: dict, provider_config: dict, 
                     calculated_value: float = 0.0) -> "BidTradeServeEntity":
        """
        Create BidTradeServeEntity from a single raw bid trade data record
        
        Args:
            raw_data: Single bid trade record
            metadata: Processing metadata
            provider_config: Provider configuration
            calculated_value: Pre-calculated value from exchange ACP lookup
            
        Returns:
            BidTradeServeEntity: Populated bid trade entity
        """
        current_time = datetime.now()
        provider_metadata = provider_config.get("metadata", {})

        
        # Get bid type and map to group
        bid_type = raw_data.get("type", "")
        group = cls._map_type_to_group(bid_type)
        
        # Create entity with metadata and raw data
        entity_data = {
            "inserted_at": current_time,
            "created_at": current_time,
            "updated_at": current_time,
            "timestamp": current_time,
            "data_type": "bid",
            "execution_id": metadata.get("execution_id"),
            "updated_flag": UpdatedFlag.UPDATED,  # Default to UPDATED for new records
            
            # Bid trade fields from raw data
            "scheduled_at": raw_data.get("scheduled_at", current_time),
            "time_block": raw_data.get("time_block", 0),
            "type": bid_type,
            "bidded": cls._safe_float(raw_data.get("bidded")),
            "traded": cls._safe_float(raw_data.get("traded", 0)),
            
            # Calculated fields
            "value": calculated_value,
            "group": group,
            
            # Source tracking
            "source": provider_metadata.get("source"),
            "order_id": raw_data.get("order_id"),
        }
        
        return cls(**entity_data)
    
    @staticmethod
    def _map_type_to_group(bid_type: str) -> str:
        """
        Map bid type to group classification
        
        Args:
            bid_type: Bid type (DAM, GDAM, RTM, TAM)
            
        Returns:
            Group classification (D or R)
        """
        # Define type to group mapping based on business logic
        type_group_mapping = {
            "DAM": "D",    # Day Ahead Market -> D
            "GDAM": "D",   # Green Day Ahead Market -> D
            "RTM": "R",    # Real Time Market -> R
            "TAM": "T",    # Term Ahead Market -> T
        }
        
        return type_group_mapping.get(bid_type.upper(), "D")  # Default to D
    
    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        """Safely convert value to float, allowing None"""
        try:
            if value is None or value == "":
                return None
            return float(str(value).replace(",", ""))
        except (ValueError, TypeError):
            return None


class BidTradeServeHistoricalEntity(BaseModel):
    """
    Bid Trade Serve Historical Entity - Archived time series data
    
    This entity represents the historical bid trade data that has been archived
    from the active bid_trade_serve collection. It preserves the original _id
    for tracking purposes.
    """
    
    model_config = ConfigDict(
        validate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={
            ObjectId: str,
            datetime: lambda v: v.isoformat()
        }
    )

    # MongoDB ID (preserved from original)
    id: Optional[Any] = Field(default_factory=ObjectId, alias="_id")
    
    # All fields from BidTradeServeEntity
    scheduled_at: datetime = Field(..., description="Scheduled date and time for bid trade data")
    time_block: int = Field(..., description="Time block identifier (1-96)")
    
    # Metadata fields
    data_type: str = Field(default="bid", description="Data type identifier")
    execution_id: Optional[str] = Field(None, description="Job execution ID from audit_db")
    order_id: Optional[str] = Field(None, description="Exchange Order ID")
    
    # Update flag
    updated_flag: UpdatedFlag = Field(default=UpdatedFlag.UPDATED, description="Flag indicating if data was updated")
    
    # Revision tracking
    source_revision_number: Optional[int] = Field(None, description="Revision number from source data")
    system_revision_number: int = Field(default=1, description="System revision counter (increments on each update)")
    
    # BuySell Flag ('B' or 'S')
    buy_sell_flag: str = Field(..., description="BuySell Flag ('B' or 'S')")
    
    # Bid trade data fields
    type: str = Field(..., description="Bid type (DAM, GDAM, RTM, TAM)")
    bidded: Optional[float] = Field(None, description="Bidded amount")
    traded: float = Field(default=0.0, description="Traded amount")

    # Calculated fields
    value: float = Field(default=0.0, description="Calculated value (acp * traded / 4)")
    group: str = Field(..., description="Group classification (D or R)")

    # Bid type: 'single' or 'block'
    bid_type: Optional[str] = Field(None, description="Bid type: 'single' or 'block'")

    # Original processing timestamps (preserved)
    inserted_at: datetime = Field(..., description="Original insertion timestamp")
    updated_at: datetime = Field(..., description="Original last update timestamp")
    created_at: datetime = Field(..., description="Original creation timestamp")
    timestamp: datetime = Field(..., description="Original time series indexing timestamp")
    
    # Archive-specific timestamps
    archived_at: datetime = Field(default_factory=datetime.now, description="Archive timestamp")
    archived_timestamp: datetime = Field(default_factory=datetime.now, description="Archive timestamp for time series indexing")# Database Index Management
def ensure_bid_trade_serve_indexes(collection):
    """
    Ensure database indexes for bid_trade_serve collection
    
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
        
        # Bid type index
        collection.create_index("bid_type", background=True)
        
        # Market type index (DAM/RTM)
        collection.create_index("market_type", background=True)
        
        # Plant ID index for plant-specific bid queries
        collection.create_index("plant_id", background=True)
        
        # Compound index for time + market type queries (very common)
        collection.create_index([
            ("scheduled_at", 1),
            ("market_type", 1)
        ], background=True)
        
        # Compound index for time + plant queries
        collection.create_index([
            ("scheduled_at", 1),
            ("plant_id", 1)
        ], background=True)
        
        # Compound index for time block + market type
        collection.create_index([
            ("time_block", 1),
            ("market_type", 1)
        ], background=True)
        
        # Index for bid status filtering
        collection.create_index("bid_status", background=True)
        
        # Index for updated flag filtering
        collection.create_index("updated_flag", background=True)
        
        # Index for processing timestamps
        collection.create_index("inserted_at", background=True)
        collection.create_index("updated_at", background=True)
        
        # Index for order_id
        collection.create_index("order_id", background=True)
        
        logger.debug("✅ Bid trade serve collection indexes ensured")
        
    except Exception as e:
        logger.warning(f"⚠️ Failed to create bid trade serve indexes: {e}")

def ensure_bid_trade_serve_historical_indexes(collection):
    """
    Ensure database indexes for bid_trade_serve_historical collection
    
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
        
        # Market and bid type indexes for historical analysis
        collection.create_index("market_type", background=True)
        collection.create_index("bid_type", background=True)
        collection.create_index("plant_id", background=True)
        
        # Compound index for historical market-based queries
        collection.create_index([
            ("scheduled_at", 1),
            ("market_type", 1)
        ], background=True)
        
        # Time block index for historical data
        collection.create_index("time_block", background=True)
        
        # TTL index for automatic cleanup (optional - 1 year retention)
        # collection.create_index("archived_at", expireAfterSeconds=31536000, background=True)
        
        logger.debug("✅ Bid trade serve historical collection indexes ensured")
        
    except Exception as e:
        logger.warning(f"⚠️ Failed to create bid trade serve historical indexes: {e}")