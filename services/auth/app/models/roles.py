from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from bson import ObjectId
from datetime import datetime

class RoleInDB(BaseModel):
    id: str = Field(default_factory=str, alias="_id")
    name: str
    description: str = ""
    permissions: List[str] = []  # List of permission strings like "users:read", "users:write", etc.
    # Caps this role at `read` on every page it holds. Roles created before this field
    # existed have no value for it, so the default must stay False -- an absent flag must
    # never turn an operational role read-only.
    is_read_only: bool = False
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class RoleCreate(BaseModel):
    name: str
    description: str = ""
    permissions: List[str] = []
    is_read_only: bool = False

class RoleUpdate(BaseModel):
    name: str = None
    description: str = None
    permissions: List[str] = None
    # None means "not supplied": an update that only renames must leave the flag alone.
    is_read_only: Optional[bool] = None
