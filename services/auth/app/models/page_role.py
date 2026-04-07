from pydantic import BaseModel, Field
from typing import List, Optional
from bson import ObjectId
from datetime import datetime

class PageRoleInDB(BaseModel):
    """Page-Role relationship model for database storage"""
    id: str = Field(default_factory=str, alias="_id")
    page_id: str
    role_id: str
    permissions: List[str] = ["read"]  # Default permission
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class PageRoleCreate(BaseModel):
    """Model for creating a new page-role relationship"""
    page_id: str
    role_id: str
    permissions: List[str] = ["read"]

class PageRoleUpdate(BaseModel):
    """Model for updating a page-role relationship"""
    permissions: Optional[List[str]] = None
    is_active: Optional[bool] = None
