from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Any
from bson import ObjectId
from datetime import datetime

class MenuInDB(BaseModel):
    """Menu model for database storage with two-level nested structure"""
    id: str = Field(default_factory=str, alias="_id")
    name: str
    icon: Optional[str] = None
    parent_id: Optional[str] = None  # For nested menu items
    path: Optional[str] = None  # Route path
    order: int = 0  # Menu ordering
    is_active: bool = True
    is_deleted: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    @field_validator('order', mode='before')
    @classmethod
    def coerce_order(cls, v: Any) -> int:
        if isinstance(v, float):
            return int(v)
        return v

    @field_validator('id', mode='before')
    @classmethod
    def validate_id(cls, v: Any) -> str:
        """Convert ObjectId to string if needed"""
        if isinstance(v, ObjectId):
            return str(v)
        return str(v) if v is not None else ""
    
    @field_validator('parent_id', mode='before')
    @classmethod
    def validate_parent_id(cls, v: Any) -> Optional[str]:
        """Convert ObjectId to string if needed"""
        if isinstance(v, ObjectId):
            return str(v)
        return str(v) if v is not None else None

class MenuCreate(BaseModel):
    """Model for creating a new menu item"""
    name: str
    icon: Optional[str] = None
    parent_id: Optional[str] = None
    path: Optional[str] = None
    order: int = 0

class MenuUpdate(BaseModel):
    """Model for updating a menu item"""
    name: Optional[str] = None
    icon: Optional[str] = None
    parent_id: Optional[str] = None
    path: Optional[str] = None
    order: Optional[int] = None
    is_active: Optional[bool] = None

class MenuWithChildren(BaseModel):
    """Menu model with nested children for API responses"""
    id: str
    name: str
    icon: Optional[str] = None
    parent_id: Optional[str] = None
    path: Optional[str] = None
    order: int
    is_active: bool
    children: List['MenuWithChildren'] = []

    @field_validator('order', mode='before')
    @classmethod
    def coerce_order(cls, v: Any) -> int:
        if isinstance(v, float):
            return int(v)
        return v
