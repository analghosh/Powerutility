"""
Unit tests for centralized permission checking system
Tests libs/core/authz/permissions.py functionality
"""

import pytest
from unittest.mock import Mock, patch
from fastapi import HTTPException
from libs.core.authz.permissions import (
    PermissionChecker,
    require_permission,
    require_any_permission,
    require_all_permissions,
    require_admin,
    require_super_admin,
    check_user_permission,
    assert_user_permission,
    get_user_permissions,
    filter_by_permission,
    CommonPermissions
)
from libs.core.authz.principal import UserPrincipal


@pytest.fixture
def regular_user():
    """Create a regular user with basic permissions"""
    return UserPrincipal(
        user_id="user123",
        tenant_id="default",
        is_super_admin=False,
        authz_version=1,
        effective_permissions={"users:read", "dashboard:read"}
    )


@pytest.fixture
def admin_user():
    """Create an admin user with admin permissions"""
    return UserPrincipal(
        user_id="admin123",
        tenant_id="default",
        is_super_admin=False,
        authz_version=1,
        effective_permissions={"admin:*", "users:read", "users:write"}
    )


@pytest.fixture
def super_admin_user():
    """Create a super admin user"""
    return UserPrincipal(
        user_id="superadmin123",
        tenant_id="default",
        is_super_admin=True,
        authz_version=1,
        effective_permissions={"*"}
    )


@pytest.fixture
def empty_user():
    """Create a user with no permissions"""
    return UserPrincipal(
        user_id="empty123",
        tenant_id="default",
        is_super_admin=False,
        authz_version=1,
        effective_permissions=set()
    )


class TestPermissionChecker:
    """Test the PermissionChecker class logic"""
    
    def test_has_permission_regular_user(self, regular_user):
        """Test permission checking for regular users"""
        assert PermissionChecker.has_permission(regular_user, "users:read") is True
        assert PermissionChecker.has_permission(regular_user, "dashboard:read") is True
        assert PermissionChecker.has_permission(regular_user, "users:write") is False
        assert PermissionChecker.has_permission(regular_user, "admin:read") is False
    
    def test_has_permission_super_admin(self, super_admin_user):
        """Test super admin bypasses all permission checks"""
        assert PermissionChecker.has_permission(super_admin_user, "users:read") is True
        assert PermissionChecker.has_permission(super_admin_user, "users:write") is True
        assert PermissionChecker.has_permission(super_admin_user, "admin:delete") is True
        assert PermissionChecker.has_permission(super_admin_user, "nonexistent:permission") is True
    
    def test_has_permission_empty_user(self, empty_user):
        """Test user with no permissions"""
        assert PermissionChecker.has_permission(empty_user, "users:read") is False
        assert PermissionChecker.has_permission(empty_user, "dashboard:read") is False
    
    def test_has_any_permission_success(self, regular_user):
        """Test has_any_permission with user having one of the permissions"""
        permissions = ["users:write", "users:read", "admin:read"]
        assert PermissionChecker.has_any_permission(regular_user, permissions) is True
    
    def test_has_any_permission_failure(self, regular_user):
        """Test has_any_permission with user having none of the permissions"""
        permissions = ["users:write", "admin:read", "system:delete"]
        assert PermissionChecker.has_any_permission(regular_user, permissions) is False
    
    def test_has_any_permission_super_admin(self, super_admin_user):
        """Test super admin bypasses has_any_permission"""
        permissions = ["nonexistent:permission"]
        assert PermissionChecker.has_any_permission(super_admin_user, permissions) is True
    
    def test_has_all_permissions_success(self, regular_user):
        """Test has_all_permissions with user having all permissions"""
        permissions = ["users:read", "dashboard:read"]
        assert PermissionChecker.has_all_permissions(regular_user, permissions) is True
    
    def test_has_all_permissions_partial(self, regular_user):
        """Test has_all_permissions with user having only some permissions"""
        permissions = ["users:read", "users:write"]
        assert PermissionChecker.has_all_permissions(regular_user, permissions) is False
    
    def test_has_all_permissions_super_admin(self, super_admin_user):
        """Test super admin bypasses has_all_permissions"""
        permissions = ["nonexistent:permission", "another:permission"]
        assert PermissionChecker.has_all_permissions(super_admin_user, permissions) is True
    
    def test_has_role_admin(self, admin_user):
        """Test role checking for admin users"""
        assert PermissionChecker.has_role(admin_user, "admin") is True
    
    def test_has_role_super_admin(self, super_admin_user):
        """Test super admin bypasses role checking"""
        assert PermissionChecker.has_role(super_admin_user, "nonexistent_role") is True


