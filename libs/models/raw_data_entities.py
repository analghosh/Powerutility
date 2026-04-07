"""
Raw Data Entity Models for Ingestion Service
Defines the data structures for raw CSV data storage
"""

from datetime import datetime
from typing import Optional, Any, Dict, List
from pydantic import BaseModel, Field, ConfigDict
from bson import ObjectId
from libs.enum.raw_data_status import RawDataStatus


class DemandRawDataEntity(BaseModel):
    """
    Demand Raw Data Entity - Stores raw CSV data for demand processing
    
    This entity represents the raw demand data uploaded via CSV files
    before any processing or transformation.
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
                "status": "pending",
                "raw_data": [
                    {
                        "time_block": 1,
                        "schedule_date": "2025-01-15T10:00:00Z",
                        "type": "IDF",
                        "zone": "North Zone",
                        "Actual": 150.5,
                        "Drawal": 150.0,
                        "MAPE": 2.5,
                        "MPE": 1.2,
                        "Raw_Frequency": 50.0,
                        "Schedule": 148.0,
                        "comp_act_demand": 150.5,
                        "created_at": "2025-01-15T10:00:00Z",
                        "dsm_rate": 0.05,
                        "forecast": 148.0,
                        "forecasted_frequency": 148.0,
                        "inserted_at": "2025-01-15T10:00:00Z",
                        "net_dsm_amount": 7.5,
                        "remarks": "Test data upload",
                        "rostering": 0.0,
                        "source_actual": "CSV Upload",
                        "source_forecast": "CSV Upload",
                        "source_rostering": "CSV Upload",
                        "udm": 150.5,
                        "updated_at": "2025-01-15T10:00:00Z",
                        "updated_flag": "UPDATED",
                        "comp_udm": 0.0
                    }
                ],
                "created_at": "2025-01-15T10:00:00",
                "updated_at": "2025-01-15T10:00:00"
            }
        }
    )
    
    # MongoDB ID
    id: Optional[Any] = Field(default_factory=ObjectId, alias="_id")
    
    # Status from raw_data_status enum
    status: RawDataStatus = Field(default=RawDataStatus.PENDING, description="Processing status of the raw data")
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.now, description="Creation timestamp")
    updated_at: datetime = Field(default_factory=datetime.now, description="Last update timestamp")
    
    # Raw data storage - stores the complete raw data as list of dictionaries
    raw_data: List[Dict[str, Any]] = Field(default_factory=list, description="Complete raw data as list of dictionaries")
    
    # Time-series metadata field
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Time-series metadata for MongoDB")
    
    # Optional metadata
    file_name: Optional[str] = Field(None, description="Original CSV file name")


class SupplyRawDataEntity(BaseModel):
    """
    Supply Raw Data Entity - Stores raw CSV data for supply processing
    
    This entity represents the raw supply data uploaded via CSV files
    before any processing or transformation.
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
                "status": "pending",
                "raw_data": [
                    {
                        "scheduled_at": "2025-01-15T10:00:00Z",
                        "time_block": 1,
                        "plant_name": "Thermal A",
                        "dc_sg": "DC",
                        "volume": 500.0,
                        "fc_price": 2.5,
                        "px_price": 2.6,
                        "vc_price": 2.4,
                        "category": "Thermal",
                        "technology": "Coal",
                        "price_group": "A",
                        "c_s": "Central"
                    }
                ],
                "created_at": "2025-01-15T10:00:00",
                "updated_at": "2025-01-15T10:00:00"
            }
        }
    )
    
    # MongoDB ID
    id: Optional[Any] = Field(default_factory=ObjectId, alias="_id")
    
    # Status from raw_data_status enum
    status: RawDataStatus = Field(default=RawDataStatus.PENDING, description="Processing status of the raw data")
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.now, description="Creation timestamp")
    updated_at: datetime = Field(default_factory=datetime.now, description="Last update timestamp")
    
    # Raw data storage - stores the complete raw data as list of dictionaries
    raw_data: List[Dict[str, Any]] = Field(default_factory=list, description="Complete raw data as list of dictionaries")
    
    # Time-series metadata field
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Time-series metadata for MongoDB")
    
    # Optional metadata
    file_name: Optional[str] = Field(None, description="Original CSV file name")

