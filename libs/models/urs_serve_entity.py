"""
URS Serve Entity Model

One row per plant per time block of un-requisitioned surplus, as published by
the RLDC (Grid India) feed.

Kept apart from ``supply_serve`` on purpose. URS is already counted inside each
plant's SG total there, so writing it into the same collection under a third
``dc_sg`` value would change what every existing query returns: several callers
read ``supply_serve`` for a day with no ``dc_sg`` filter at all
(``SupplyServeRepository.get_supply_by_date_range`` takes ``dc_sg=None``), and
those would silently start double-counting. A separate collection makes the new
data additive by construction.

The plant's name, technology and cost are snapshotted onto each row rather than
joined at read time. The rate is the one that was visible for that plant when
the block was scheduled; ``plant_master`` holds only today's figure, so a join
would quietly restate history every time a price is revised.
"""

from datetime import datetime
from typing import Any, Optional

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field

#: The ``dc_sg`` marker the parser stamps on a URS record, and the value the
#: supply processor routes on. Not a DC/SG type - it is what tells the two apart.
URS_DC_SG = "URS"


class UrsServeEntity(BaseModel):
    """Un-requisitioned surplus for one plant in one 15-minute block."""

    model_config = ConfigDict(
        validate_by_name=True,
        arbitrary_types_allowed=True,
        use_enum_values=True,
        json_encoders={ObjectId: str, datetime: lambda v: v.isoformat()},
        json_schema_extra={
            "example": {
                "scheduled_at": "2026-09-20T00:00:00",
                "time_block": 45,
                "plant_id": "VALLURNTECL",
                "plant_name": "NTPC Tamil Nadu Energy Company Ltd Vallur",
                "technology": "Thermal",
                "volume": 30.0,
                "fc_price": 1.42,
                "vc_price": 3.277,
                "total_fc_vc_price": 4697.0,
            }
        },
    )

    id: Optional[Any] = Field(default_factory=ObjectId, alias="_id")

    # Identity: one row per plant per block per day.
    scheduled_at: datetime = Field(..., description="Schedule date")
    time_block: int = Field(..., description="Time block, 1-96")
    plant_id: str = Field(..., description="Plant identifier (SellerAcronym)")

    # Snapshot of the plant as it stood when this block was ingested.
    plant_name: Optional[str] = Field(None, description="Plant name at ingest time")
    technology: Optional[str] = Field(None, description="Thermal / Hydro / Nuclear / …")

    # The surplus itself.
    volume: Optional[float] = Field(None, description="URS volume in MW")
    sg_beforelosses: Optional[float] = Field(None, description="URS before transmission losses")

    # The rate that was visible for this plant at this time.
    #
    # `total_fc_vc_price` is stored rather than derived so that a row always
    # carries the number the weighted average was computed from, even when one
    # of the two halves is missing. It is in Rs/MWh: plant_master keeps Rs/kWh,
    # and mplive scaled by 1000 at the same point.
    fc_price: Optional[float] = Field(None, description="Fixed cost, Rs/MWh")
    vc_price: Optional[float] = Field(None, description="Variable cost, Rs/MWh")
    total_fc_vc_price: Optional[float] = Field(None, description="FC + VC, Rs/MWh")
    #: False when the plant has no fixed cost on record, so a reader can tell a
    #: genuine zero from a rate that is really VC-only. 28 of 35 plants are in
    #: that state today.
    price_complete: bool = Field(default=False, description="Both FC and VC were known")

    # Provenance.
    source: Optional[str] = Field(None, description="Ingestion source identifier")
    source_revision_number: Optional[int] = Field(None, description="RLDC revision number")
    schedule_published_time: Optional[datetime] = Field(
        None, description="RLDC schedule publish timestamp from source"
    )

    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
