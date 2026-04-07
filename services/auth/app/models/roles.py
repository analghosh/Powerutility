from pydantic import BaseModel, Field
from typing import List, Dict
from bson import ObjectId
from datetime import datetime

class RoleInDB(BaseModel):
    id: str = Field(default_factory=str, alias="_id")
    name: str
    description: str = ""
    permissions: List[str] = []  # List of permission strings like "users:read", "users:write", etc.
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class RoleCreate(BaseModel):
    name: str
    description: str = ""
    permissions: List[str] = []

class RoleUpdate(BaseModel):
    name: str = None
    description: str = None
    permissions: List[str] = None
