from pydantic import BaseModel, Field
from typing import List, Optional
from bson import ObjectId
from datetime import datetime

class PageInDB(BaseModel):
    """Page model for database storage - separate from role relationships"""
    id: str = Field(default_factory=str, alias="_id")
    name: str
    module_id: str
    guard_name: str = "web"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class PageCreate(BaseModel):
    """Model for creating a new page"""
    name: str
    module_id: str
    guard_name: str = "web"

class PageUpdate(BaseModel):
    """Model for updating a page"""
    name: Optional[str] = None
    module_id: Optional[str] = None
