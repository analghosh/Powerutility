"""
Peak Demand Serve Entity
Represents daily peak demand record derived from demand_serve (IDF only).
"""

from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field, ConfigDict
from bson import ObjectId


class PeakDemandServeEntity(BaseModel):
    """Daily peak demand data for a schedule date with its time block."""

    model_config = ConfigDict(
        validate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={
            ObjectId: str,
            datetime: lambda v: v.isoformat(),
        },
        json_schema_extra={
            "example": {
                "scheduled_at": "2025-10-07T00:00:00",
                "peak_demand": 4321.5,
                "average_demand": 3850.2,
                "time_block": 57,
                "created_at": "2025-10-08T00:00:05",
            }
        },
    )

    # MongoDB ID
    id: Optional[Any] = Field(default_factory=ObjectId, alias="_id")

    # Time-related fields
    scheduled_at: datetime = Field(..., description="Schedule date of the peak (date portion used)")
    time_block: int = Field(..., description="Time block identifier of peak (1-96)")

    # Peak value
    peak_demand: float = Field(..., description="Peak computed actual demand for the day")
    average_demand: float = Field(..., description="Average computed actual demand for the day")
    min_demand: Optional[float] = Field(None, description="Minimum demand for the day")
    total_energy_mu: Optional[float] = Field(None, description="Total energy in MU for the day (Sum(demand)/4000)")

    # Processing timestamps
    created_at: datetime = Field(default_factory=datetime.now, description="Creation timestamp")
