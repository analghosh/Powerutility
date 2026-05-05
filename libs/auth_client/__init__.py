"""
Auth Client Library
Provides authentication and authorization utilities for service-to-service communication
"""

from .auth_client import (
    AuthClient, 
    AuthValidationResponse, 
    get_auth_client, 
    validate_token_simple, 
    check_user_permission, 
    check_user_role
)

__all__ = [
    "AuthClient", 
    "AuthValidationResponse", 
    "get_auth_client", 
    "validate_token_simple", 
    "check_user_permission", 
    "check_user_role"
]
