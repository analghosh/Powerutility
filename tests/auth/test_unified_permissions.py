"""
Unit tests for Unified Menu-Endpoint Permission System
"""

import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from datetime import datetime, timezone

# Mock database connections BEFORE importing any app modules
with patch('pymongo.MongoClient') as mock_mongo_client:
    mock_client = MagicMock()
    mock_db = MagicMock()
    mock_collection = MagicMock()
    mock_mongo_client.return_value = mock_client
    mock_client.__getitem__.return_value = mock_db
    mock_db.__getitem__.return_value = mock_collection
    
    # Mock Redis connections
    with patch('redis.Redis') as mock_redis:
        mock_redis_instance = Mock()
        mock_redis.from_url.return_value = mock_redis_instance
        mock_redis_instance.sismember.return_value = False
        mock_redis_instance.sadd.return_value = 1
        
        # Now we can safely import
        from services.auth.app.core.rbac import (
            require_menu_access, 
            has_menu_permission, 
            get_user_accessible_menus,
            get_user_menu_permissions,
            validate_menu_access
        )
        from services.auth.app.core.dto import AuthUserDTO


class TestUnifiedPermissions:
    """Test cases for unified menu-endpoint permission system"""
    
    @pytest.fixture
    def mock_user_dto(self):
        """Mock AuthUserDTO with menu permissions"""
        return AuthUserDTO(
            id="507f1f77bcf86cd799439011",
            username="testuser",
            email="test@example.com",
            is_active=True,
            roles=["user", "viewer"],
            permissions=["users:read", "users:write", "dashboard:read"],
            profile={},
            last_login=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
            mfa_enabled=False,
            mfa_setup_complete=False,
            accessible_menus=["689ad95713ffc2f07a369277", "689ad95713ffc2f07a36925b"]
        )
    
    @pytest.fixture
    def mock_admin_user_dto(self):
        """Mock AuthUserDTO with admin permissions"""
        return AuthUserDTO(
            id="507f1f77bcf86cd799439012",
            username="admin",
            email="admin@example.com",
            is_active=True,
            roles=["admin"],
            permissions=["*"],  # Super admin
            profile={},
            last_login=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
            mfa_enabled=False,
            mfa_setup_complete=False,
            accessible_menus=[]
        )
    
    def test_has_menu_permission_with_super_admin(self, mock_admin_user_dto):
        """Test that super admin has all menu permissions"""
        # Super admin should have access to any menu with any permission
        assert has_menu_permission(mock_admin_user_dto, "Users", "read") == True
        assert has_menu_permission(mock_admin_user_dto, "Users", "write") == True
        assert has_menu_permission(mock_admin_user_dto, "Users", "create") == True
        assert has_menu_permission(mock_admin_user_dto, "Users", "delete") == True
        assert has_menu_permission(mock_admin_user_dto, "Dashboard", "read") == True
        assert has_menu_permission(mock_admin_user_dto, "NonExistentMenu", "any") == True
    
    def test_has_menu_permission_with_user_permissions(self, mock_user_dto):
        """Test menu permission checking with user permissions"""
        # User has users:read and users:write permissions
        assert has_menu_permission(mock_user_dto, "Users", "read") == True
        assert has_menu_permission(mock_user_dto, "Users", "write") == True
        
        # User doesn't have these permissions
        assert has_menu_permission(mock_user_dto, "Users", "create") == False
        assert has_menu_permission(mock_user_dto, "Users", "delete") == False
        
        # User has dashboard:read permission
        assert has_menu_permission(mock_user_dto, "Dashboard", "read") == True
        
        # User doesn't have access to non-existent menu
        assert has_menu_permission(mock_user_dto, "NonExistentMenu", "read") == False
    
    @patch('services.auth.app.core.rbac.role_repository')
    @patch('services.auth.app.core.rbac.menu_repository')
    @patch('services.auth.app.core.rbac.menu_role_repository')
    def test_has_menu_permission_with_role_permissions(
        self, 
        mock_menu_role_repo, 
        mock_menu_repo, 
        mock_role_repo,
        mock_user_dto
    ):
        """Test menu permission checking through role assignments"""
        # Mock role without direct permissions (so it will check menu-role assignments)
        mock_role = Mock()
        mock_role.id = "role_id_123"
        mock_role.permissions = []  # No direct permissions, should check menu-role
        mock_role_repo.get_role_by_name.return_value = mock_role
        
        # Mock menu
        mock_menu = Mock()
        mock_menu.id = "menu_id_123"
        mock_menu_repo.get_menu_by_name.return_value = mock_menu
        
        # Mock menu-role assignment
        mock_menu_role = Mock()
        mock_menu_role.permissions = ["create", "delete"]
        mock_menu_role_repo.get_menu_role.return_value = mock_menu_role
        
        # Test permissions through role assignments
        # The user has roles ["user", "viewer"], so we need to mock both
        mock_role_repo.get_role_by_name.side_effect = lambda name: mock_role if name in ["user", "viewer"] else None
        
        assert has_menu_permission(mock_user_dto, "Users", "create") == True
        assert has_menu_permission(mock_user_dto, "Users", "delete") == True
        
        # Verify the mocks were called correctly
        mock_role_repo.get_role_by_name.assert_called()
        mock_menu_repo.get_menu_by_name.assert_called_with("Users")
        mock_menu_role_repo.get_menu_role.assert_called_with("menu_id_123", mock_role.id)
    
    @patch('services.auth.app.core.rbac.role_repository')
    @patch('services.auth.app.core.rbac.menu_role_repository')
    @patch('services.auth.app.core.rbac.menu_repository')
    def test_get_user_accessible_menus(self, mock_menu_repo, mock_menu_role_repo, mock_role_repo):
        """Test getting user accessible menus"""
        # Mock roles
        mock_user_role = Mock()
        mock_user_role.id = "role_user_id"
        mock_viewer_role = Mock()
        mock_viewer_role.id = "role_viewer_id"
        
        mock_role_repo.get_role_by_name.side_effect = lambda name: {
            "user": mock_user_role,
            "viewer": mock_viewer_role
        }.get(name)
        
        # Mock menu-role assignments
        mock_menu_role_repo.get_menus_for_role.side_effect = lambda role_id: {
            "role_user_id": ["menu_users_id", "menu_dashboard_id"],
            "role_viewer_id": ["menu_dashboard_id"]
        }.get(role_id, [])
        
        # Mock menus
        mock_users_menu = Mock()
        mock_users_menu.name = "Users"
        mock_dashboard_menu = Mock()
        mock_dashboard_menu.name = "Dashboard"
        
        mock_menu_repo.get_menu_by_id.side_effect = lambda menu_id: {
            "menu_users_id": mock_users_menu,
            "menu_dashboard_id": mock_dashboard_menu
        }.get(menu_id)
        
        # Test getting accessible menus
        user_roles = ["user", "viewer"]
        accessible_menus = get_user_accessible_menus(user_roles)
        
        # Should return unique menu names
        assert "Users" in accessible_menus
        assert "Dashboard" in accessible_menus
        assert len(accessible_menus) == 2
    
    @patch('services.auth.app.core.rbac.role_repository')
    @patch('services.auth.app.core.rbac.menu_role_repository')
    @patch('services.auth.app.core.rbac.menu_repository')
    def test_get_user_menu_permissions(self, mock_menu_repo, mock_menu_role_repo, mock_role_repo):
        """Test getting user menu permissions"""
        # Mock role with permissions
        mock_role = Mock()
        mock_role.permissions = ["users:read", "users:write", "dashboard:read"]
        mock_role_repo.get_role_by_name.return_value = mock_role
        
        # Mock menu
        mock_menu = Mock()
        mock_menu.id = "menu_users_id"
        mock_menu_repo.get_menu_by_name.return_value = mock_menu
        
        # Mock menu-role assignment
        mock_menu_role = Mock()
        mock_menu_role.permissions = ["read", "write"]
        mock_menu_role_repo.get_menu_role.return_value = mock_menu_role
        
        # Test getting menu permissions
        user_roles = ["user"]
        permissions = get_user_menu_permissions(user_roles, "Users")
        
        # Should return permissions for the Users menu
        assert "read" in permissions
        assert "write" in permissions
        assert len(permissions) == 2
    
    def test_validate_menu_access(self):
        """Test menu access validation"""
        # Test with valid access
        user_roles = ["user"]
        assert validate_menu_access(user_roles, "Users", "read") == False  # No role permissions set up
        
        # Test with admin role
        user_roles = ["admin"]
        assert validate_menu_access(user_roles, "Users", "read") == False  # No role permissions set up
    
    @patch('services.auth.app.core.rbac.role_repository')
    @patch('services.auth.app.core.rbac.menu_role_repository')
    @patch('services.auth.app.core.rbac.menu_repository')
    def test_require_menu_access_dependency(self, mock_menu_repo, mock_menu_role_repo, mock_role_repo):
        """Test the require_menu_access dependency"""
        # This would require more complex async testing setup
        # For now, we'll test the underlying has_menu_permission function
        mock_user_dto = AuthUserDTO(
            id="test_id",
            username="testuser",
            email="test@example.com",
            is_active=True,
            roles=["user"],
            permissions=["users:read"],
            profile={},
            last_login=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
            mfa_enabled=False,
            mfa_setup_complete=False,
            accessible_menus=[]
        )
        
        # Test that the permission check works correctly
        assert has_menu_permission(mock_user_dto, "Users", "read") == True
        assert has_menu_permission(mock_user_dto, "Users", "write") == False
    
    def test_permission_string_format(self, mock_user_dto):
        """Test that permission strings are formatted correctly"""
        # The system should convert "Users" menu to "users:permission" format
        assert has_menu_permission(mock_user_dto, "Users", "read") == True
        assert has_menu_permission(mock_user_dto, "Dashboard", "read") == True
        
        # Test with spaces in menu names
        user_with_spaces = AuthUserDTO(
            id="test_id",
            username="testuser",
            email="test@example.com",
            is_active=True,
            roles=["user"],
            permissions=["user_management:read"],  # Space replaced with underscore
            profile={},
            last_login=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
            mfa_enabled=False,
            mfa_setup_complete=False,
            accessible_menus=[]
        )
        
        assert has_menu_permission(user_with_spaces, "User Management", "read") == True
    
    def test_error_handling_in_permission_check(self):
        """Test error handling in permission checking"""
        # Test with None user
        assert has_menu_permission(None, "Users", "read") == False
        
        # Test with user missing attributes
        incomplete_user = Mock()
        incomplete_user.roles = None
        incomplete_user.permissions = None
        assert has_menu_permission(incomplete_user, "Users", "read") == False
        
        # Test with empty user
        empty_user = Mock()
        empty_user.roles = []
        empty_user.permissions = []
        assert has_menu_permission(empty_user, "Users", "read") == False
        
        # Test with user that has None roles
        user_with_none_roles = Mock()
        user_with_none_roles.roles = None
        user_with_none_roles.permissions = []
        assert has_menu_permission(user_with_none_roles, "Users", "read") == False
