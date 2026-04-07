"""
Unit tests for authz principal and permission computation
Tests libs/core/authz/principal.py functionality
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch
from libs.core.authz.principal import UserPrincipal, compute_effective_permissions


class TestUserPrincipal:
    """Test UserPrincipal class"""
    
    def test_user_principal_creation(self):
        """Test UserPrincipal object creation"""
        permissions = {"users:read", "dashboard:read"}
        user = UserPrincipal(
            user_id="test123",
            tenant_id="default",
            is_super_admin=False,
            authz_version=1,
            effective_permissions=permissions
        )
        
        assert user.user_id == "test123"
        assert user.tenant_id == "default"
        assert user.is_super_admin is False
        assert user.authz_version == 1
        assert user.effective_permissions == permissions
    
    def test_user_principal_super_admin(self):
        """Test UserPrincipal with super admin"""
        user = UserPrincipal(
            user_id="admin123",
            tenant_id="default",
            is_super_admin=True,
            authz_version=1,
            effective_permissions={"*"}
        )
        
        assert user.is_super_admin is True
        assert "*" in user.effective_permissions


@pytest.mark.asyncio
class TestComputeEffectivePermissions:
    """Test compute_effective_permissions function"""
    
    @pytest.fixture
    def mock_db(self):
        """Create mock database"""
        db = Mock()
        db.roles = Mock()
        db.permissions_catalog = Mock()
        return db
    
    async def test_user_with_direct_permissions_only(self, mock_db):
        """Test user with only direct permissions, no roles"""
        user_doc = {
            "_id": "user123",
            "roles": [],
            "permissions": ["users:read", "dashboard:read"]
        }
        
        # Mock empty roles query
        mock_db.roles.find.return_value.to_list = AsyncMock(return_value=[])
        
        result = await compute_effective_permissions(user_doc, "default", mock_db)
        
        assert result == {"users:read", "dashboard:read"}
        mock_db.roles.find.assert_called_once_with({"name": {"$in": []}})
    
    async def test_user_with_roles_only(self, mock_db):
        """Test user with roles but no direct permissions"""
        user_doc = {
            "_id": "user123",
            "roles": ["viewer", "editor"],
            "permissions": []
        }
        
        # Mock roles query
        role_docs = [
            {"name": "viewer", "permissions": ["users:read", "dashboard:read"]},
            {"name": "editor", "permissions": ["users:write", "posts:write"]}
        ]
        mock_db.roles.find.return_value.to_list = AsyncMock(return_value=role_docs)
        
        result = await compute_effective_permissions(user_doc, "default", mock_db)
        
        expected = {"users:read", "dashboard:read", "users:write", "posts:write"}
        assert result == expected
        mock_db.roles.find.assert_called_once_with({"name": {"$in": ["viewer", "editor"]}})
    
    async def test_user_with_roles_and_direct_permissions(self, mock_db):
        """Test user with both roles and direct permissions"""
        user_doc = {
            "_id": "user123",
            "roles": ["viewer"],
            "permissions": ["special:access"]
        }
        
        role_docs = [
            {"name": "viewer", "permissions": ["users:read"]}
        ]
        mock_db.roles.find.return_value.to_list = AsyncMock(return_value=role_docs)
        
        result = await compute_effective_permissions(user_doc, "default", mock_db)
        
        assert result == {"users:read", "special:access"}
    
    async def test_user_with_wildcard_permission_with_catalog(self, mock_db):
        """Test user with wildcard permission and permissions catalog exists"""
        user_doc = {
            "_id": "admin123",
            "roles": [],
            "permissions": ["*"]
        }
        
        mock_db.roles.find.return_value.to_list = AsyncMock(return_value=[])
        
        # Mock permissions catalog
        catalog_doc = {
            "permissions": ["users:read", "users:write", "admin:*", "system:read"]
        }
        mock_db.permissions_catalog.find_one = AsyncMock(return_value=catalog_doc)
        
        result = await compute_effective_permissions(user_doc, "default", mock_db)
        
        expected = {"users:read", "users:write", "admin:*", "system:read"}
        assert result == expected
        mock_db.permissions_catalog.find_one.assert_called_once()
    
    async def test_user_with_wildcard_permission_no_catalog(self, mock_db):
        """Test user with wildcard permission but no permissions catalog"""
        user_doc = {
            "_id": "admin123", 
            "roles": [],
            "permissions": ["*"]
        }
        
        mock_db.roles.find.return_value.to_list = AsyncMock(return_value=[])
        mock_db.permissions_catalog.find_one = AsyncMock(return_value=None)
        
        result = await compute_effective_permissions(user_doc, "default", mock_db)
        
        # Should fallback to default comprehensive permission set
        # Note: fallback doesn't include "*" itself, but expands to actual permissions
        assert "admin:*" in result
        assert "users:read" in result
        assert "users:write" in result
        assert len(result) > 5  # Should have many default permissions
    
    async def test_user_with_wildcard_in_role(self, mock_db):
        """Test user with role that has wildcard permission"""
        user_doc = {
            "_id": "admin123",
            "roles": ["admin"],
            "permissions": []
        }
        
        role_docs = [
            {"name": "admin", "permissions": ["*"]}
        ]
        mock_db.roles.find.return_value.to_list = AsyncMock(return_value=role_docs)
        mock_db.permissions_catalog.find_one = AsyncMock(return_value=None)
        
        result = await compute_effective_permissions(user_doc, "default", mock_db)
        
        # Should expand wildcard from role  
        # Note: fallback doesn't include "*" itself, but expands to actual permissions
        assert "admin:*" in result
        assert len(result) > 5
    
    async def test_empty_user_doc(self, mock_db):
        """Test user with no roles or permissions"""
        user_doc = {}
        
        mock_db.roles.find.return_value.to_list = AsyncMock(return_value=[])
        
        result = await compute_effective_permissions(user_doc, "default", mock_db)
        
        assert result == set()
    
    async def test_nonexistent_roles(self, mock_db):
        """Test user with roles that don't exist in database"""
        user_doc = {
            "_id": "user123",
            "roles": ["nonexistent_role"],
            "permissions": ["direct:permission"]
        }
        
        # Mock empty roles query (roles don't exist)
        mock_db.roles.find.return_value.to_list = AsyncMock(return_value=[])
        
        result = await compute_effective_permissions(user_doc, "default", mock_db)
        
        # Should only have direct permissions
        assert result == {"direct:permission"}
        mock_db.roles.find.assert_called_once_with({"name": {"$in": ["nonexistent_role"]}})
    
    async def test_roles_with_empty_permissions(self, mock_db):
        """Test roles that have no permissions defined"""
        user_doc = {
            "_id": "user123",
            "roles": ["empty_role"],
            "permissions": []
        }
        
        role_docs = [
            {"name": "empty_role"}  # No permissions field
        ]
        mock_db.roles.find.return_value.to_list = AsyncMock(return_value=role_docs)
        
        result = await compute_effective_permissions(user_doc, "default", mock_db)
        
        assert result == set()
    
    async def test_roles_with_null_permissions(self, mock_db):
        """Test roles with null permissions"""
        user_doc = {
            "_id": "user123", 
            "roles": ["null_role"],
            "permissions": []
        }
        
        role_docs = [
            {"name": "null_role", "permissions": None}
        ]
        mock_db.roles.find.return_value.to_list = AsyncMock(return_value=role_docs)
        
        result = await compute_effective_permissions(user_doc, "default", mock_db)
        
        assert result == set()
    
    async def test_complex_permission_union(self, mock_db):
        """Test complex scenario with multiple roles and overlapping permissions"""
        user_doc = {
            "_id": "user123",
            "roles": ["role1", "role2", "role3"],
            "permissions": ["direct1", "direct2", "users:read"]  # users:read overlaps with role1
        }
        
        role_docs = [
            {"name": "role1", "permissions": ["users:read", "users:write"]},
            {"name": "role2", "permissions": ["dashboard:read"]}, 
            {"name": "role3", "permissions": ["admin:read", "direct1"]}  # direct1 overlaps
        ]
        mock_db.roles.find.return_value.to_list = AsyncMock(return_value=role_docs)
        
        result = await compute_effective_permissions(user_doc, "default", mock_db)
        
        expected = {
            "users:read", "users:write",  # from role1
            "dashboard:read",              # from role2
            "admin:read",                  # from role3
            "direct1", "direct2"           # direct permissions (duplicates removed)
        }
        assert result == expected
    
    async def test_database_error_handling(self, mock_db):
        """Test error handling when database operations fail"""
        user_doc = {
            "_id": "user123",
            "roles": ["role1"],
            "permissions": []
        }
        
        # Mock database error
        mock_db.roles.find.return_value.to_list = AsyncMock(side_effect=Exception("Database error"))
        
        # Should propagate the exception
        with pytest.raises(Exception, match="Database error"):
            await compute_effective_permissions(user_doc, "default", mock_db)


