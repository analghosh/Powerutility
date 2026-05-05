"""Core libraries for EPMS services (lightweight init)."""

# Lazy imports for optional heavy modules
try:
    from .auth import JWTTokenManager, AuthMiddleware, AuthDependency, TokenResponse, UserInfo, AuthError
    from .jwt_validator import JWTValidator, TokenPayload, UserInfo as JWTUserInfo
    from .dependencies import (
        get_current_user,
        get_current_user_with_details,
        get_current_user_with_roles,
        get_current_user_with_permissions,
        get_current_user_with_any_permission,
        get_optional_user,
        require_admin,
        require_user,
        require_operator,
        require_read_permission,
        require_write_permission,
        require_delete_permission,
        require_any_data_permission,
    )
    from .middleware import AuthMiddleware as CoreAuthMiddleware

    __all__ = [
        "JWTTokenManager",
        "JWTValidator",
        "TokenPayload",
        "UserInfo",
        "JWTUserInfo",
        "TokenResponse",
        "AuthError",
        "get_current_user",
        "get_current_user_with_details",
        "get_current_user_with_roles",
        "get_current_user_with_permissions",
        "get_current_user_with_any_permission",
        "get_optional_user",
        "require_admin",
        "require_user",
        "require_operator",
        "require_read_permission",
        "require_write_permission",
        "require_delete_permission",
        "require_any_data_permission",
        "AuthMiddleware",
        "CoreAuthMiddleware",
        "AuthDependency",
    ]
except Exception as e:
    import logging
    logging.getLogger(__name__).warning(f"Optional core dependencies failed to import: {e}")
    __all__ = []
