"""
Exchange DSM Serve Entities
"""

from datetime import datetime, date
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator


class ExchangeDsmServeEntity(BaseModel):
    """
    Entity representing exchange DSM serve data
    """
    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True
    )

    scheduled_at: datetime = Field(..., description="Date for which data is applicable")
    time_block: int = Field(..., description="Time block (1-96)")
    market_type: str = Field(..., description="Market type (e.g., DAM, RTM)")
    px_name: str = Field(..., description="Power Exchange name (e.g., PXIL)")
    
    total_buy_cleared_vol: float = Field(default=0.0, description="Cleared buy volume")
    total_sell_cleared_vol: float = Field(default=0.0, description="Cleared sell volume")
    price: float = Field(default=0.0, description="Market price")
    
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    @field_validator('scheduled_at', mode='before')
    @classmethod
    def parse_scheduled_at(cls, v):
        """Parse scheduled_at into datetime"""
        if isinstance(v, datetime):
            return v
        if isinstance(v, date):
            return datetime.combine(v, datetime.min.time())
        if isinstance(v, str):
            try:
                # Handle DD-Mon-YYYY format common in PXIL
                for fmt in ["%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"]:
                    try:
                        return datetime.strptime(v, fmt)
                    except ValueError:
                        continue
                return datetime.fromisoformat(v.replace('Z', '+00:00'))
            except ValueError:
                pass
        return v

    @classmethod
    def from_raw_data(cls, raw_data: Dict[str, Any]) -> "ExchangeDsmServeEntity":
        """
        Create entity from raw data
        """
        return cls(
            scheduled_at=raw_data.get("scheduled_at"),
            time_block=raw_data.get("time_block"),
            market_type=raw_data.get("market_type"),
            px_name=raw_data.get("px_name"),
            total_buy_cleared_vol=raw_data.get("total_buy_cleared_vol", 0.0),
            total_sell_cleared_vol=raw_data.get("total_sell_cleared_vol", 0.0),
            price=raw_data.get("price", 0.0)
        )


class ExchangeDsmHistoricalEntity(ExchangeDsmServeEntity):
    """
    Entity representing exchange DSM historical data
    """
    is_historical: bool = Field(default=True)
    stored_at: datetime = Field(default_factory=datetime.now)
