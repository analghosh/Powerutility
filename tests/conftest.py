"""
Pytest configuration for unit tests
Ensures all database connections are mocked
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Global mocks to prevent any real database connections
@pytest.fixture(scope="session", autouse=True)
def mock_all_databases():
    """Mock all database connections for unit tests"""
    
    # Mock MongoDB
    with patch('pymongo.MongoClient') as mock_mongo:
        mock_client = MagicMock()
        mock_db = MagicMock()
        mock_collection = MagicMock()
        mock_mongo.return_value = mock_client
        mock_client.__getitem__.return_value = mock_db
        mock_db.__getitem__.return_value = mock_collection
        
        # Mock Redis
        with patch('redis.Redis') as mock_redis:
            mock_redis_instance = Mock()
            mock_redis.from_url.return_value = mock_redis_instance
            mock_redis_instance.sismember.return_value = False
            mock_redis_instance.sadd.return_value = 1
            mock_redis_instance.delete.return_value = 1
            
            # Mock settings
            with patch('services.auth.app.core.config.settings') as mock_settings:
                # Auth settings
                mock_settings.AUTH_DB_NAME = "test_auth_db"
                mock_settings.MONGODB_AUTH_COLLECTION = "test_users"
                mock_settings.MONGODB_URL = "mongodb://test:27017"
                
                # JWT settings
                mock_settings.JWT_SECRET_KEY = "test_secret_key"
                mock_settings.JWT_ALGORITHM = "HS256"
                mock_settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 30
                mock_settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS = 7
                
                # MFA settings
                mock_settings.MFA_ISSUER = "EPMS"
                mock_settings.APP_NAME = "EPMS"
                mock_settings.MFA_ALGORITHM = "SHA1"
                mock_settings.MFA_DIGITS = 6
                mock_settings.MFA_PERIOD = 30
                mock_settings.MFA_WINDOW = 1
                
                # Password settings
                mock_settings.BCRYPT_ROUNDS = 12
                mock_settings.MAX_LOGIN_ATTEMPTS = 5
                mock_settings.LOCKOUT_DURATION_MINUTES = 15
                mock_settings.PASSWORD_EXPIRY_DAYS = 90
                mock_settings.PASSWORD_HISTORY_COUNT = 5
                
                # Redis settings
                mock_settings.REDIS_URL = "redis://test:6379"
                
                yield mock_settings

@pytest.fixture(autouse=True)
def mock_logging():
    """Mock logging to avoid real log files"""
    with patch('logging.getLogger') as mock_logger:
        mock_logger_instance = Mock()
        mock_logger.return_value = mock_logger_instance
        yield mock_logger_instance

# Ensure no real environment variables are loaded
@pytest.fixture(autouse=True)
def mock_env_vars():
    """Mock environment variables"""
    with patch.dict(os.environ, {
        'AUTH_DB_NAME': 'test_auth_db',
        'MONGODB_URL': 'mongodb://test:27017',
        'JWT_SECRET_KEY': 'test_secret_key',
        'REDIS_URL': 'redis://test:6379'
    }):
        yield
