"""
High-Performance Fixtures for Ingestion Tests
Optimized for maximum test execution speed
"""

import pytest
import os
import sys
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from datetime import datetime, timedelta
from pathlib import Path

# Performance: Set critical environment variables early
os.environ.update({
    'MONGODB_URI': 'mongodb://test:27017',
    'MONGODB_DATABASE': 'test_db', 
    'LOG_LEVEL': 'CRITICAL',  # Minimize logging overhead
    'DEBUG': 'False',
    'TESTING': 'True',
    'PYTHONDONTWRITEBYTECODE': '1',  # Skip .pyc file creation
    'PYTHONHASHSEED': '0',  # Deterministic hash for consistent performance
})

# Add project root once
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


# Global patch manager for better performance
_GLOBAL_PATCHES = []

def _start_global_patches():
    """Start all global patches at once for better performance"""
    global _GLOBAL_PATCHES
    
    patches = [
        # Core infrastructure mocks
        patch('pymongo.MongoClient', return_value=MagicMock()),
        patch('redis.Redis', return_value=MagicMock()),
        
        # Service initialization mocks (prevent expensive __init__)
        patch('services.ingestion.app.services.raw_data_service.RawDataService.__init__', return_value=None),
        # Legacy raw_data_processor removed - using unified system now
        patch('services.ingestion.app.repository.upload_tracking_repository.UploadTrackingRepository.__init__', return_value=None),
        patch('services.ingestion.app.repository.demand_raw_data_repository.DemandRawDataRepository.__init__', return_value=None),
        # Legacy supply_raw_data_repository removed - using unified system now
        
        # Logging mocks (major performance impact)
        patch('services.ingestion.app.core.logger.get_ingestion_logger', return_value=MagicMock()),
        patch('logging.getLogger', return_value=MagicMock()),
        
        # Config and settings mocks
        patch('services.ingestion.app.core.config.settings'),
        
        # File system mocks
        patch('pathlib.Path.mkdir'),
        patch('pathlib.Path.exists', return_value=True),
    ]
    
    _GLOBAL_PATCHES = [p.start() for p in patches]

def _stop_global_patches():
    """Stop all global patches"""
    global _GLOBAL_PATCHES
    for patch_obj in reversed(_GLOBAL_PATCHES):
        try:
            patch_obj.stop()
        except RuntimeError:
            pass  # Already stopped
    _GLOBAL_PATCHES.clear()


@pytest.fixture(scope="session", autouse=True)
def performance_optimized_mocks():
    """High-performance global mocks applied once per test session"""
    _start_global_patches()
    yield
    _stop_global_patches()


# Pre-configured mock instances for reuse (performance optimization)
_MOCK_AUTH_RESPONSE = {
    "user_id": "test_user_123",
    "username": "test_user", 
    "roles": ["admin"],
    "token": "test_token_123"
}

_MOCK_UPLOAD_RECORD = {
    "upload_id": "550e8400-e29b-41d4-a716-446655440000",
    "file_name": "test_data.csv",
    "status": "COMPLETED",
    "status_message": "Processing completed successfully", 
    "file_size": 1024,
    "created_at": datetime.now() - timedelta(minutes=5),
    "updated_at": "2024-01-01T10:00:05Z",
    "completed_at": "2024-01-01T10:00:05Z"
}

_MOCK_FILE_STORAGE_RESPONSE = {
    "file_size": 1024,
    "file_extension": ".csv", 
    "file_path": "/tmp/test_file.csv"
}

_MOCK_RAW_DATA_RESPONSE = {
    "document_id": "test_doc_123",
    "total_records": 2,
    "collection_name": "test_demand_raw_data",
    "provider_type": "test_data"
}


@pytest.fixture(scope="session")
def mock_auth_service():
    """High-performance mock auth service (session-scoped)"""
    mock_auth = MagicMock()
    mock_auth.validate_token_and_get_user = AsyncMock(return_value=_MOCK_AUTH_RESPONSE)
    mock_auth.check_page_access = AsyncMock(return_value=True)
    return mock_auth


@pytest.fixture(scope="session")
def mock_file_storage_service():
    """High-performance mock file storage service (session-scoped)"""
    mock_storage = MagicMock()
    mock_storage.store_file = AsyncMock(return_value=_MOCK_FILE_STORAGE_RESPONSE)
    mock_storage.get_file_path.return_value = "/test/path/file.csv"
    mock_storage.delete_file.return_value = True
    return mock_storage


@pytest.fixture(scope="session")
def mock_upload_tracking_repository():
    """High-performance mock upload tracking repository (session-scoped)"""
    mock_repo = MagicMock()
    mock_instance = MagicMock()
    mock_instance.create_upload_record.return_value = "507f1f77bcf86cd799439011"
    mock_instance.get_upload_by_id.return_value = _MOCK_UPLOAD_RECORD
    mock_instance.update_upload_status.return_value = True
    mock_repo.return_value = mock_instance
    return mock_repo


@pytest.fixture(scope="session")
def mock_background_processor():
    """High-performance mock background processor (session-scoped)"""
    mock_bg = MagicMock()
    mock_bg.process_uploaded_file = AsyncMock()
    return mock_bg


# Lightweight fixtures for performance
@pytest.fixture(scope="session")
def auth_headers():
    """Standard auth headers for requests (session-scoped for reuse)"""
    return {"Authorization": "Bearer test_token_123"}


@pytest.fixture(scope="session") 
def sample_csv_content():
    """Sample CSV content for tests (session-scoped for reuse)"""
    return b"timeblock,value\n2024-01-01,100.5\n2024-01-02,200.0"


@pytest.fixture(scope="session")
def mock_raw_data_service():
    """High-performance mock raw data service (session-scoped)"""
    mock_service = MagicMock()
    mock_service.process_csv_upload = AsyncMock(return_value=_MOCK_RAW_DATA_RESPONSE)
    return mock_service


@pytest.fixture(scope="session")
def mock_raw_data_processor():
    """High-performance mock raw data processor (session-scoped)"""
    mock_processor = MagicMock()
    mock_processor.get_pending_demand_data.return_value = []
    mock_processor.get_pending_supply_data.return_value = []
    mock_processor.translate_demand_data.return_value = []
    mock_processor.translate_supply_data.return_value = []
    return mock_processor


# Minimal test app (only if needed)
@pytest.fixture(scope="session")
def minimal_test_app():
    """Ultra-lightweight FastAPI app for testing (session-scoped)"""
    from fastapi import FastAPI
    
    app = FastAPI(title="Test Ingestion Service")
    
    @app.get("/health")
    async def health():
        return {
            "status": "healthy",
            "service": "ingestion-service", 
            "version": "1.0.0",
            "timestamp": "2024-01-01T00:00:00Z"
        }
    
    return app


@pytest.fixture(scope="session")
def test_client(minimal_test_app):
    """Ultra-lightweight test client (session-scoped)"""
    from fastapi.testclient import TestClient
    return TestClient(minimal_test_app)