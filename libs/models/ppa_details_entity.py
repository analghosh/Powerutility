"""
PPA Details Entity Model
Stores Power Purchase Agreement details for plants from plant_master.
plant_id and plant_name are sourced from plant_master and kept in sync.
"""

from datetime import datetime, date
from typing import Optional, Any, List
from pydantic import BaseModel, Field, ConfigDict
from bson import ObjectId


class PpaFileInfo(BaseModel):
    """Metadata for an uploaded PPA agreement file."""

    file_id: str = Field(..., description="Unique file identifier")
    original_filename: str = Field(..., description="Original uploaded filename")
    stored_filename: str = Field(..., description="Filename on disk")
    content_type: Optional[str] = Field(default=None, description="MIME type")
    size_bytes: Optional[int] = Field(default=None, description="File size in bytes")
    uploaded_at: datetime = Field(default_factory=datetime.utcnow, description="Upload timestamp")


class PpaDetailsEntity(BaseModel):
    """PPA Details entity model."""

    id: Optional[Any] = Field(default_factory=ObjectId, alias="_id")

    # Sourced from plant_master (synced automatically)
    plant_id: str = Field(..., description="Plant ID from plant_master")
    plant_name: str = Field(..., description="Plant name from plant_master")

    # PPA-specific fields
    category: Optional[str] = Field(default=None, description="Category")
    ppa_signing_date: Optional[datetime] = Field(default=None, description="PPA signing date")
    agreement_term: Optional[float] = Field(default=None, description="Agreement term in years")
    expiry: Optional[datetime] = Field(default=None, description="Expiry date based on COD + agreement term")
    installed_capacity_mw: Optional[float] = Field(default=None, description="Installed capacity (MW)")
    no_of_units: Optional[int] = Field(default=None, description="Number of units")
    mppmcl_firm_allocation_mw: Optional[float] = Field(
        default=None, description="MPPMCL Firm Allocation (MW)"
    )
    unallocated_share_cs_mw: Optional[float] = Field(
        default=None, description="Unallocated share (CS) (MW)"
    )
    total_allocation_mw: Optional[float] = Field(default=None, description="Total Allocation (MW)")
    commercial_operation_date: Optional[datetime] = Field(
        default=None, description="Commercial operation date of power station (COD)"
    )
    remark: Optional[str] = Field(default=None, description="Remark")
    no_of_pages_in_agreement: Optional[int] = Field(
        default=None, description="Number of pages in agreement"
    )
    status: Optional[str] = Field(default="Active", description="Active / Inactive")
    postal_address: Optional[str] = Field(default=None, description="Postal address")
    expired_but_extended: Optional[bool] = Field(
        default=False, description="Expired but extended flag"
    )
    ppa_files: List[PpaFileInfo] = Field(default_factory=list, description="Uploaded PPA files")

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        populate_by_name=True,
        json_encoders={
            ObjectId: str,
            datetime: lambda v: v.isoformat(),
            date: lambda v: v.isoformat(),
        },
        json_schema_extra={
            "example": {
                "plant_id": "SPP",
                "plant_name": "Sample Power Plant",
                "category": "Thermal",
                "ppa_signing_date": "2020-01-15T00:00:00Z",
                "agreement_term": 25.0,
                "installed_capacity_mw": 500.0,
                "no_of_units": 2,
                "mppmcl_firm_allocation_mw": 400.0,
                "unallocated_share_cs_mw": 100.0,
                "total_allocation_mw": 500.0,
                "commercial_operation_date": "2020-06-01T00:00:00Z",
                "status": "Active",
                "expired_but_extended": False,
            }
        },
    )
