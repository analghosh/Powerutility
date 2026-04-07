"""
Exchange Trade entities
Defines data models for exchange trade serve and historical data
"""

from datetime import datetime, date
from typing import Dict, Any, List, Optional, Union
from bson import ObjectId
from pydantic import BaseModel, Field, ConfigDict
from libs.enum.updated_flag import UpdatedFlag


class ExchangeTradeServeEntity(BaseModel):
    """
    Exchange Trade Serve Entity
    
    This entity represents the processed exchange trade data that is actively served
    to the frontend applications. It contains a 4-day rolling window of data.
    """
    
    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={
            ObjectId: str,
            datetime: lambda v: v.isoformat(),
            date: lambda v: v.isoformat()
        }
    )
    
    # MongoDB ID
    id: Optional[Any] = Field(default_factory=ObjectId, alias="_id")
    
    # Time-related fields (for timeseries and 4-day window logic)
    scheduled_at: datetime = Field(..., description="Scheduled datetime for exchange data")
    time_block: int = Field(..., description="Time block identifier (1-96)")
    
    # Processing timestamps (for timeseries)
    inserted_at: datetime = Field(default_factory=datetime.now, description="Insertion timestamp")
    updated_at: datetime = Field(default_factory=datetime.now, description="Last update timestamp")
    created_at: datetime = Field(default_factory=datetime.now, description="Creation timestamp")
    timestamp: datetime = Field(default_factory=datetime.now, description="Time series indexing timestamp")
    
    # Market identification fields
    market_type: str = Field(..., description="Type of market data (DAM/RTM/GDAM/HPDAM)")
    px_name: str = Field(default="HPX", description="Power exchange name (HPX)")
    data_type: str = Field(default="exchange_trade", description="Data type identifier")
    
    # Exchange data fields
    Total_Buy_Cleared_Vol: float = Field(default=0.0, description="Total Buy Cleared Volume")
    Total_Sell_Cleared_Vol: float = Field(default=0.0, description="Total Sell Cleared Volume")
    Price: float = Field(default=0.0, description="Price")
    TimePeriod: str = Field(default="", description="Time Period string e.g. 00:00:00-00:15:00")
    
    @classmethod
    def from_raw_data(cls, raw_data: dict, market_type: str, px_name: str = "HPX") -> "ExchangeTradeServeEntity":
        """
        Create ExchangeTradeServeEntity from a single raw exchange trade data record
        """
        current_time = datetime.now()
        
        # Get scheduled date and convert to datetime object
        scheduled_at_raw = raw_data.get("scheduled_at")
        if isinstance(scheduled_at_raw, datetime):
            scheduled_at = scheduled_at_raw
        elif isinstance(scheduled_at_raw, date):
            scheduled_at = datetime.combine(scheduled_at_raw, datetime.min.time())
        elif isinstance(scheduled_at_raw, str):
            try:
                # Truncate at T just in case
                if "T" in scheduled_at_raw:
                    scheduled_at_raw = scheduled_at_raw.split("T")[0]
                scheduled_at = datetime.strptime(scheduled_at_raw, "%Y-%m-%d")
            except ValueError:
                scheduled_at = datetime.combine(current_time.date(), datetime.min.time())
        else:
            scheduled_at = datetime.combine(current_time.date(), datetime.min.time())
            
        time_block = int(raw_data.get("TimeBlock", 0))
        
        return cls(
            inserted_at=current_time,
            created_at=current_time,
            updated_at=current_time,
            timestamp=current_time,
            scheduled_at=scheduled_at,
            time_block=time_block,
            market_type=market_type,
            px_name=px_name,
            Total_Buy_Cleared_Vol=cls._safe_float(raw_data.get("Total_Buy_Cleared_Vol", 0)),
            Total_Sell_Cleared_Vol=cls._safe_float(raw_data.get("Total_Sell_Cleared_Vol", 0)),
            Price=cls._safe_float(raw_data.get("Price", 0)),
            TimePeriod=raw_data.get("TimePeriod", "")
        )

    @staticmethod
    def _safe_float(value: Any) -> float:
        """Safely convert value to float"""
        try:
            if value is None or value == "":
                return 0.0
            return float(str(value).replace(",", ""))
        except (ValueError, TypeError):
            return 0.0

    def model_dump_for_db(self) -> Dict[str, Any]:
        """Convert entity to dictionary for database storage"""
        return self.model_dump(by_alias=True)

class ExchangeTradeHistoricalEntity(BaseModel):
    """
    Exchange Trade Historical Entity
    Archived time series data
    """
    
    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={
            ObjectId: str,
            datetime: lambda v: v.isoformat(),
            date: lambda v: v.isoformat()
        }
    )

    id: Optional[Any] = Field(default_factory=ObjectId, alias="_id")
    
    # All fields from ExchangeTradeServeEntity
    scheduled_at: datetime = Field(..., description="Scheduled datetime for exchange data")
    time_block: int = Field(..., description="Time block identifier (1-96)")
    
    market_type: str = Field(..., description="Type of market data (DAM/RTM/GDAM/HPDAM)")
    px_name: str = Field(default="HPX", description="Power exchange name (HPX)")
    data_type: str = Field(default="exchange_trade", description="Data type identifier")
    
    Total_Buy_Cleared_Vol: float = Field(default=0.0)
    Total_Sell_Cleared_Vol: float = Field(default=0.0)
    Price: float = Field(default=0.0)
    TimePeriod: str = Field(default="")
    
    inserted_at: datetime = Field(..., description="Original insertion timestamp")
    updated_at: datetime = Field(..., description="Original last update timestamp")
    created_at: datetime = Field(..., description="Original creation timestamp")
    timestamp: datetime = Field(..., description="Original time series indexing timestamp")
    
    archived_at: datetime = Field(default_factory=datetime.now)
    archived_timestamp: datetime = Field(default_factory=datetime.now)

# Database Index Management
def ensure_exchange_trade_serve_indexes(collection):
    """Ensure database indexes for exchange_trade_serve collection"""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        collection.create_index([("scheduled_at", 1)], background=True)
        collection.create_index("time_block", background=True)
        collection.create_index("market_type", background=True)
        collection.create_index([
            ("scheduled_at", 1),
            ("market_type", 1),
            ("px_name", 1),
            ("time_block", 1)
        ], background=True, unique=True)
        logger.debug("✅ Exchange trade serve collection indexes ensured")
    except Exception as e:
        logger.warning(f"⚠️ Failed to create exchange trade serve indexes: {e}")

def ensure_exchange_trade_serve_historical_indexes(collection):
    """Ensure database indexes for exchange_trade_serve_historical collection"""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        collection.create_index([("scheduled_at", 1)], background=True)
        collection.create_index([("archived_at", -1)], background=True)
        collection.create_index("market_type", background=True)
        collection.create_index([
            ("scheduled_at", 1),
            ("market_type", 1)
        ], background=True)
        collection.create_index("time_block", background=True)
        logger.debug("✅ Exchange trade serve historical collection indexes ensured")
    except Exception as e:
        logger.warning(f"⚠️ Failed to create exchange trade serve historical indexes: {e}")
