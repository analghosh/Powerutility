from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional, Any
from pydantic import BaseModel, Field, ConfigDict


class MarketEnum(str, Enum):
    RTM = "RTM"
    GDAM = "GDAM"
    HPDAM = "HPDAM"
    DAM = "DAM"


class ExchangeEnum(str, Enum):
    IEX = "IEX"
    HPX = "HPX"
    PXIL = "PXIL"


class PowerExchangeEntity(BaseModel):
    model_config = ConfigDict(
        validate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={datetime: lambda v: v.isoformat()},
        json_schema_extra={
            "example": {
                "key": "max_bid_price",
                "market": "RTM",
                "exchange": "IEX",
                "value": 20.0,
                "created_at": "2026-07-07T07:20:00Z",
                "updated_at": "2026-07-07T07:50:00Z"
            }
        }
    )
    key: str = Field(...)
    market: MarketEnum = Field(...)
    exchange: ExchangeEnum = Field(...)
    value: Any = Field(...)
    created_at: Optional[datetime] = Field(None)
    updated_at: Optional[datetime] = Field(None)
