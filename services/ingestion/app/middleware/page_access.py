"""
Page Access Middleware for Ingestion Service
"""

from fastapi import Request, HTTPException, Depends
from typing import Dict, Any
from ..services.auth_client import auth_client

async def get_current_user(request: Request) -> Dict[str, Any]:
    """Get current user from request headers"""
    try:
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            raise HTTPException(status_code=401, detail="Authorization header required")
        
        # Extract token from Bearer format
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        else:
            token = auth_header
        
        user_info = await auth_client.validate_token_and_get_user(token)
        if not user_info:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        
        return user_info
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Authentication failed") from exc

def require_page_access(page_name: str):
    """
    Dependency to require access to a specific page
    
    Args:
        page_name: Name of the page to check access for
        
    Returns:
        Dependency function that checks page access via auth service
    """
    async def checker(current_user: Dict[str, Any] = Depends(get_current_user)):
        try:
            user_roles = current_user.get("roles", [])
            token = current_user.get("token", "")
            
            # Check page access via auth service
            has_access = await auth_client.check_page_access(user_roles, page_name, token)
            
            if not has_access:
                raise HTTPException(
                    status_code=403,
                    detail=f"Access denied to page: {page_name}"
                )
            
            return current_user
        
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error checking page access: {str(e)}"
            ) from e
    
    return checker

async def get_user_pages(user_roles: list, token: str) -> list:
    """Get list of pages accessible to user"""
    try:
        return await auth_client.get_user_accessible_pages(user_roles, token)
    except (ValueError, TypeError, KeyError, ConnectionError) as exc:
        # Log the error for debugging but return empty list for graceful degradation
        return []
