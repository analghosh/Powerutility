"""
V2 FastAPI dependencies
"""

from typing import Dict, Any
from fastapi import Depends, HTTPException, Request


def get_authenticated_user(request: Request) -> Dict[str, Any]:
    """
    Dependency to get current authenticated user from request state
    
    Returns:
        Current user context
        
    Raises:
        HTTPException: If user is not authenticated
    """
    user = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def get_request_correlation_id(request: Request) -> str:
    """
    Dependency to get current request correlation ID from request state
    
    Returns:
        Current correlation ID
    """
    correlation_id = getattr(request.state, "correlation_id", None)
    return correlation_id or "unknown"


def get_user_context(request: Request) -> Dict[str, Any]:
    """
    Dependency to get user context with correlation ID from request state.
    Returns anonymous context when no auth is present (safe for public routes).
    
    Returns:
        Dictionary containing user info and correlation ID
    """
    user = getattr(request.state, "current_user", None)
    correlation_id = get_request_correlation_id(request)
    
    if user:
        return {
            "user": user,
            "correlation_id": correlation_id,
            "user_id": user.get("id") or user.get("user_id"),
            "username": user.get("username"),
            "roles": user.get("roles", []),
            "permissions": user.get("permissions", [])
        }
    
    # Anonymous context for public routes
    return {
        "user": None,
        "correlation_id": correlation_id,
        "user_id": "anonymous",
        "username": "anonymous",
        "roles": [],
        "permissions": []
    }