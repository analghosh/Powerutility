"""
Auth Client Service for Ingestion Service
"""

import logging
import jwt
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from fastapi import HTTPException

from ..core.config import settings

logger = logging.getLogger(__name__)

class AuthClient:
    """Client for validating JWT tokens locally"""
    
    def __init__(self):
        # JWT configuration from settings
        self.secret_key = settings.JWT_SECRET_KEY
        self.algorithm = settings.JWT_ALGORITHM
    
    async def validate_token_and_get_user(self, token: str) -> Optional[Dict[str, Any]]:
        """Validate JWT token locally and get current user info"""
        try:
            # Decode and validate JWT token
            payload = jwt.decode(
                token, 
                self.secret_key, 
                algorithms=[self.algorithm],
                options={"verify_exp": True}
            )
            
            # Check if token is expired
            if "exp" in payload:
                exp_timestamp = payload["exp"]
                current_timestamp = datetime.now(timezone.utc).timestamp()
                if current_timestamp > exp_timestamp:
                    logger.warning("Token has expired")
                    return None
            
            # Extract user information from token payload
            user_data = {
                "user_id": payload.get("sub", payload.get("user_id", "unknown")),
                "username": payload.get("username", payload.get("preferred_username", "unknown")),
                "roles": payload.get("roles", ["user"]),
                "permissions": payload.get("permissions", []),
                "token": token
            }
            
            logger.info("Token validated successfully for user: %s", user_data["username"])
            return user_data
            
        except jwt.ExpiredSignatureError:
            logger.warning("Token has expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.error("Invalid token: %s", str(e))
            return None
        except Exception as e:
            logger.error("Error validating token: %s", str(e))
            return None
    
    async def check_page_access(self, user_roles: List[str], page_name: str, token: str) -> bool:
        """Check if user has access to a specific page based on roles"""
        try:
            # Define page access permissions based on roles
            page_permissions = {
                "Data Ingestion": ["admin"],
                "Provider Management": ["admin"],
                "System Administration": ["admin"],
                "User Management": ["admin"],
                "Reports": ["admin"]
            }
            
            # Get required roles for the page
            required_roles = page_permissions.get(page_name, ["admin"])
            
            # Check if user has any of the required roles
            has_access = any(role in user_roles for role in required_roles)
            
            if has_access:
                logger.info("Access granted for user with roles %s to page %s", user_roles, page_name)
            else:
                logger.warning("Access denied for user with roles %s to page %s", user_roles, page_name)
            
            return has_access
                    
        except Exception as e:
            logger.error("Error checking page access: %s", str(e))
            return False
    
    async def get_user_accessible_pages(self, user_roles: List[str], token: str) -> List[str]:
        """Get list of pages user can access based on roles"""
        try:
            # Define page access permissions based on roles
            page_permissions = {
                "Data Ingestion": ["admin", "data_analyst", "operator"],
                "Provider Management": ["admin", "config_manager"],
                "System Administration": ["admin"],
                "User Management": ["admin"],
                "Reports": ["admin", "data_analyst", "manager"]
            }
            
            # Find all pages the user can access
            accessible_pages = []
            for page_name, required_roles in page_permissions.items():
                if any(role in user_roles for role in required_roles):
                    accessible_pages.append(page_name)
            
            logger.info("User with roles %s can access pages: %s", user_roles, accessible_pages)
            return accessible_pages
                    
        except Exception as e:
            logger.error("Error getting accessible pages: %s", str(e))
            return []

# Global auth client instance
auth_client = AuthClient()
