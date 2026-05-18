"""
Dc Sg Entity Models
Defines the data structures for dc sg collections
"""

from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field, ConfigDict


class DCSgEntity(BaseModel):
    """
    Dc Sg Entity
    
    This entity represents the processed supply data that is actively served
    to the frontend applications.
    """
    
    model_config = ConfigDict(
        validate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={
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
    rtm_tm: float = Field(default=0.0, description="RTM Technical Minimum value (55% of DC volume)")
    multiplier: float = Field(default=0.0, description="Multiplier value")
    ramping: str = Field(default="", description="Ramping information")
    ramp_up: Optional[float] = Field(None, description="Ramp up value")
    ramp_down: Optional[float] = Field(None, description="Ramp down value")
    dc_afterloss: Optional[float] = Field(None, description="DC after loss value")
    entire_shared_onbar_ent_amount: Optional[float] = Field(None, description="Entire Shared Onbar Entitlement Amount")
    entire_shared_offbar_ent_amount: Optional[float] = Field(None, description="Entire Shared Offbar Entitlement Amount")
    rtm_max: Optional[float] = Field(None, description="RTM Max value (SG of tomorrow at 10:10 AM, locked once set)")

    # Process flags
    process_type: Optional[str] = Field(None, description="Process type (today/tomorrow)")
    updated_flag: Optional[str] = Field(None, description="Updated flag (UPDATED/REPLACED/OLD)")
    
    # Max MW DC for SG records (locked value from 08:00-08:14 tomorrow data)
    max_mw_dc: Optional[float] = Field(None, description="Max MW DC value (copied from volume for SG on tomorrow's 08:00-08:14 data)")
    eightThirtyValue: Optional[float] = Field(None, description="8:30 value for SG records (copied from volume for SG on tomorrow's 08:00-08:14 data)")
    # Source tracking
    source: Optional[str] = Field(None, description="Data source identifier")
    revision_no: Optional[int] = Field(None, description="Revision number from source")
