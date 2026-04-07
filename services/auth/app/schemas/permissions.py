from pydantic import BaseModel
from typing import List, Dict, Optional

class PermissionSchema(BaseModel):
    module: str
    action: str

class RoleSchema(BaseModel):
    name: str
    permissions: List[str]  # ex: ["dashboard:view", "user_management:delete"]

class PageEndpointSchema(BaseModel):
    """Schema for page endpoint information"""
    method: str
    path: str
    description: Optional[str] = None

class PageSectionSchema(BaseModel):
    """Schema for page section information"""
    name: str
    endpoints: List[str]
    description: Optional[str] = None

class PageAccessSchema(BaseModel):
    """Schema for page access information"""
    page_name: str
    sections: List[str]
    endpoints: Dict[str, List[str]]
    total_sections: int
    total_endpoints: int

class UserPageAccessSchema(BaseModel):
    """Schema for user's accessible pages"""
    accessible_pages: List[str]
    page_details: Dict[str, Dict]
    total_pages: int

class PagePermissionSchema(BaseModel):
    """Schema for page-based permission"""
    page_name: str
    permission: str  # e.g., "user_management:access"
    description: Optional[str] = None
