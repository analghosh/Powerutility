"""
Page Access Middleware for Ingestion Service
"""

import secrets
from fastapi import Request, HTTPException, Depends
from typing import Any, Dict
from libs.authz.service_auth import INTERNAL_TOKEN_HEADER
from ..core.config import settings
from ..services.auth_client import auth_client

SUPER_ROLES = {"admin", "superadmin"}

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

async def require_internal_or_admin(request: Request) -> Dict[str, Any]:
    """
    Guard for the cron control routes.

    `/cron/status`, `/cron/trigger/{job_id}` and `/cron/trigger-all` were the only routes
    on this service with no authentication of any kind -- trigger-all starts every
    registered ingestion job. They are called by other EPMS services, so a trusted
    internal credential is accepted alongside an administrator login.
    """
    presented = request.headers.get(INTERNAL_TOKEN_HEADER)
    expected = getattr(settings, "INTERNAL_SERVICE_TOKEN", "") or ""
    if expected and presented and secrets.compare_digest(presented, expected):
        return {"user_id": "internal-service", "username": "internal-service",
                "roles": ["service"], "is_service_account": True}

    user = await get_current_user(request)
    roles = {r.lower() for r in (user.get("roles") or []) if isinstance(r, str)}
    if not roles & SUPER_ROLES:
        raise HTTPException(status_code=403, detail="Administrator role required")
    return user


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

            # The caller's real permissions, resolved by the auth service at login. These
            # were previously fetched and then thrown away: the check compared role names
            # against a hardcoded list instead.
            has_access = await auth_client.check_page_access(
                user_roles, page_name, token,
                user_permissions=current_user.get("permissions", []),
            )
            
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
