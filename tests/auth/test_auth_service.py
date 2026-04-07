"""
Unit tests for AuthService
"""

import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock

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
        
        # Mock the audit queue before importing
        with patch('libs.audit_queue.queue_manager.audit_queue') as mock_audit_queue:
            mock_audit_queue.add_to_queue.return_value = "job_123"
            
            # Now we can safely import
            from datetime import datetime, timedelta, timezone
            from services.auth.app.services.auth_service import AuthService
            from services.auth.app.core.dto import UserDTO, AuthUserDTO


class TestAuthService:
    """Test cases for AuthService"""
    
    @pytest.fixture
    def mock_audit_queue(self):
        """Mock audit queue"""
        with patch('services.auth.app.services.auth_service.audit_queue') as mock_queue:
            mock_queue.add_to_queue.return_value = "job_123"
            yield mock_queue
    
    @pytest.fixture
    def auth_service(self, mock_audit_queue):
        """Create AuthService instance for testing"""
        service = AuthService()
        # Properly mock the repository methods
        service.user_repo.authenticate_user = Mock()
        service.user_repo.get_user_by_id = Mock()
        service.user_repo.update_password = Mock()
        
        return service
    
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
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            login_attempts=0,
            locked_until=None,
            password_expires_at=datetime.now(timezone.utc) + timedelta(days=90),
            mfa_enabled=False,
            mfa_setup_complete=False,
            accessible_menus=["dashboard", "settings"]  # Added for menu-based RBAC testing
        )
    
    @pytest.fixture
    def mock_auth_user_dto(self, mock_user_dto):
        """Mock AuthUserDTO"""
        return AuthUserDTO.from_user_dto(mock_user_dto)
    
    @pytest.fixture
    def mock_request_info(self):
        """Mock request information"""
        return {
            "ip_address": "192.168.1.1",
            "user_agent": "Mozilla/5.0",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "timestamp": "2024-01-01T00:00:00Z"
        }
    
    def test_auth_service_initialization(self, auth_service):
        """Test AuthService initialization"""
        assert auth_service is not None
        assert hasattr(auth_service, 'user_repo')
        assert hasattr(auth_service, 'jwt_service')
        # The audit_repo is no longer a direct attribute since we use queue-based logging
        assert hasattr(auth_service, '_log_audit_event')
    
    def test_auth_service_has_required_methods(self, auth_service):
        """Test that AuthService has required methods"""
        assert hasattr(auth_service, 'login')
        assert hasattr(auth_service, 'refresh_token')
        assert hasattr(auth_service, 'logout')
        assert hasattr(auth_service, 'get_current_user')
        assert hasattr(auth_service, 'change_password')
        assert hasattr(auth_service, '_log_audit_event')  # New queue-based logging method
    
    def test_user_repo_mocking(self, auth_service):
        """Test that user repository methods are properly mocked"""
        # Test that mocked methods return what we set
        auth_service.user_repo.authenticate_user.return_value = "test_user"
        result = auth_service.user_repo.authenticate_user("username", "password")
        assert result == "test_user"
    
    def test_audit_queue_logging(self, auth_service, mock_audit_queue):
        """Test that audit events are properly queued"""
        # Test the new queue-based logging method
        job_id = auth_service._log_audit_event(
            event_type="test_event",
            username="testuser",
            success=True
        )
        
        # Verify that the audit queue was called
        mock_audit_queue.add_to_queue.assert_called_once()
        assert job_id == "job_123"
        
        # Verify the call arguments
        call_args = mock_audit_queue.add_to_queue.call_args
        assert call_args[1]['source_service'] == "auth"
        assert call_args[1]['destination'] == "mongo"
        
        # Check the log data structure
        log_data = call_args[1]['log_data']
        assert log_data['event_type'] == "test_event"
        assert log_data['username'] == "testuser"
        assert log_data['success'] is True
        assert log_data['service'] == "auth"
        assert 'timestamp' in log_data
    
    def test_jwt_service_has_required_methods(self, auth_service):
        """Test that JWT service has required methods"""
        jwt_service = auth_service.jwt_service
        assert hasattr(jwt_service, 'create_access_token')
        assert hasattr(jwt_service, 'create_refresh_token')
        assert hasattr(jwt_service, 'verify_token')
        assert hasattr(jwt_service, 'verify_access_token')
        assert hasattr(jwt_service, 'verify_refresh_token')
    
    def test_mock_user_dto_creation(self, mock_user_dto):
        """Test mock user DTO creation"""
        assert mock_user_dto.id == "507f1f77bcf86cd799439011"
        assert mock_user_dto.username == "testuser"
        assert mock_user_dto.email == "test@example.com"
        assert mock_user_dto.is_active is True
        assert mock_user_dto.roles == ["user"]
        assert mock_user_dto.permissions == ["read"]
        assert mock_user_dto.accessible_menus == ["dashboard", "settings"]
    
    def test_mock_auth_user_dto_creation(self, mock_auth_user_dto):
        """Test mock auth user DTO creation"""
        assert mock_auth_user_dto.id == "507f1f77bcf86cd799439011"
        assert mock_auth_user_dto.username == "testuser"
        assert mock_auth_user_dto.email == "test@example.com"
        assert mock_auth_user_dto.roles == ["user"]
        assert mock_auth_user_dto.permissions == ["read"]
        assert mock_auth_user_dto.accessible_menus == ["dashboard", "settings"]
    
    def test_mock_request_info_creation(self, mock_request_info):
        """Test mock request info creation"""
        assert mock_request_info["ip_address"] == "192.168.1.1"
        assert mock_request_info["user_agent"] == "Mozilla/5.0"
        assert mock_request_info["method"] == "POST"
        assert mock_request_info["path"] == "/api/v1/auth/login"
        assert mock_request_info["timestamp"] == "2024-01-01T00:00:00Z"


if __name__ == "__main__":
    pytest.main([__file__]) 