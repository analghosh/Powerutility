"""
Auth Client Service for Service-to-Service Communication
"""

import httpx
import logging
from typing import Optional, Dict, Any, List
from fastapi import HTTPException
from ..core.config import settings

logger = logging.getLogger(__name__)

class AuthClient:
    """Client for communicating with the auth service"""
    
    def __init__(self):
        self.base_url = f"http://{settings.SERVICE_HOST}:{settings.SERVICE_PORT}/api/v1"
        self.timeout = 30.0
    
    async def validate_token_and_get_user(self, token: str) -> Optional[Dict[str, Any]]:
        """Validate token and get current user info"""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                headers = {"Authorization": f"Bearer {token}"}
                response = await client.get(
                    f"{self.base_url}/auth/validate-token",
                    headers=headers
                )
                
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 401:
                    return None
                else:
                    logger.error(f"Auth service error: {response.status_code} - {response.text}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error validating token: {str(e)}")
            return None
    
    async def get_user_info(self, user_id: str, token: str) -> Optional[Dict[str, Any]]:
        """Get user information by ID"""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                headers = {"Authorization": f"Bearer {token}"}
                response = await client.get(
                    f"{self.base_url}/auth/user/{user_id}",
                    headers=headers
                )
                
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 404:
                    return None
                else:
                    logger.error(f"Auth service error: {response.status_code} - {response.text}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error getting user info: {str(e)}")
            return None
    
    async def validate_user_permissions(self, user_id: str, required_permissions: List[str], token: str) -> Dict[str, Any]:
        """Validate if a user has specific permissions"""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                headers = {"Authorization": f"Bearer {token}"}
                data = {"user_id": user_id, "required_permissions": required_permissions}
                response = await client.post(
                    f"{self.base_url}/auth/validate-permissions",
                    headers=headers,
                    json=data
                )
                
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.error(f"Auth service error: {response.status_code} - {response.text}")
                    return {
                        "user_id": user_id,
                        "has_all_permissions": False,
                        "missing_permissions": required_permissions,
                        "error": "Auth service unavailable"
                    }
                    
        except Exception as e:
            logger.error(f"Error validating permissions: {str(e)}")
            return {
                "user_id": user_id,
                "has_all_permissions": False,
                "missing_permissions": required_permissions,
                "error": "Auth service unavailable"
            }
    
    async def get_available_roles(self, token: str) -> Optional[Dict[str, Any]]:
        """Get all available roles"""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                headers = {"Authorization": f"Bearer {token}"}
                response = await client.get(
                    f"{self.base_url}/auth/roles",
                    headers=headers
                )
                
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.error(f"Auth service error: {response.status_code} - {response.text}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error getting roles: {str(e)}")
            return None
    
    async def get_available_permissions(self, token: str) -> Optional[Dict[str, Any]]:
        """Get all available permissions"""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                headers = {"Authorization": f"Bearer {token}"}
                response = await client.get(
                    f"{self.base_url}/auth/permissions",
                    headers=headers
                )
                
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.error(f"Auth service error: {response.status_code} - {response.text}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error getting permissions: {str(e)}")
            return None

# Global auth client instance
auth_client = AuthClient()
