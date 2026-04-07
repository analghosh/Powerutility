"""
Unit tests for authz guard and authorization flow
Tests libs/core/authz/guard.py functionality
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch
from fastapi import HTTPException, Request
from libs.core.authz.guard import get_current_user, global_authz_guard, _load_user_doc, _bearer
from libs.core.authz.principal import UserPrincipal
from libs.core.authz.context import RequestContext


class TestBearerTokenExtraction:
    """Test bearer token extraction from requests"""
    
    def test_bearer_valid_token(self):
        """Test extracting valid bearer token"""
        request = Mock()
        request.headers = {"Authorization": "Bearer abc123xyz"}
        
        token = _bearer(request)
        assert token == "abc123xyz"
    
    def test_bearer_missing_header(self):
        """Test missing Authorization header"""
        request = Mock()
        request.headers = {}
        
        with pytest.raises(HTTPException) as exc_info:
            _bearer(request)
        
        assert exc_info.value.status_code == 401
        assert "Missing bearer token" in str(exc_info.value.detail)
    
    def test_bearer_invalid_format(self):
        """Test invalid authorization header format"""
        request = Mock()
        request.headers = {"Authorization": "Basic abc123"}
        
        with pytest.raises(HTTPException) as exc_info:
            _bearer(request)
        
        assert exc_info.value.status_code == 401
        assert "Missing bearer token" in str(exc_info.value.detail)
    
    def test_bearer_empty_token(self):
        """Test bearer with empty token"""
        request = Mock()
        request.headers = {"Authorization": "Bearer "}
        
        token = _bearer(request)
        assert token == ""
    
    def test_bearer_case_sensitive(self):
        """Test that Bearer is case sensitive"""
        request = Mock()
        request.headers = {"Authorization": "bearer abc123"}
        
        with pytest.raises(HTTPException):
            _bearer(request)


@pytest.mark.asyncio
class TestLoadUserDoc:
    """Test user document loading from database"""
    
    async def test_load_user_valid_id(self):
        """Test loading user with valid ID"""
        mock_db = Mock()
        mock_db.users = Mock()
        mock_db.users.find_one = AsyncMock(return_value={"_id": "user123", "username": "testuser"})
        
        result = await _load_user_doc(mock_db, "user123")
        
        assert result == {"_id": "user123", "username": "testuser"}
        mock_db.users.find_one.assert_called_once_with({"_id": "user123"})
    
    async def test_load_user_not_found(self):
        """Test loading non-existent user"""
        mock_db = Mock()
        mock_db.users = Mock()
        mock_db.users.find_one = AsyncMock(return_value=None)
        
        result = await _load_user_doc(mock_db, "nonexistent")
        
        assert result is None
        mock_db.users.find_one.assert_called_once_with({"_id": "nonexistent"})
    
    async def test_load_user_empty_id(self):
        """Test loading user with empty ID"""
        mock_db = Mock()
        
        result = await _load_user_doc(mock_db, "")
        
        assert result is None
        # Should not call database
        assert not hasattr(mock_db, 'users') or not mock_db.users.find_one.called
    
    async def test_load_user_none_id(self):
        """Test loading user with None ID"""
        mock_db = Mock()
        
        result = await _load_user_doc(mock_db, None)
        
        assert result is None


@pytest.mark.asyncio
class TestGetCurrentUser:
    """Test get_current_user function"""
    
    @pytest.fixture
    def mock_jwt_validator(self):
        """Mock JWT validator"""
        with patch('libs.core.authz.guard.jwt_validator') as mock:
            yield mock
    
    @pytest.fixture
    def mock_compute_permissions(self):
        """Mock compute_effective_permissions"""
        with patch('libs.core.authz.guard.compute_effective_permissions') as mock:
            yield mock
    
    @pytest.fixture 
    def mock_request(self):
        """Create mock request"""
        request = Mock(spec=Request)
        request.headers = {"Authorization": "Bearer valid_token"}
        return request
    
    @pytest.fixture
    def mock_context(self):
        """Create mock request context"""
        return RequestContext(correlation_id="test123")
    
    @pytest.fixture
    def mock_db(self):
        """Create mock database"""
        db = Mock()
        db.users = Mock()
        return db
    
    async def test_get_current_user_success(
        self, mock_request, mock_context, mock_db, 
        mock_jwt_validator, mock_compute_permissions
    ):
        """Test successful user authentication and authorization"""
        # Mock JWT validation
        token_payload = Mock()
        token_payload.user_id = "user123"
        token_payload.roles = ["user"]
        token_payload.permissions = ["token:permission"]
        mock_jwt_validator.validate_token_with_details.return_value = (token_payload, None)
        
        # Mock user document
        user_doc = {
            "_id": "user123",
            "username": "testuser",
            "roles": ["user"],
            "permissions": ["direct:permission"],
            "authz_version": 1
        }
        mock_db.users.find_one = AsyncMock(return_value=user_doc)
        
        # Mock permission computation
        mock_compute_permissions.return_value = {"computed:permission"}
        
        result = await get_current_user(mock_request, mock_context, mock_db)
        
        assert isinstance(result, UserPrincipal)
        assert result.user_id == "user123"
        assert result.tenant_id == "default"
        assert result.authz_version == 1
        # Should be union of computed permissions + token permissions
        assert "computed:permission" in result.effective_permissions
        assert "token:permission" in result.effective_permissions
    
    async def test_get_current_user_invalid_token(
        self, mock_request, mock_context, mock_db, mock_jwt_validator
    ):
        """Test invalid JWT token"""
        mock_jwt_validator.validate_token_with_details.return_value = (None, "Invalid token")
        
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(mock_request, mock_context, mock_db)
        
        assert exc_info.value.status_code == 401
        assert "Invalid token" in str(exc_info.value.detail)
    
    async def test_get_current_user_user_not_found(
        self, mock_request, mock_context, mock_db, mock_jwt_validator
    ):
        """Test user not found in database"""
        token_payload = Mock()
        token_payload.user_id = "nonexistent"
        token_payload.roles = []
        token_payload.permissions = []
        mock_jwt_validator.validate_token_with_details.return_value = (token_payload, None)
        
        mock_db.users.find_one = AsyncMock(return_value=None)
        
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(mock_request, mock_context, mock_db)
        
        assert exc_info.value.status_code == 401
        assert "User not found" in str(exc_info.value.detail)
    
    async def test_get_current_user_super_admin_detection(
        self, mock_request, mock_context, mock_db,
        mock_jwt_validator, mock_compute_permissions
    ):
        """Test super admin detection from various sources"""
        token_payload = Mock()
        token_payload.user_id = "admin123"
        token_payload.roles = ["admin"]
        token_payload.permissions = []
        mock_jwt_validator.validate_token_with_details.return_value = (token_payload, None)
        
        user_doc = {
            "_id": "admin123",
            "is_super_admin": True,
            "authz_version": 1
        }
        mock_db.users.find_one = AsyncMock(return_value=user_doc)
        mock_compute_permissions.return_value = set()
        
        result = await get_current_user(mock_request, mock_context, mock_db)
        
        assert result.is_super_admin is True
    
    async def test_get_current_user_super_admin_by_role(
        self, mock_request, mock_context, mock_db,
        mock_jwt_validator, mock_compute_permissions
    ):
        """Test super admin detection by admin role in token"""
        token_payload = Mock()
        token_payload.user_id = "admin123"
        token_payload.roles = ["admin"]
        token_payload.permissions = []
        mock_jwt_validator.validate_token_with_details.return_value = (token_payload, None)
        
        user_doc = {
            "_id": "admin123",
            "authz_version": 1
        }
        mock_db.users.find_one = AsyncMock(return_value=user_doc)
        mock_compute_permissions.return_value = set()
        
        result = await get_current_user(mock_request, mock_context, mock_db)
        
        assert result.is_super_admin is True
    
    async def test_get_current_user_super_admin_by_wildcard(
        self, mock_request, mock_context, mock_db,
        mock_jwt_validator, mock_compute_permissions
    ):
        """Test super admin detection by wildcard permission"""
        token_payload = Mock()
        token_payload.user_id = "admin123"
        token_payload.roles = []
        token_payload.permissions = []
        mock_jwt_validator.validate_token_with_details.return_value = (token_payload, None)
        
        user_doc = {
            "_id": "admin123",
            "authz_version": 1
        }
        mock_db.users.find_one = AsyncMock(return_value=user_doc)
        mock_compute_permissions.return_value = {"*"}  # Wildcard permission
        
        result = await get_current_user(mock_request, mock_context, mock_db)
        
        assert result.is_super_admin is True
    
    async def test_get_current_user_permission_union(
        self, mock_request, mock_context, mock_db,
        mock_jwt_validator, mock_compute_permissions
    ):
        """Test union of database and token permissions"""
        token_payload = Mock()
        token_payload.user_id = "user123"
        token_payload.roles = []
        token_payload.permissions = ["token:perm1", "token:perm2"]
        mock_jwt_validator.validate_token_with_details.return_value = (token_payload, None)
        
        user_doc = {
            "_id": "user123",
            "authz_version": 1
        }
        mock_db.users.find_one = AsyncMock(return_value=user_doc)
        mock_compute_permissions.return_value = {"db:perm1", "db:perm2", "token:perm1"}  # One overlap
        
        result = await get_current_user(mock_request, mock_context, mock_db)
        
        expected_permissions = {"db:perm1", "db:perm2", "token:perm1", "token:perm2"}
        assert result.effective_permissions == expected_permissions


@pytest.mark.asyncio
class TestGlobalAuthzGuard:
    """Test global_authz_guard function"""
    
    @pytest.fixture
    def mock_required_permissions_for(self):
        """Mock required_permissions_for function"""
        with patch('libs.core.authz.guard.required_permissions_for') as mock:
            yield mock
    
    @pytest.fixture
    def mock_request(self):
        """Create mock FastAPI request"""
        request = Mock(spec=Request)
        request.method = "GET"
        request.url = Mock()
        request.url.path = "/api/v1/users"
        
        # Mock route in scope
        route = Mock()
        route.path_format = "/api/v1/users"
        request.scope = {"route": route}
        
        return request
    
    @pytest.fixture
    def regular_user(self):
        """Create regular user for testing"""
        return UserPrincipal(
            user_id="user123",
            tenant_id="default",
            is_super_admin=False,
            authz_version=1,
            effective_permissions={"users:read", "dashboard:read"}
        )
    
    @pytest.fixture
    def super_admin_user(self):
        """Create super admin user for testing"""
        return UserPrincipal(
            user_id="admin123",
            tenant_id="default",
            is_super_admin=True,
            authz_version=1,
            effective_permissions={"*"}
        )
    
    @pytest.fixture
    def mock_db(self):
        """Create mock database for testing"""
        return Mock()
    
    async def test_global_authz_guard_super_admin_bypass(
        self, mock_request, super_admin_user, mock_db=None, mock_required_permissions_for=None
    ):
        """Test super admin bypasses all permission checks"""
        if mock_db is None:
            mock_db = Mock()
        
        # Should not call required_permissions_for for super admin
        result = await global_authz_guard(mock_request, super_admin_user, mock_db)
        
        assert result is None  # No exception raised
        # Should not check permissions for super admin
        if mock_required_permissions_for:
            mock_required_permissions_for.assert_not_called()
    
    async def test_global_authz_guard_no_route(
        self, regular_user, mock_db=None, mock_required_permissions_for=None
    ):
        """Test request with no route information"""
        if mock_db is None:
            mock_db = Mock()
        
        request = Mock(spec=Request)
        request.scope = {}  # No route
        
        result = await global_authz_guard(request, regular_user, mock_db)
        
        assert result is None  # Should pass (no-op for non-API paths)
    
    async def test_global_authz_guard_public_route(
        self, mock_request, regular_user, mock_db, mock_required_permissions_for
    ):
        """Test public route (no permissions required)"""
        mock_required_permissions_for.return_value = []  # Public route
        
        result = await global_authz_guard(mock_request, regular_user, mock_db)
        
        assert result is None  # Should pass
        mock_required_permissions_for.assert_called_once()
    
    async def test_global_authz_guard_valid_permissions(
        self, mock_request, regular_user, mock_db, mock_required_permissions_for
    ):
        """Test user with valid permissions for route"""
        mock_required_permissions_for.return_value = ["users:read"]
        
        result = await global_authz_guard(mock_request, regular_user, mock_db)
        
        assert result is None  # Should pass
    
    async def test_global_authz_guard_missing_permissions(
        self, mock_request, regular_user, mock_db, mock_required_permissions_for
    ):
        """Test user missing required permissions"""
        mock_required_permissions_for.return_value = ["admin:write"]
        
        with pytest.raises(HTTPException) as exc_info:
            await global_authz_guard(mock_request, regular_user, mock_db)
        
        assert exc_info.value.status_code == 403
        assert "Missing permissions: admin:write" in str(exc_info.value.detail)
    
    async def test_global_authz_guard_admin_wildcard_success(
        self, mock_request, mock_db, mock_required_permissions_for
    ):
        """Test admin wildcard permission success"""
        admin_user = UserPrincipal(
            user_id="admin123",
            tenant_id="default",
            is_super_admin=False,
            authz_version=1,
            effective_permissions={"admin:*"}
        )
        
        mock_required_permissions_for.return_value = ["admin:*"]
        
        result = await global_authz_guard(mock_request, admin_user, mock_db)
        
        assert result is None  # Should pass
    
    async def test_global_authz_guard_admin_wildcard_with_global_wildcard(
        self, mock_request, mock_db, mock_required_permissions_for
    ):
        """Test admin wildcard requirement with user having global wildcard"""
        wildcard_user = UserPrincipal(
            user_id="wildcard123",
            tenant_id="default",
            is_super_admin=False,
            authz_version=1,
            effective_permissions={"*"}
        )
        
        mock_required_permissions_for.return_value = ["admin:*"]
        
        result = await global_authz_guard(mock_request, wildcard_user, mock_db)
        
        assert result is None  # Should pass
    
    async def test_global_authz_guard_multiple_missing_permissions(
        self, mock_request, regular_user, mock_db, mock_required_permissions_for
    ):
        """Test user missing multiple required permissions"""
        mock_required_permissions_for.return_value = ["admin:write", "system:delete", "users:admin"]
        
        with pytest.raises(HTTPException) as exc_info:
            await global_authz_guard(mock_request, regular_user, mock_db)
        
        assert exc_info.value.status_code == 403
        detail = str(exc_info.value.detail)
        assert "admin:write" in detail
        assert "system:delete" in detail
        assert "users:admin" in detail
    
    async def test_global_authz_guard_partial_permissions(
        self, mock_request, regular_user, mock_db, mock_required_permissions_for
    ):
        """Test user having some but not all required permissions"""
        mock_required_permissions_for.return_value = ["users:read", "users:write"]  # User only has read
        
        with pytest.raises(HTTPException) as exc_info:
            await global_authz_guard(mock_request, regular_user, mock_db)
        
        assert exc_info.value.status_code == 403
        assert "users:write" in str(exc_info.value.detail)
        assert "users:read" not in str(exc_info.value.detail)  # Should not list permissions user has


@pytest.mark.asyncio
class TestIntegrationScenarios:
    """Test integration scenarios combining multiple components"""
    
    async def test_full_auth_flow_success(self):
        """Test complete authentication and authorization flow"""
        # Mock request with valid token
        request = Mock(spec=Request)
        request.headers = {"Authorization": "Bearer valid_token"}
        request.method = "GET"
        request.url = Mock()
        request.url.path = "/api/v1/users"
        
        route = Mock()
        route.path_format = "/api/v1/users"
        request.scope = {"route": route}
        
        # Mock context
        context = RequestContext(correlation_id="test123")
        
        # Mock database
        db = Mock()
        db.users = Mock()
        user_doc = {
            "_id": "user123",
            "username": "testuser",
            "roles": ["user"],
            "permissions": ["users:read"],
            "authz_version": 1
        }
        db.users.find_one = AsyncMock(return_value=user_doc)
        
        with patch('libs.core.authz.guard.jwt_validator') as mock_jwt, \
             patch('libs.core.authz.guard.compute_effective_permissions') as mock_compute, \
             patch('libs.core.authz.guard.required_permissions_for') as mock_required:
            
            # Mock JWT validation
            token_payload = Mock()
            token_payload.user_id = "user123"
            token_payload.roles = ["user"]
            token_payload.permissions = []
            mock_jwt.validate_token_with_details.return_value = (token_payload, None)
            
            # Mock permission computation
            mock_compute.return_value = {"users:read"}
            
            # Mock required permissions
            mock_required.return_value = ["users:read"]
            
            # Test get_current_user
            user = await get_current_user(request, context, db)
            assert isinstance(user, UserPrincipal)
            assert user.user_id == "user123"
            assert "users:read" in user.effective_permissions
            
            # Test global_authz_guard
            result = await global_authz_guard(request, user, db)
            assert result is None  # Should pass
    
    async def test_full_auth_flow_failure(self):
        """Test complete flow with authorization failure"""
        request = Mock(spec=Request)
        request.headers = {"Authorization": "Bearer valid_token"}
        request.method = "DELETE"
        request.url = Mock()
        request.url.path = "/api/v1/admin/users"
        
        route = Mock()
        route.path_format = "/api/v1/admin/users"
        request.scope = {"route": route}
        
        context = RequestContext(correlation_id="test123")
        
        db = Mock()
        db.users = Mock()
        user_doc = {
            "_id": "user123",
            "roles": ["user"],
            "permissions": ["users:read"],
            "authz_version": 1
        }
        db.users.find_one = AsyncMock(return_value=user_doc)
        
        with patch('libs.core.authz.guard.jwt_validator') as mock_jwt, \
             patch('libs.core.authz.guard.compute_effective_permissions') as mock_compute, \
             patch('libs.core.authz.guard.required_permissions_for') as mock_required:
            
            token_payload = Mock()
            token_payload.user_id = "user123"
            token_payload.roles = ["user"]
            token_payload.permissions = []
            mock_jwt.validate_token_with_details.return_value = (token_payload, None)
            
            mock_compute.return_value = {"users:read"}
            mock_required.return_value = ["admin:delete"]  # Requires admin permission
            
            # Get user successfully
            user = await get_current_user(request, context, db)
            assert user.user_id == "user123"
            
            # Authorization should fail
            with pytest.raises(HTTPException) as exc_info:
                await global_authz_guard(request, user, db)
            
            assert exc_info.value.status_code == 403
            assert "admin:delete" in str(exc_info.value.detail)