class TestUtilityFunctions:
    """Test utility functions for permission checking"""
    
    def test_check_user_permission(self, regular_user):
        """Test check_user_permission utility"""
        assert check_user_permission(regular_user, "users:read") is True
        assert check_user_permission(regular_user, "users:write") is False
    
    def test_assert_user_permission_success(self, regular_user):
        """Test assert_user_permission with valid permission"""
        # Should not raise exception
        assert_user_permission(regular_user, "users:read")
    
    def test_assert_user_permission_failure(self, regular_user):
        """Test assert_user_permission with invalid permission"""
        with pytest.raises(HTTPException) as exc_info:
            assert_user_permission(regular_user, "users:write")
        
        assert exc_info.value.status_code == 403
        assert "Missing required permission: users:write" in str(exc_info.value.detail)
    
    def test_get_user_permissions(self, regular_user):
        """Test getting list of user permissions"""
        permissions = get_user_permissions(regular_user)
        assert isinstance(permissions, list)
        assert "users:read" in permissions
        assert "dashboard:read" in permissions
        assert len(permissions) == 2
    
    def test_filter_by_permission(self, regular_user):
        """Test filtering items by permission"""
        items = [
            {"id": 1, "type": "users"},
            {"id": 2, "type": "admin"},
            {"id": 3, "type": "dashboard"}
        ]
        
        # Filter using function to generate permission
        filtered = filter_by_permission(
            items,
            regular_user,
            lambda item: f"{item['type']}:read"
        )
        
        # Should include users and dashboard items, exclude admin
        assert len(filtered) == 2
        assert filtered[0]["type"] == "users"
        assert filtered[1]["type"] == "dashboard"
    
    def test_filter_by_permission_super_admin(self, super_admin_user):
        """Test filtering with super admin returns all items"""
        items = [{"id": 1}, {"id": 2}, {"id": 3}]
        filtered = filter_by_permission(items, super_admin_user, "any:permission")
        assert len(filtered) == 3
        assert filtered == items
    
    def test_filter_by_permission_string(self, regular_user):
        """Test filtering with static permission string"""
        items = [1, 2, 3, 4, 5]
        filtered = filter_by_permission(items, regular_user, "users:read")
        assert len(filtered) == 5  # User has users:read permission
        
        filtered = filter_by_permission(items, regular_user, "admin:read")
        assert len(filtered) == 0  # User doesn't have admin:read permission


@pytest.mark.asyncio
class TestFastAPIDependencies:
    """Test FastAPI dependency functions"""
    
    async def test_require_permission_success(self, regular_user):
        """Test require_permission dependency with valid permission"""
        dependency = require_permission("users:read")
        
        # Mock the dependency call
        result = await dependency(regular_user)
        assert result == regular_user
    
    async def test_require_permission_failure(self, regular_user):
        """Test require_permission dependency with invalid permission"""
        dependency = require_permission("admin:write")
        
        with pytest.raises(HTTPException) as exc_info:
            await dependency(regular_user)
        
        assert exc_info.value.status_code == 403
        assert "Missing required permission: admin:write" in str(exc_info.value.detail)
    
    async def test_require_any_permission_success(self, regular_user):
        """Test require_any_permission with valid permissions"""
        dependency = require_any_permission("users:write", "users:read", "admin:read")
        
        result = await dependency(regular_user)
        assert result == regular_user
    
    async def test_require_any_permission_failure(self, regular_user):
        """Test require_any_permission with no valid permissions"""
        dependency = require_any_permission("users:write", "admin:read")
        
        with pytest.raises(HTTPException) as exc_info:
            await dependency(regular_user)
        
        assert exc_info.value.status_code == 403
        assert "Need any of: users:write, admin:read" in str(exc_info.value.detail)
    
    async def test_require_all_permissions_success(self, regular_user):
        """Test require_all_permissions with valid permissions"""
        dependency = require_all_permissions("users:read", "dashboard:read")
        
        result = await dependency(regular_user)
        assert result == regular_user
    
    async def test_require_all_permissions_failure(self, regular_user):
        """Test require_all_permissions with missing permissions"""
        dependency = require_all_permissions("users:read", "users:write")
        
        with pytest.raises(HTTPException) as exc_info:
            await dependency(regular_user)
        
        assert exc_info.value.status_code == 403
        assert "Missing required permissions: users:write" in str(exc_info.value.detail)
    
    async def test_require_admin_success(self, admin_user):
        """Test require_admin with admin user"""
        dependency = require_admin()
        
        result = await dependency(admin_user)
        assert result == admin_user
    
    async def test_require_admin_failure(self, regular_user):
        """Test require_admin with regular user"""
        dependency = require_admin()
        
        with pytest.raises(HTTPException) as exc_info:
            await dependency(regular_user)
        
        assert exc_info.value.status_code == 403
    
    async def test_require_super_admin_success(self, super_admin_user):
        """Test require_super_admin with super admin user"""
        dependency = require_super_admin()
        
        result = await dependency(super_admin_user)
        assert result == super_admin_user
    
    async def test_require_super_admin_failure(self, admin_user):
        """Test require_super_admin with admin user (not super admin)"""
        dependency = require_super_admin()
        
        with pytest.raises(HTTPException) as exc_info:
            await dependency(admin_user)
        
        assert exc_info.value.status_code == 403
        assert "Super admin access required" in str(exc_info.value.detail)


