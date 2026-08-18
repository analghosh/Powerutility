from pydantic import BaseModel, Field
from typing import List, Optional
from bson import ObjectId
from datetime import datetime

class MenuRoleInDB(BaseModel):
    """Menu-Role relationship model for database storage"""
    id: str = Field(default_factory=str, alias="_id")
    menu_id: str
    role_id: str
    # A grant row means "this role has this page". The list is kept at ["read"] for
    # backward compatibility with older tooling; the verbs a role actually holds come
    # from roles.is_read_only via libs/authz/page_model.
    permissions: List[str] = ["read"]
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class MenuRoleCreate(BaseModel):
    """Model for creating a new menu-role relationship"""
    menu_id: str
    role_id: str
    # A grant row means "this role has this page". The list is kept at ["read"] for
    # backward compatibility with older tooling; the verbs a role actually holds come
    # from roles.is_read_only via libs/authz/page_model.
    permissions: List[str] = ["read"]

class MenuRoleUpdate(BaseModel):
    """Model for updating a menu-role relationship"""
    # A grant row means "this role has this page". The list is kept at ["read"] for
    # backward compatibility with older tooling; the verbs a role actually holds come
    # from roles.is_read_only via libs/authz/page_model.
    permissions: List[str] = ["read"]

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
