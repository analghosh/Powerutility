"""
Unit tests for MFA Service
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import pyotp
import qrcode
import io
import base64

# Import after global mocks in conftest.py
from services.auth.app.services.mfa_service import MFAService


class TestMFAService:
    """Test cases for MFA Service"""
    
    @pytest.fixture
    def mfa_service(self):
        """Create MFAService instance for testing"""
        return MFAService()
    
    def test_generate_secret(self, mfa_service):
        """Test TOTP secret generation"""
        # Act
        secret = mfa_service.generate_secret()
        
        # Assert
        assert isinstance(secret, str)
        assert len(secret) == 32  # Base32 encoded secret should be 32 chars
        
        # Verify it's a valid base32 string
        try:
            pyotp.TOTP(secret)
            valid_secret = True
        except:
            valid_secret = False
        assert valid_secret
    
    def test_generate_secret_uniqueness(self, mfa_service):
        """Test that generated secrets are unique"""
        # Act
        secret1 = mfa_service.generate_secret()
        secret2 = mfa_service.generate_secret()
        
        # Assert
        assert secret1 != secret2
    
    @patch('pyotp.TOTP')
    def test_verify_totp_valid(self, mock_totp, mfa_service):
        """Test TOTP code verification with valid code"""
        # Arrange
        secret = "JBSWY3DPEHPK3PXP"
        valid_code = "123456"
        
        mock_totp_instance = Mock()
        mock_totp.return_value = mock_totp_instance
        mock_totp_instance.verify.return_value = True
        
        # Act
        result = mfa_service.verify_totp(secret, valid_code)
        
        # Assert
        assert result is True
        mock_totp.assert_called_once_with(
            secret,
            digits=mfa_service.digits,
            interval=mfa_service.period,
            name=mfa_service.issuer
        )
        mock_totp_instance.verify.assert_called_once_with(valid_code, valid_window=mfa_service.window)
    
    @patch('pyotp.TOTP')
    def test_verify_totp_invalid(self, mock_totp, mfa_service):
        """Test TOTP code verification with invalid code"""
        # Arrange
        secret = "JBSWY3DPEHPK3PXP"
        invalid_code = "000000"
        
        mock_totp_instance = Mock()
        mock_totp.return_value = mock_totp_instance
        mock_totp_instance.verify.return_value = False
        
        # Act
        result = mfa_service.verify_totp(secret, invalid_code)
        
        # Assert
        assert result is False
        mock_totp.assert_called_once_with(
            secret,
            digits=mfa_service.digits,
            interval=mfa_service.period,
            name=mfa_service.issuer
        )
        mock_totp_instance.verify.assert_called_once_with(invalid_code, valid_window=mfa_service.window)
    
    def test_verify_totp_empty_secret(self, mfa_service):
        """Test TOTP code verification with empty secret"""
        # Arrange
        empty_secret = ""
        code = "123456"
        
        # Act
        result = mfa_service.verify_totp(empty_secret, code)
        
        # Assert
        assert result is False
    
    def test_verify_totp_empty_code(self, mfa_service):
        """Test TOTP code verification with empty code"""
        # Arrange
        secret = "JBSWY3DPEHPK3PXP"
        empty_code = ""
        
        # Act
        result = mfa_service.verify_totp(secret, empty_code)
        
        # Assert
        assert result is False
    
    def test_generate_qr_code(self, mfa_service):
        """Test QR code generation"""
        # Arrange
        secret = pyotp.random_base32()
        username = "testuser@example.com"
        
        # Act
        qr_code_data = mfa_service.generate_qr_code(secret, username)
        
        # Assert
        assert isinstance(qr_code_data, str)
        assert qr_code_data.startswith("data:image/png;base64,")
        
        # Verify it's valid base64
        try:
            base64_data = qr_code_data.split(",")[1]
            base64.b64decode(base64_data)
            valid_base64 = True
        except:
            valid_base64 = False
        assert valid_base64
    
    def test_generate_qr_code_contains_correct_url(self, mfa_service):
        """Test that QR code contains the correct TOTP URL"""
        # Arrange
        secret = "JBSWY3DPEHPK3PXP"  # Fixed secret for testing
        username = "testuser@example.com"
        
        with patch('pyotp.totp.TOTP') as mock_totp:
            mock_totp_instance = Mock()
            mock_totp.return_value = mock_totp_instance
            expected_url = f"otpauth://totp/EPMS:{username}?secret={secret}&issuer=EPMS"
            mock_totp_instance.provisioning_uri.return_value = expected_url
            
            # Act
            qr_code_data = mfa_service.generate_qr_code(secret, username)
            
            # Assert
            mock_totp.assert_called_once_with(
                secret,
                digits=mfa_service.digits,
                interval=mfa_service.period,
                name=mfa_service.issuer
            )
            mock_totp_instance.provisioning_uri.assert_called_once_with(
                name=username,
                issuer_name=mfa_service.issuer
            )
    
    def test_get_remaining_time(self, mfa_service):
        """Test getting remaining time in TOTP period"""
        # Act
        remaining_time = mfa_service.get_remaining_time()
        
        # Assert
        assert isinstance(remaining_time, int)
        assert 0 <= remaining_time <= 30  # TOTP period is typically 30 seconds
    
    def test_get_remaining_time_consistency(self, mfa_service):
        """Test that remaining time is consistent within the same second"""
        # Act
        time1 = mfa_service.get_remaining_time()
        time2 = mfa_service.get_remaining_time()
        
        # Assert
        # Should be the same or differ by at most 1 second
        assert abs(time1 - time2) <= 1
    
    @patch('pyotp.TOTP')
    def test_verify_totp_with_settings(self, mock_totp, mfa_service):
        """Test TOTP verification uses correct settings"""
        # Arrange
        secret = "JBSWY3DPEHPK3PXP"
        code = "123456"
        mock_totp_instance = Mock()
        mock_totp.return_value = mock_totp_instance
        mock_totp_instance.verify.return_value = True
        
        # Act
        result = mfa_service.verify_totp(secret, code)
        
        # Assert
        assert result is True
        mock_totp.assert_called_once_with(
            secret,
            digits=mfa_service.digits,
            interval=mfa_service.period,
            name=mfa_service.issuer
        )
    
    def test_mfa_service_initialization(self, mfa_service):
        """Test MFAService initialization"""
        # Assert
        assert mfa_service is not None
        assert hasattr(mfa_service, 'generate_secret')
        assert hasattr(mfa_service, 'verify_totp')
        assert hasattr(mfa_service, 'generate_qr_code')
        assert hasattr(mfa_service, 'get_remaining_time')
        assert hasattr(mfa_service, 'generate_totp')
    
    def test_secret_format(self, mfa_service):
        """Test that generated secret follows Base32 format"""
        # Act
        secret = mfa_service.generate_secret()
        
        # Assert
        # Base32 should only contain A-Z and 2-7
        import re
        assert re.match(r'^[A-Z2-7]+$', secret)
    
    def test_generate_totp_code(self, mfa_service):
        """Test TOTP code generation"""
        # Arrange
        secret = pyotp.random_base32()
        
        # Act
        code = mfa_service.generate_totp(secret)
        
        # Assert
        assert isinstance(code, str)
        assert len(code) == 6  # Default TOTP is 6 digits
        assert code.isdigit()
    
    def test_verify_totp_uses_window_buffer(self, mfa_service):
        """Test that TOTP verification uses window buffer"""
        # Arrange
        secret = pyotp.random_base32()
        
        with patch('pyotp.TOTP') as mock_totp:
            mock_totp_instance = Mock()
            mock_totp.return_value = mock_totp_instance
            mock_totp_instance.verify.return_value = True
            
            # Act
            result = mfa_service.verify_totp(secret, "123456")
            
            # Assert
            assert result is True
            mock_totp_instance.verify.assert_called_once_with("123456", valid_window=mfa_service.window)
    
    def test_configurable_issuer_from_app_name(self, mfa_service):
        """Test that MFA issuer uses APP_NAME from config"""
        # Assert
        # Should use APP_NAME as issuer (from config: EPMS)
        assert mfa_service.issuer == "EPMS"
    
    @patch('qrcode.QRCode')
    def test_qr_code_generation_parameters(self, mock_qrcode, mfa_service):
        """Test QR code generation with correct parameters"""
        # Arrange
        secret = "JBSWY3DPEHPK3PXP"
        username = "testuser@example.com"
        
        mock_qr_instance = Mock()
        mock_qrcode.return_value = mock_qr_instance
        mock_img = Mock()
        mock_qr_instance.make_image.return_value = mock_img
        
        # Mock the image save operation
        mock_buffer = Mock()
        mock_img.save = Mock()
        
        with patch('io.BytesIO', return_value=mock_buffer):
            with patch('base64.b64encode', return_value=b'base64data'):
                # Act
                mfa_service.generate_qr_code(secret, username)
                
                # Assert
                mock_qrcode.assert_called_once_with(
                    version=1,
                    error_correction=qrcode.ERROR_CORRECT_L,
                    box_size=10,
                    border=4
                )
