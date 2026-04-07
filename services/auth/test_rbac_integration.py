#!/usr/bin/env python3
"""
Test script to verify centralized RBAC integration in auth service
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

async def test_rbac_integration():
    """Test centralized RBAC integration"""
    print("🧪 Testing centralized RBAC integration...")
    
    try:
        # Test 1: Import centralized RBAC components
        print("1. Testing imports...")
        from libs.core.authz import global_authz_guard, get_current_user, UserPrincipal
        from libs.core.authz.principal import compute_effective_permissions
        from libs.core.authz.config import required_permissions_for
        from libs.core.authz.permissions_catalog import ensure_permissions_catalog
        print("   ✅ All centralized RBAC imports successful")
        
        # Test 2: Test permission computation logic
        print("2. Testing permission computation...")
        
        # Mock user document (Auth Service format)
        mock_user_doc = {
            "_id": "test_user_123",
            "roles": ["admin", "user"],
            "permissions": ["dashboard:read"],
            "is_super_admin": False
        }
        
        # Mock database with roles
        class MockDB:
            class MockRoles:
                def find(self, query):
                    class MockCursor:
                        async def to_list(self, limit):
                            return [
                                {"name": "admin", "permissions": ["users:read", "users:write", "admin:*"]},
                                {"name": "user", "permissions": ["dashboard:read"]}
                            ]
                    return MockCursor()
            
            class MockPermissionsCatalog:
                async def find_one(self, query, projection=None):
                    return {"permissions": ["users:read", "users:write", "admin:*", "dashboard:read"]}
            
            def __init__(self):
                self.roles = self.MockRoles()
                self.permissions_catalog = self.MockPermissionsCatalog()
        
        mock_db = MockDB()
        
        # Test permission computation
        effective_perms = await compute_effective_permissions(mock_user_doc, "default", mock_db)
        expected_perms = {"users:read", "users:write", "admin:*", "dashboard:read"}
        
        if expected_perms.issubset(effective_perms):
            print("   ✅ Permission computation working correctly")
            print(f"   📋 Computed permissions: {sorted(effective_perms)}")
        else:
            print("   ❌ Permission computation failed")
            print(f"   Expected: {expected_perms}")
            print(f"   Got: {effective_perms}")
            return False
        
        # Test 3: Test route permission mapping
        print("3. Testing route permission mapping...")
        
        class MockDBOverrides:
            class MockEndpointOverrides:
                async def find_one(self, query, projection=None):
                    return None  # No overrides
            
            def __init__(self):
                self.endpoint_overrides = self.MockEndpointOverrides()
        
        mock_db_overrides = MockDBOverrides()
        
        # Test various route mappings
        test_routes = [
            ("GET", "/api/v1/users", ["users:read"]),
            ("POST", "/api/v1/users", ["users:create"]),
            ("PUT", "/api/v1/users/123", ["users:update"]),
            ("DELETE", "/api/v1/users/123", ["users:delete"]),
            ("GET", "/admin/settings", ["admin:*"]),
            ("GET", "/health", []),  # Public route
        ]
        
        for method, path, expected in test_routes:
            required = await required_permissions_for(mock_db_overrides, method, path)
            if required == expected:
                print(f"   ✅ {method} {path} → {required}")
            else:
                print(f"   ❌ {method} {path} → Expected: {expected}, Got: {required}")
                return False
        
        # Test 4: Test UserPrincipal creation
        print("4. Testing UserPrincipal creation...")
        
        user_principal = UserPrincipal(
            user_id="test_user_123",
            tenant_id="default",
            is_super_admin=False,
            authz_version=1,
            effective_permissions=effective_perms
        )
        
        if (user_principal.user_id == "test_user_123" and 
            user_principal.tenant_id == "default" and
            not user_principal.is_super_admin and
            "admin:*" in user_principal.effective_permissions):
            print("   ✅ UserPrincipal creation successful")
        else:
            print("   ❌ UserPrincipal creation failed")
            return False
        
        print("\n🎉 All RBAC integration tests passed!")
        print("\n📋 Integration Summary:")
        print("   ✅ Centralized RBAC imports working")
        print("   ✅ Permission computation from roles working")
        print("   ✅ Route-to-permission mapping working")
        print("   ✅ UserPrincipal creation working")
        print("   ✅ Auth service ready for centralized RBAC")
        
        return True
        
    except Exception as e:
        print(f"❌ RBAC integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = asyncio.run(test_rbac_integration())
    sys.exit(0 if success else 1)