"""
Unit tests for AuthController
"""

import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from fastapi import HTTPException

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
        from services.auth.app.controllers.auth_controller import AuthController
        from services.auth.app.core.dto import UserDTO, AuthUserDTO
        from services.auth.app.schemas.auth import LoginRequest, RefreshTokenRequest, ChangePasswordRequest
from datetime import datetime, timedelta


class TestAuthController:
    """Test cases for AuthController"""
    
    @pytest.fixture
    def auth_controller(self):
        """Create AuthController instance for testing"""
        controller = AuthController()
        # Mock the auth service
        controller.auth_service = AsyncMock()
        return controller
    
    @pytest.fixture
    def mock_user_dto(self):
        """Mock UserDTO"""
        return UserDTO(
            id="507f1f77bcf86cd799439011",
            username="testuser",
            email="test@example.com",
            is_active=True,
            is_verified=True,
            roles=["user"],
            permissions=["read"],
            profile={
                "first_name": "Test",
                "last_name": "User",
                "phone": "1234567890",
                "address": {"city": "Test City"},
                "preferences": {"theme": "dark"}
            },
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_login=datetime.now(),
            login_attempts=0,
            locked_until=None,
            password_expires_at=datetime.now() + timedelta(days=90),
            mfa_enabled=False,
            mfa_setup_complete=False
        )
    
    @pytest.fixture
    def mock_auth_user_dto(self, mock_user_dto):
        """Mock AuthUserDTO"""
        return AuthUserDTO.from_user_dto(mock_user_dto)
    
    @pytest.fixture
    def mock_request(self):
        """Mock FastAPI request"""
        request = Mock()
        request.client.host = "192.168.1.1"
        request.headers = {"user-agent": "Mozilla/5.0"}
        request.method = "POST"
        request.url.path = "/api/v1/auth/login"
        return request
    
    def test_extract_request_info(self, auth_controller, mock_request):
        """Test request info extraction"""
        info = auth_controller._extract_request_info(mock_request)
        
        assert info["ip_address"] == "192.168.1.1"
        assert info["user_agent"] == "Mozilla/5.0"
        assert info["method"] == "POST"
        assert info["path"] == "/api/v1/auth/login"
        assert "timestamp" in info
    
    def test_extract_request_info_no_client(self, auth_controller):
        """Test request info extraction with no client"""
        request = Mock()
        request.client = None
        request.headers = {"user-agent": "Mozilla/5.0"}
        request.method = "POST"
        request.url.path = "/api/v1/auth/login"
        
        info = auth_controller._extract_request_info(request)
        
        assert info["ip_address"] is None
        assert info["user_agent"] == "Mozilla/5.0"
        assert info["method"] == "POST"
        assert info["path"] == "/api/v1/auth/login"
        assert "timestamp" in info
    
    @pytest.mark.asyncio
    async def test_login_success(self, auth_controller, mock_request):
        """Test successful login"""
        # Mock successful login response
        mock_response = {
            "user": {
                "id": "507f1f77bcf86cd799439011",
                "username": "testuser",
                "email": "test@example.com",
                "roles": ["user"],
                "permissions": ["read"]
            },
            "access_token": "access_token_123",
            "refresh_token": "refresh_token_123",
            "token_type": "bearer",
            "expires_in": 1800
        }
        
        auth_controller.auth_service.login.return_value = mock_response
        
        login_data = LoginRequest(
            username="testuser",
            password="password123"
        )
        
        result = await auth_controller.login(login_data, mock_request)
        
        assert result is not None
        assert "user" in result
        assert "access_token" in result
        assert "refresh_token" in result
        assert result["user"]["username"] == "testuser"
        
        # Verify auth service was called
        auth_controller.auth_service.login.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_login_failure(self, auth_controller, mock_request):
        """Test failed login"""
        # Mock failed login (returns None)
        auth_controller.auth_service.login.return_value = None
        
        login_data = LoginRequest(
            username="testuser",
            password="wrongpassword"
        )
        
        with pytest.raises(HTTPException) as exc_info:
            await auth_controller.login(login_data, mock_request)
        
        assert exc_info.value.status_code == 401
        assert "Invalid username or password" in str(exc_info.value.detail)
    
    @pytest.mark.asyncio
    async def test_login_exception(self, auth_controller, mock_request):
        """Test login with exception"""
        # Mock auth service to raise exception
        auth_controller.auth_service.login.side_effect = Exception("Database error")
        
        login_data = LoginRequest(
            username="testuser",
            password="password123"
        )
        
        with pytest.raises(HTTPException) as exc_info:
            await auth_controller.login(login_data, mock_request)
        
        assert exc_info.value.status_code == 500
        assert "Internal server error" in str(exc_info.value.detail)
    
    @pytest.mark.asyncio
    async def test_refresh_token_success(self, auth_controller, mock_request):
        """Test successful token refresh"""
        # Mock successful refresh response
        mock_response = {
            "access_token": "new_access_token_123",
            "token_type": "bearer",
            "expires_in": 1800
        }
        
        auth_controller.auth_service.refresh_token.return_value = mock_response
        
        refresh_data = RefreshTokenRequest(
            refresh_token="refresh_token_123"
        )
        
        result = await auth_controller.refresh_token(refresh_data, mock_request)
        
        assert result is not None
        assert "access_token" in result
        assert result["access_token"] == "new_access_token_123"
        
        # Verify auth service was called
        auth_controller.auth_service.refresh_token.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_refresh_token_failure(self, auth_controller, mock_request):
        """Test failed token refresh"""
        # Mock failed refresh (returns None)
        auth_controller.auth_service.refresh_token.return_value = None
        
        refresh_data = RefreshTokenRequest(
            refresh_token="invalid_token"
        )
        
        with pytest.raises(HTTPException) as exc_info:
            await auth_controller.refresh_token(refresh_data, mock_request)
        
        assert exc_info.value.status_code == 401
        assert "Invalid or expired refresh token" in str(exc_info.value.detail)
    
    @pytest.mark.asyncio
    async def test_logout_success(self, auth_controller, mock_request):
        """Test successful logout"""
        # Mock successful logout
        auth_controller.auth_service.logout.return_value = True
        
        result = await auth_controller.logout("access_token_123", "refresh_token_123", mock_request)
        
        assert result is not None
        assert result["message"] == "Logged out successfully"
        
        # Verify auth service was called
        auth_controller.auth_service.logout.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_logout_failure(self, auth_controller, mock_request):
        """Test failed logout"""
        # Mock failed logout
        auth_controller.auth_service.logout.return_value = False
        
        with pytest.raises(HTTPException) as exc_info:
            await auth_controller.logout("invalid_token", "invalid_refresh_token", mock_request)
        
        assert exc_info.value.status_code == 400
        assert "Invalid token" in str(exc_info.value.detail)
    
    @pytest.mark.asyncio
    async def test_get_current_user_success(self, auth_controller, mock_auth_user_dto):
        """Test successful current user retrieval"""
        # Mock successful user retrieval
        auth_controller.auth_service.get_current_user.return_value = mock_auth_user_dto
        
        token = "valid_access_token"
        
        result = await auth_controller.get_current_user(token)
        
        assert result is not None
        assert result.id == "507f1f77bcf86cd799439011"
        assert result.username == "testuser"
        assert result.email == "test@example.com"
        
        # Verify auth service was called
        auth_controller.auth_service.get_current_user.assert_called_once_with(token)
    
    @pytest.mark.asyncio
    async def test_get_current_user_failure(self, auth_controller):
        """Test failed current user retrieval"""
        # Mock failed user retrieval
        auth_controller.auth_service.get_current_user.return_value = None
        
        token = "invalid_token"
        
        with pytest.raises(HTTPException) as exc_info:
            await auth_controller.get_current_user(token)
        
        assert exc_info.value.status_code == 401
        assert "Invalid or expired token" in str(exc_info.value.detail)
    
    @pytest.mark.asyncio
    async def test_change_password_success(self, auth_controller, mock_request):
        """Test successful password change"""
        # Mock successful password change
        auth_controller.auth_service.change_password.return_value = True
        
        user_id = "507f1f77bcf86cd799439011"
        password_data = ChangePasswordRequest(
            current_password="oldpassword",
            new_password="newpassword123"
        )
        
        result = await auth_controller.change_password(user_id, password_data, mock_request)
        
        assert result is not None
        assert result["message"] == "Password changed successfully"
        
        # Verify auth service was called
        auth_controller.auth_service.change_password.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_change_password_failure(self, auth_controller, mock_request):
        """Test failed password change"""
        # Mock failed password change
        auth_controller.auth_service.change_password.return_value = False
        
        user_id = "507f1f77bcf86cd799439011"
        password_data = ChangePasswordRequest(
            current_password="wrongpassword",
            new_password="newpassword123"
        )
        
        with pytest.raises(HTTPException) as exc_info:
            await auth_controller.change_password(user_id, password_data, mock_request)
        
        assert exc_info.value.status_code == 400
        assert "Invalid current password or password requirements not met" in str(exc_info.value.detail)
    
    @pytest.mark.asyncio
    async def test_change_password_exception(self, auth_controller, mock_request):
        """Test password change with exception"""
        # Mock auth service to raise exception
        auth_controller.auth_service.change_password.side_effect = Exception("Database error")
        
        user_id = "507f1f77bcf86cd799439011"
        password_data = ChangePasswordRequest(
            current_password="oldpassword",
            new_password="newpassword123"
        )
        
        with pytest.raises(HTTPException) as exc_info:
            await auth_controller.change_password(user_id, password_data, mock_request)
        
        assert exc_info.value.status_code == 500
        assert "Internal server error" in str(exc_info.value.detail)


if __name__ == "__main__":
    pytest.main([__file__]) 