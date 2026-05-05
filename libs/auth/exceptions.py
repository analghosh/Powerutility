"""
Authentication-related exceptions
"""

from fastapi import HTTPException
from typing import Optional


class AuthenticationError(HTTPException):
    """Base authentication error"""
    
    def __init__(self, detail: str = "Authentication failed", status_code: int = 401):
        super().__init__(status_code=status_code, detail=detail)


class InvalidTokenError(AuthenticationError):
    """Invalid JWT token error"""
    
    def __init__(self, detail: str = "Invalid token"):
        super().__init__(detail=detail, status_code=401)


class TokenExpiredError(AuthenticationError):
    """Expired JWT token error"""
    
    def __init__(self, detail: str = "Token has expired"):
        super().__init__(detail=detail, status_code=401)


class MissingTokenError(AuthenticationError):
    """Missing authorization header error"""
    
    def __init__(self, detail: str = "Authorization header required"):
        super().__init__(detail=detail, status_code=401)


class InvalidAuthHeaderError(AuthenticationError):
    """Invalid authorization header format error"""
    
    def __init__(self, detail: str = "Invalid authorization header format"):
        super().__init__(detail=detail, status_code=401)
