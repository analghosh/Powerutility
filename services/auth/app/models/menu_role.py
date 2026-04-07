from pydantic import BaseModel, Field
from typing import List, Optional
from bson import ObjectId
from datetime import datetime

class MenuRoleInDB(BaseModel):
    """Menu-Role relationship model for database storage"""
    id: str = Field(default_factory=str, alias="_id")
    menu_id: str
    role_id: str
    permissions: List[str] = ["read"]  # Permissions for this menu: read, write, create, delete
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class MenuRoleCreate(BaseModel):
    """Model for creating a new menu-role relationship"""
    menu_id: str
    role_id: str
    permissions: List[str] = ["read"]

class MenuRoleUpdate(BaseModel):
    """Model for updating a menu-role relationship"""
    permissions: Optional[List[str]] = None

class MenuPermissionResponse(BaseModel):
    """Response model for menu permissions"""
    menu_id: str
    menu_name: str
    menu_path: Optional[str] = None
    permissions: List[str]
    assigned_at: str

class RoleMenuResponse(BaseModel):
    """Response model for role menu assignments"""
    role_id: str
    role_name: str
    menus: List[MenuPermissionResponse]