class TestCommonPermissions:
    """Test CommonPermissions constants"""
    
    def test_permission_constants_exist(self):
        """Test that all expected permission constants exist"""
        assert hasattr(CommonPermissions, 'ADMIN_ALL')
        assert hasattr(CommonPermissions, 'USERS_READ')
        assert hasattr(CommonPermissions, 'USERS_WRITE')
        assert hasattr(CommonPermissions, 'ROLES_READ')
        assert hasattr(CommonPermissions, 'MENU_READ')
        assert hasattr(CommonPermissions, 'DASHBOARD_READ')
        assert hasattr(CommonPermissions, 'SYSTEM_READ')
    
    def test_permission_constant_values(self):
        """Test that permission constants have expected values"""
        assert CommonPermissions.ADMIN_ALL == "admin:*"
        assert CommonPermissions.USERS_READ == "users:read"
        assert CommonPermissions.USERS_WRITE == "users:write"
        assert CommonPermissions.USERS_CREATE == "users:create"
        assert CommonPermissions.USERS_DELETE == "users:delete"
        assert CommonPermissions.ROLES_READ == "roles:read"
        assert CommonPermissions.MENU_READ == "menu:read"
        assert CommonPermissions.DASHBOARD_READ == "dashboard:read"


class TestEdgeCases:
    """Test edge cases and error conditions"""
    
    def test_empty_permission_string(self, regular_user):
        """Test checking empty permission string"""
        assert PermissionChecker.has_permission(regular_user, "") is False
    
    def test_none_permission(self, regular_user):
        """Test checking None permission"""
        # Should return False, not raise exception
        assert PermissionChecker.has_permission(regular_user, None) is False
    
    def test_empty_permissions_list(self, regular_user):
        """Test checking empty permissions list"""
        assert PermissionChecker.has_any_permission(regular_user, []) is False
        assert PermissionChecker.has_all_permissions(regular_user, []) is True  # Vacuous truth
    
    def test_wildcard_permission(self, admin_user):
        """Test users with admin:* permission"""
        # User with admin:* should have admin permissions but not necessarily others
        assert PermissionChecker.has_permission(admin_user, "admin:read") is True
        assert PermissionChecker.has_permission(admin_user, "admin:write") is True
        # Note: admin:* doesn't automatically grant non-admin permissions in this implementation
    
    def test_case_sensitivity(self, regular_user):
        """Test that permissions are case sensitive"""
        assert PermissionChecker.has_permission(regular_user, "USERS:READ") is False
        assert PermissionChecker.has_permission(regular_user, "Users:Read") is False
        assert PermissionChecker.has_permission(regular_user, "users:read") is True
    
    def test_filter_by_permission_empty_list(self, regular_user):
        """Test filtering empty list"""
        filtered = filter_by_permission([], regular_user, "any:permission")
        assert filtered == []
    
    def test_filter_by_permission_callable_error(self, regular_user):
        """Test filtering with callable that raises exception"""
        items = [{"id": 1}]
        
        def failing_permission(item):
            raise ValueError("Test error")
        
        with pytest.raises(ValueError):
            filter_by_permission(items, regular_user, failing_permission)