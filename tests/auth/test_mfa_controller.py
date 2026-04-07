"""
Unit tests for MFA Controller
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
from fastapi import HTTPException

# Import after global mocks in conftest.py
from services.auth.app.controllers.mfa_controller import MFAController
from services.auth.app.schemas.mfa import EnableMFARequest, EnableMFAResponse
from services.auth.app.core.dto import UserDTO


class TestMFAController:
    """Test cases for MFA Controller"""
    
    @pytest.fixture
    def mfa_controller(self):
        """Create MFAController instance for testing"""
        controller = MFAController()
        # Mock dependencies
        controller.user_model = Mock()
        controller.mfa_service = Mock()
        return controller
    
    @pytest.fixture
    def mock_user_dto(self):
        """Mock UserDTO without MFA enabled"""
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
            mfa_enabled=False,  # MFA not enabled
            mfa_setup_complete=False
        )
    
    @pytest.fixture
    def mock_user_with_mfa(self, mock_user_dto):
        """Mock UserDTO with MFA already enabled"""
        mock_user_dto.mfa_enabled = True
        mock_user_dto.mfa_setup_complete = True
        return mock_user_dto
    
    @pytest.fixture
    def enable_mfa_request(self):
        """Mock EnableMFARequest (empty body since no password required)"""
        return EnableMFARequest()
    
    @pytest.mark.asyncio
    async def test_enable_mfa_success(self, mfa_controller, mock_user_dto, enable_mfa_request):
        """Test successful MFA enablement"""
        # Arrange
        user_id = "507f1f77bcf86cd799439011"
        secret = "JBSWY3DPEHPK3PXP"
        qr_code = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAZoA..."
        backup_codes = ["CODE1234", "CODE5678", "CODE9012"]
        remaining_time = 25
        
        mfa_controller.user_model.get_user_by_id.return_value = mock_user_dto
        mfa_controller.mfa_service.generate_secret.return_value = secret
        mfa_controller.mfa_service.generate_qr_code.return_value = qr_code
        mfa_controller.mfa_service.get_remaining_time.return_value = remaining_time
        mfa_controller.user_model.update_user.return_value = True
        
        # Mock the generate_backup_codes method
        mfa_controller.generate_backup_codes = Mock(return_value=backup_codes)
        
        # Act
        result = await mfa_controller.enable_mfa(user_id, enable_mfa_request)
        
        # Assert
        assert isinstance(result, EnableMFAResponse)
        assert result.secret == secret
        assert result.qr_code == qr_code
        assert result.backup_codes == backup_codes
        assert result.remaining_time == remaining_time
        
        # Verify user_model.update_user was called with correct data
        mfa_controller.user_model.update_user.assert_called_once_with(user_id, {
            "mfa_secret": secret,
            "mfa_enabled": True,
            "mfa_backup_codes": backup_codes,
            "mfa_setup_complete": False
        })
        
        # Verify services were called
        mfa_controller.mfa_service.generate_secret.assert_called_once()
        mfa_controller.mfa_service.generate_qr_code.assert_called_once_with(secret, mock_user_dto.username)
        mfa_controller.mfa_service.get_remaining_time.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_enable_mfa_user_not_found(self, mfa_controller, enable_mfa_request):
        """Test MFA enablement when user doesn't exist"""
        # Arrange
        user_id = "nonexistent_user_id"
        mfa_controller.user_model.get_user_by_id.return_value = None
        
        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            await mfa_controller.enable_mfa(user_id, enable_mfa_request)
        
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "User not found"
        
        # Verify get_user_by_id was called
        mfa_controller.user_model.get_user_by_id.assert_called_once_with(user_id)
        
        # Verify other services were not called
        mfa_controller.mfa_service.generate_secret.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_enable_mfa_already_enabled(self, mfa_controller, mock_user_with_mfa, enable_mfa_request):
        """Test MFA enablement when MFA is already enabled"""
        # Arrange
        user_id = "507f1f77bcf86cd799439011"
        mfa_controller.user_model.get_user_by_id.return_value = mock_user_with_mfa
        
        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            await mfa_controller.enable_mfa(user_id, enable_mfa_request)
        
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "MFA is already enabled"
        
        # Verify get_user_by_id was called
        mfa_controller.user_model.get_user_by_id.assert_called_once_with(user_id)
        
        # Verify other services were not called
        mfa_controller.mfa_service.generate_secret.assert_not_called()
        mfa_controller.user_model.update_user.assert_not_called()
    
    def test_generate_backup_codes(self, mfa_controller):
        """Test backup code generation"""
        # Act
        codes = mfa_controller.generate_backup_codes(count=5)
        
        # Assert
        assert len(codes) == 5
        for code in codes:
            assert isinstance(code, str)
            assert len(code) == 8  # 8-character codes
            assert code.isupper()  # Should be uppercase
    
    def test_generate_backup_codes_default_count(self, mfa_controller):
        """Test backup code generation with default count"""
        # Act
        codes = mfa_controller.generate_backup_codes()
        
        # Assert
        assert len(codes) == 8  # Default count
    
    @pytest.mark.asyncio
    async def test_enable_mfa_no_password_verification(self, mfa_controller, mock_user_dto, enable_mfa_request):
        """Test that MFA enablement doesn't require password verification"""
        # Arrange
        user_id = "507f1f77bcf86cd799439011"
        mfa_controller.user_model.get_user_by_id.return_value = mock_user_dto
        mfa_controller.mfa_service.generate_secret.return_value = "SECRET123"
        mfa_controller.mfa_service.generate_qr_code.return_value = "QR_CODE"
        mfa_controller.mfa_service.get_remaining_time.return_value = 30
        mfa_controller.user_model.update_user.return_value = True
        mfa_controller.generate_backup_codes = Mock(return_value=["CODE1"])
        
        # Act
        await mfa_controller.enable_mfa(user_id, enable_mfa_request)
        
        # Assert - Verify no authentication methods were called
        # The old implementation would call authenticate_user, but now it shouldn't
        assert not hasattr(mfa_controller.user_model, 'authenticate_user') or \
               not mfa_controller.user_model.authenticate_user.called
    
    @pytest.mark.asyncio
    async def test_enable_mfa_request_has_no_fields(self, enable_mfa_request):
        """Test that EnableMFARequest has no required fields"""
        # Assert
        # The request should be valid with no fields
        assert isinstance(enable_mfa_request, EnableMFARequest)
        
        # Verify it can be created with empty dict
        empty_request = EnableMFARequest()
        assert isinstance(empty_request, EnableMFARequest)
