"""
API Key Management Controller
Request handling layer for API key management operations
"""
from typing import Dict, Any, List, Optional, Union
from pydantic import BaseModel, Field
from datetime import datetime

from ..services.api_key_management_service import api_key_management_service


def format_datetime_to_iso(date_value: Optional[Union[datetime, str]]) -> Optional[str]:
    """
    Safely format datetime to ISO string format
    
    Args:
        date_value: datetime object, ISO string, or None
        
    Returns:
        ISO formatted string with Z suffix, or None/empty string
    """
    if date_value is None:
        return None
    
    # If already a string, check if it needs formatting
    if isinstance(date_value, str):
        # If it already ends with Z or has timezone, return as is
        if date_value.endswith('Z') or '+' in date_value or date_value.count('-') > 2:
            return date_value
        # Try to parse and reformat
        try:
            # Try parsing as ISO format
            dt = datetime.fromisoformat(date_value.replace('Z', '+00:00'))
            return dt.isoformat().replace('+00:00', 'Z')
        except (ValueError, AttributeError):
            # If parsing fails, return as is
            return date_value
    
    # If it's a datetime object, format it
    if isinstance(date_value, datetime):
        return date_value.isoformat() + "Z"
    
    # Fallback: convert to string
    return str(date_value)


# ===========================
# Request/Response Models
# ===========================

class CreateAPIKeyRequest(BaseModel):
    """Request model for creating a new API key"""
    name: str = Field(..., description="Client/Company name", min_length=1)
    description: Optional[str] = Field(None, description="Additional description or notes")
    api_key_prefix: Optional[str] = Field(None, description="Optional prefix for API key (default: 'client')")


class APIKeyResponse(BaseModel):
    """Response model for API key creation (includes secret - shown only once)"""
    api_key: str = Field(..., description="The API key identifier")
    secret: str = Field(..., description="The secret key (shown only once - save immediately)")
    name: str = Field(..., description="Client/Company name")
    description: str = Field(..., description="Description")
    is_active: bool = Field(..., description="Active status")
    created_at: str = Field(..., description="Creation timestamp (ISO format)")
    created_by: str = Field(..., description="Username of admin who created the key")


class APIKeyInfo(BaseModel):
    """Response model for API key information (without secret)"""
    api_key: str = Field(..., description="The API key identifier")
    name: str = Field(..., description="Client/Company name")
    description: str = Field(..., description="Description")
    is_active: bool = Field(..., description="Active status")
    created_at: str = Field(..., description="Creation timestamp (ISO format)")
    updated_at: Optional[str] = Field(None, description="Last update timestamp")
    last_used_at: Optional[str] = Field(None, description="Last usage timestamp")
    created_by: Optional[str] = Field(None, description="Username of admin who created the key")


class APIKeyStatusResponse(BaseModel):
    """Response model for status update operations"""
    api_key: str = Field(..., description="The API key identifier")
    is_active: bool = Field(..., description="Current active status")
    message: str = Field(..., description="Status message")


# ===========================
# Controller Class
# ===========================

class APIKeyManagementController:
    async def create_api_key(self, request: CreateAPIKeyRequest, username: str) -> APIKeyResponse:
        """
        Create a new API key
        
        Args:
            request: Create API key request data
            username: Username of admin creating the key
            
        Returns:
            APIKeyResponse with api_key and secret
        """
        result = api_key_management_service.create_api_key(
            name=request.name,
            description=request.description,
            api_key_prefix=request.api_key_prefix,
            created_by=username
        )
        
        return APIKeyResponse(
            api_key=result["api_key"],
            secret=result["secret"],
            name=result["name"],
            description=result["description"],
            is_active=result["is_active"],
            created_at=format_datetime_to_iso(result["created_at"]) or "",
            created_by=result["created_by"]
        )

    async def list_api_keys(self, is_active: Optional[bool] = None) -> List[APIKeyInfo]:
        """
        List all API keys (without secrets)
        
        Args:
            is_active: Optional filter by active status
            
        Returns:
            List of APIKeyInfo
        """
        keys = api_key_management_service.list_api_keys(is_active=is_active)
        
        result = []
        for key in keys:
            created_at = key.get("created_at")
            updated_at = key.get("updated_at")
            last_used_at = key.get("last_used_at")
            
            result.append(APIKeyInfo(
                api_key=key.get("api_key"),
                name=key.get("name", ""),
                description=key.get("description", ""),
                is_active=key.get("is_active", False),
                created_at=format_datetime_to_iso(created_at) or "",
                updated_at=format_datetime_to_iso(updated_at),
                last_used_at=format_datetime_to_iso(last_used_at),
                created_by=key.get("created_by")
            ))
        
        return result

    async def get_api_key(self, api_key: str) -> APIKeyInfo:
        """
        Get API key information (without secret)
        
        Args:
            api_key: The API key identifier
            
        Returns:
            APIKeyInfo
        """
        key_data = api_key_management_service.get_api_key(api_key)
        
        created_at = key_data.get("created_at")
        updated_at = key_data.get("updated_at")
        last_used_at = key_data.get("last_used_at")
        
        return APIKeyInfo(
            api_key=key_data.get("api_key"),
            name=key_data.get("name", ""),
            description=key_data.get("description", ""),
            is_active=key_data.get("is_active", False),
            created_at=format_datetime_to_iso(created_at) or "",
            updated_at=format_datetime_to_iso(updated_at),
            last_used_at=format_datetime_to_iso(last_used_at),
            created_by=key_data.get("created_by")
        )

    async def activate_api_key(self, api_key: str) -> APIKeyStatusResponse:
        """
        Activate an API key
        
        Args:
            api_key: The API key identifier
            
        Returns:
            APIKeyStatusResponse
        """
        result = api_key_management_service.activate_api_key(api_key)
        return APIKeyStatusResponse(**result)

    async def deactivate_api_key(self, api_key: str) -> APIKeyStatusResponse:
        """
        Deactivate an API key
        
        Args:
            api_key: The API key identifier
            
        Returns:
            APIKeyStatusResponse
        """
        result = api_key_management_service.deactivate_api_key(api_key)
        return APIKeyStatusResponse(**result)


# Singleton instance
api_key_management_controller = APIKeyManagementController()