"""
Unit tests for JWTValidator (Shared Component)
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
import jwt
from libs.core.jwt_validator import JWTValidator, TokenPayload


class TestJWTValidator:
    """Test cases for JWTValidator (Shared Component)"""
    
    @pytest.fixture
    def jwt_validator(self):
        """Create JWTValidator instance for testing"""
        # Create a simple validator with test settings
        validator = JWTValidator()
        # Override settings for testing
        validator.secret_key = "test-secret-key"
        validator.algorithm = "HS256"
        validator.access_token_expire_minutes = 30
        return validator
    
    @pytest.fixture
    def valid_token_payload(self):
        """Valid token payload"""
        return {
            "sub": "507f1f77bcf86cd799439011",
            "username": "testuser",
            "email": "test@example.com",
            "roles": ["user"],
            "permissions": ["read"],
            "exp": datetime.now() + timedelta(minutes=30),
            "type": "access"
        }
    
    @pytest.fixture
    def valid_token(self, jwt_validator, valid_token_payload):
        """Valid JWT token"""
        return jwt.encode(valid_token_payload, jwt_validator.secret_key, algorithm=jwt_validator.algorithm)
    
    def test_init(self, jwt_validator):
        """Test JWTValidator initialization"""
        assert jwt_validator.secret_key == "test-secret-key"
        assert jwt_validator.algorithm == "HS256"
        assert jwt_validator.access_token_expire_minutes == 30
        assert jwt_validator.redis_client is not None
    
    def test_validate_token_success(self, jwt_validator, valid_token):
        """Test successful token validation"""
        # Mock Redis blacklist check
        jwt_validator.is_token_blacklisted = Mock(return_value=False)
        
        result = jwt_validator.validate_token(valid_token)
        
        assert result is not None
        assert isinstance(result, TokenPayload)
        assert result.user_id == "507f1f77bcf86cd799439011"
        assert result.username == "testuser"
        assert result.email == "test@example.com"
        assert result.roles == ["user"]
        assert result.permissions == ["read"]
    
    def test_validate_token_invalid_signature(self, jwt_validator):
        """Test token validation with invalid signature"""
        # Create token with wrong secret
        invalid_token = jwt.encode(
            {"sub": "user123", "username": "testuser"},
            "wrong-secret",
            algorithm="HS256"
        )
        
        result = jwt_validator.validate_token(invalid_token)
        
        assert result is None
    
    def test_validate_token_expired(self, jwt_validator):
        """Test token validation with expired token"""
        # Create expired token
        expired_payload = {
            "sub": "507f1f77bcf86cd799439011",
            "username": "testuser",
            "email": "test@example.com",
            "roles": ["user"],
            "permissions": ["read"],
            "exp": datetime.now() - timedelta(minutes=30),  # Expired
            "type": "access"
        }
        expired_token = jwt.encode(expired_payload, jwt_validator.secret_key, algorithm=jwt_validator.algorithm)
        
        result = jwt_validator.validate_token(expired_token)
        
        assert result is None
    
    def test_validate_token_blacklisted(self, jwt_validator, valid_token):
        """Test token validation with blacklisted token"""
        # Mock Redis blacklist check to return True
        jwt_validator.is_token_blacklisted = Mock(return_value=True)
        
        result = jwt_validator.validate_token(valid_token)
        
        assert result is None
        jwt_validator.is_token_blacklisted.assert_called_once_with(valid_token)
    
    def test_validate_token_malformed(self, jwt_validator):
        """Test token validation with malformed token"""
        result = jwt_validator.validate_token("invalid.token.format")
        
        assert result is None
    
    def test_validate_token_with_details_success(self, jwt_validator, valid_token):
        """Test token validation with details"""
        # Mock Redis blacklist check
        jwt_validator.is_token_blacklisted = Mock(return_value=False)
        
        result, error = jwt_validator.validate_token_with_details(valid_token)
        
        assert result is not None
        assert error is None
        assert isinstance(result, TokenPayload)
        assert result.user_id == "507f1f77bcf86cd799439011"
    
    def test_validate_token_with_details_invalid(self, jwt_validator):
        """Test token validation with details for invalid token"""
        result, error = jwt_validator.validate_token_with_details("invalid.token")
        
        assert result is None
        assert error is not None
        assert "Invalid token" in error
    
    def test_validate_token_with_details_expired(self, jwt_validator):
        """Test token validation with details for expired token"""
        # Create expired token
        expired_payload = {
            "sub": "507f1f77bcf86cd799439011",
            "username": "testuser",
            "email": "test@example.com",
            "roles": ["user"],
            "permissions": ["read"],
            "exp": datetime.now() - timedelta(minutes=30),  # Expired
            "type": "access"
        }
        expired_token = jwt.encode(expired_payload, jwt_validator.secret_key, algorithm=jwt_validator.algorithm)
        
        result, error = jwt_validator.validate_token_with_details(expired_token)
        
        assert result is None
        assert error is not None
        assert "expired" in error.lower()
    
    def test_validate_token_with_details_blacklisted(self, jwt_validator, valid_token):
        """Test token validation with details for blacklisted token"""
        # Mock Redis blacklist check to return True
        jwt_validator.is_token_blacklisted = Mock(return_value=True)
        
        result, error = jwt_validator.validate_token_with_details(valid_token)
        
        assert result is None
        assert error is not None
        assert "blacklisted" in error.lower()
    
    def test_get_user_from_token_success(self, jwt_validator, valid_token):
        """Test getting user from token"""
        # Mock Redis blacklist check
        jwt_validator.is_token_blacklisted = Mock(return_value=False)
        
        result = jwt_validator.get_user_from_token(valid_token)
        
        assert result is not None
        assert result.user_id == "507f1f77bcf86cd799439011"
        assert result.username == "testuser"
        assert result.email == "test@example.com"
    
    def test_get_user_from_token_invalid(self, jwt_validator):
        """Test getting user from invalid token"""
        result = jwt_validator.get_user_from_token("invalid.token")
        
        assert result is None
    
    def test_has_permission_success(self, jwt_validator, valid_token):
        """Test permission check with valid permission"""
        # Mock Redis blacklist check
        jwt_validator.is_token_blacklisted = Mock(return_value=False)
        
        result = jwt_validator.has_permission(valid_token, "read")
        
        assert result is True
    
    def test_has_permission_failure(self, jwt_validator, valid_token):
        """Test permission check with invalid permission"""
        # Mock Redis blacklist check
        jwt_validator.is_token_blacklisted = Mock(return_value=False)
        
        result = jwt_validator.has_permission(valid_token, "write")
        
        assert result is False
    
    def test_has_permission_invalid_token(self, jwt_validator):
        """Test permission check with invalid token"""
        result = jwt_validator.has_permission("invalid.token", "read")
        
        assert result is False
    
    def test_has_role_success(self, jwt_validator, valid_token):
        """Test role check with valid role"""
        # Mock Redis blacklist check
        jwt_validator.is_token_blacklisted = Mock(return_value=False)
        
        result = jwt_validator.has_role(valid_token, "user")
        
        assert result is True
    
    def test_has_role_failure(self, jwt_validator, valid_token):
        """Test role check with invalid role"""
        # Mock Redis blacklist check
        jwt_validator.is_token_blacklisted = Mock(return_value=False)
        
        result = jwt_validator.has_role(valid_token, "admin")
        
        assert result is False
    
    def test_has_role_invalid_token(self, jwt_validator):
        """Test role check with invalid token"""
        result = jwt_validator.has_role("invalid.token", "user")
        
        assert result is False
    
    def test_has_any_role_success(self, jwt_validator, valid_token):
        """Test any role check with valid role"""
        # Mock Redis blacklist check
        jwt_validator.is_token_blacklisted = Mock(return_value=False)
        
        result = jwt_validator.has_any_role(valid_token, ["admin", "user"])
        
        assert result is True
    
    def test_has_any_role_failure(self, jwt_validator, valid_token):
        """Test any role check with no matching roles"""
        # Mock Redis blacklist check
        jwt_validator.is_token_blacklisted = Mock(return_value=False)
        
        result = jwt_validator.has_any_role(valid_token, ["admin", "moderator"])
        
        assert result is False
    
    def test_has_any_role_invalid_token(self, jwt_validator):
        """Test any role check with invalid token"""
        result = jwt_validator.has_any_role("invalid.token", ["user"])
        
        assert result is False
    
    def test_is_token_blacklisted_true(self, jwt_validator):
        """Test token blacklist check when token is blacklisted"""
        # Mock Redis to return True
        jwt_validator.redis_client.sismember = Mock(return_value=True)
        
        result = jwt_validator.is_token_blacklisted("blacklisted_token")
        
        assert result is True
        jwt_validator.redis_client.sismember.assert_called_once_with("jwt_blacklist", "blacklisted_token")
    
    def test_is_token_blacklisted_false(self, jwt_validator):
        """Test token blacklist check when token is not blacklisted"""
        # Mock Redis to return False
        jwt_validator.redis_client.sismember = Mock(return_value=False)
        
        result = jwt_validator.is_token_blacklisted("valid_token")
        
        assert result is False
        jwt_validator.redis_client.sismember.assert_called_once_with("jwt_blacklist", "valid_token")
    
    def test_is_token_blacklisted_redis_error(self, jwt_validator):
        """Test token blacklist check with Redis error"""
        # Mock Redis to raise exception
        jwt_validator.redis_client.sismember = Mock(side_effect=Exception("Redis error"))
        
        result = jwt_validator.is_token_blacklisted("token")
        
        assert result is False
    
    def test_is_token_expired_true(self, jwt_validator):
        """Test token expiration check when token is expired"""
        # Create expired token
        expired_payload = {
            "sub": "507f1f77bcf86cd799439011",
            "username": "testuser",
            "exp": datetime.now() - timedelta(minutes=30),  # Expired
            "type": "access"
        }
        expired_token = jwt.encode(expired_payload, jwt_validator.secret_key, algorithm=jwt_validator.algorithm)
        
        result = jwt_validator.is_token_expired(expired_token)
        
        assert result is True
    
    def test_is_token_expired_false(self, jwt_validator, valid_token):
        """Test token expiration check when token is not expired"""
        result = jwt_validator.is_token_expired(valid_token)
        
        assert result is False
    
    def test_is_token_expired_invalid_token(self, jwt_validator):
        """Test token expiration check with invalid token"""
        result = jwt_validator.is_token_expired("invalid.token")
        
        assert result is True  # Invalid tokens are considered expired
    
    def test_blacklist_token_success(self, jwt_validator):
        """Test successful token blacklisting"""
        jwt_validator.redis_client.sadd = Mock(return_value=1)
        
        result = jwt_validator.blacklist_token("token_to_blacklist")
        
        assert result is True
        jwt_validator.redis_client.sadd.assert_called_once_with("jwt_blacklist", "token_to_blacklist")
    
    def test_blacklist_token_redis_error(self, jwt_validator):
        """Test token blacklisting with Redis error"""
        jwt_validator.redis_client.sadd = Mock(side_effect=Exception("Redis error"))
        
        result = jwt_validator.blacklist_token("token_to_blacklist")
        
        assert result is False


if __name__ == "__main__":
    pytest.main([__file__]) 