from __future__ import annotations
from typing import Optional
from datetime import datetime

from bson import ObjectId
from pydantic import BaseModel, Field, field_validator, field_serializer, ConfigDict

# Single source of truth for collection name — change here once
COLL_PRICE_GROUP = "price_group"


# --- Pydantic v2-compatible ObjectId type ---
class PyObjectId(ObjectId):
    """Pydantic v2 adapter for bson.ObjectId."""
    @classmethod
    def __get_pydantic_core_schema__(cls, source, handler):
        # v2 uses core_schema; accept ObjectId or str and convert to ObjectId
        from pydantic_core import core_schema
        return core_schema.no_info_after_validator_function(
            cls._validate,
            core_schema.union_schema([
                core_schema.is_instance_schema(ObjectId),
                core_schema.str_schema()
            ]),
        )

    @classmethod
    def _validate(cls, v):
        if isinstance(v, ObjectId):
            return v
        try:
            return ObjectId(str(v))
        except Exception:
            raise ValueError("Invalid ObjectId")


class PriceGroupEntity(BaseModel):
    # Pydantic v2 config
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[PyObjectId] = Field(alias="_id", default=None)
    price_group: str = Field(min_length=1, max_length=50)
    price_from: float = 0.0
    price_to: float = 0.0
    inserted_date: datetime = Field(default_factory=datetime.utcnow)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # --- validators/serializers must be defined INSIDE the class in v2 ---

    @field_validator("price_to")
    @classmethod
    def check_range(cls, v, info):
        pf = info.data.get("price_from", 0)
        if v < pf:
            raise ValueError("price_to must be >= price_from")
        return v

    @field_serializer("id")
    def dump_id(self, v):
        return str(v) if v else None


class PriceGroupCreate(BaseModel):
    price_group: str
    price_from: float
    price_to: float


class PriceGroupUpdate(BaseModel):
    price_group: Optional[str] = None
    price_from: Optional[float] = None
    price_to: Optional[float] = None