@pytest.mark.asyncio 
class TestPerformanceScenarios:
    """Test performance-related scenarios"""
    
    async def test_large_number_of_roles(self, mock_db=None):
        """Test user with many roles"""
        if mock_db is None:
            mock_db = Mock()
            mock_db.roles = Mock()
        
        # User with 100 roles
        many_roles = [f"role_{i}" for i in range(100)]
        user_doc = {
            "_id": "user123",
            "roles": many_roles,
            "permissions": []
        }
        
        # Each role has 5 permissions
        role_docs = [
            {"name": f"role_{i}", "permissions": [f"perm_{i}_{j}" for j in range(5)]}
            for i in range(100)
        ]
        mock_db.roles.find.return_value.to_list = AsyncMock(return_value=role_docs)
        
        result = await compute_effective_permissions(user_doc, "default", mock_db)
        
        # Should have 500 unique permissions (100 roles * 5 permissions each)
        assert len(result) == 500
        mock_db.roles.find.assert_called_once_with({"name": {"$in": many_roles}})
    
    async def test_large_permissions_catalog(self, mock_db=None):
        """Test wildcard expansion with large permissions catalog"""
        if mock_db is None:
            mock_db = Mock()
            mock_db.roles = Mock()
            mock_db.permissions_catalog = Mock()
        
        user_doc = {
            "_id": "admin123",
            "roles": [],
            "permissions": ["*"]
        }
        
        mock_db.roles.find.return_value.to_list = AsyncMock(return_value=[])
        
        # Large permissions catalog
        large_catalog = {
            "permissions": [f"resource_{i}:action_{j}" for i in range(50) for j in range(10)]
        }
        mock_db.permissions_catalog.find_one = AsyncMock(return_value=large_catalog)
        
        result = await compute_effective_permissions(user_doc, "default", mock_db)
        
        # Should have 500 permissions (50 resources * 10 actions each)
        assert len(result) == 500
        assert "resource_0:action_0" in result
        assert "resource_49:action_9" in result
