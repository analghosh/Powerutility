"""
Unit tests for FileUploadController
"""

import unittest
from unittest.mock import Mock, patch, AsyncMock
from fastapi import UploadFile, HTTPException

from services.ingestion.app.controller.file_upload_controller import FileUploadController


class TestFileUploadController(unittest.TestCase):
    """Test cases for FileUploadController"""
    
    def setUp(self):
        """Set up test fixtures"""
        with patch('services.ingestion.app.controller.file_upload_controller.settings') as mock_settings:
            mock_settings.MONGODB_URI = "mongodb://test"
            mock_settings.MONGODB_DATABASE = "test_db"
            self.controller = FileUploadController()
    
    @patch('services.ingestion.app.controller.file_upload_controller.ProviderService')
    async def test_validate_provider_combination_success(self, mock_provider_service_class):
        """Test successful provider combination validation"""
        # Arrange
        provider_type = "TEST_DATA"
        data_type = "demand"
        
        mock_provider_service = Mock()
        mock_provider_service.validate_provider_type_data_type_combination.return_value = {
            "valid": True,
            "provider_id": "test_id",
            "provider_name": "Test Provider",
            "provider_type": provider_type,
            "data_type": data_type,
            "ingestion_type": "UPLOAD"
        }
        mock_provider_service_class.return_value = mock_provider_service
        
        # Act
        result = await self.controller._validate_provider_combination(provider_type, data_type)
        
        # Assertions
        self.assertTrue(result["valid"])
        self.assertEqual(result["provider_id"], "test_id")
        self.assertEqual(result["provider_name"], "Test Provider")
        mock_provider_service.validate_provider_type_data_type_combination.assert_called_once_with(
            provider_type, data_type
        )
    
    @patch('services.ingestion.app.controller.file_upload_controller.ProviderService')
    async def test_validate_provider_combination_failure(self, mock_provider_service_class):
        """Test failed provider combination validation"""
        # Arrange
        provider_type = "INVALID"
        data_type = "demand"
        
        mock_provider_service = Mock()
        mock_provider_service.validate_provider_type_data_type_combination.return_value = {
            "valid": False,
            "error": "No provider found with code 'INVALID' for data type 'demand'",
            "provider_type": provider_type,
            "data_type": data_type
        }
        mock_provider_service_class.return_value = mock_provider_service
        
        # Act
        result = await self.controller._validate_provider_combination(provider_type, data_type)
        
        # Assertions
        self.assertFalse(result["valid"])
        self.assertIn("No provider found", result["error"])
        self.assertEqual(result["provider_type"], provider_type)
        self.assertEqual(result["data_type"], data_type)
    
    @patch('services.ingestion.app.controller.file_upload_controller.ProviderService')
    async def test_validate_provider_combination_exception(self, mock_provider_service_class):
        """Test provider combination validation with exception"""
        # Arrange
        provider_type = "TEST_DATA"
        data_type = "demand"
        
        mock_provider_service_class.side_effect = Exception("Service error")
        
        # Act
        result = await self.controller._validate_provider_combination(provider_type, data_type)
        
        # Assertions
        self.assertFalse(result["valid"])
        self.assertIn("Provider validation error", result["error"])
        self.assertEqual(result["provider_type"], provider_type)
        self.assertEqual(result["data_type"], data_type)
    
    async def test_validate_file_success(self):
        """Test successful file validation"""
        # Arrange
        mock_file = Mock(spec=UploadFile)
        mock_file.filename = "test.csv"
        mock_file.size = 1024
        mock_file.read = AsyncMock(return_value=b"test,data\n1,2")
        mock_file.seek = AsyncMock()
        
        data_type = "demand"
        
        # Act
        result = await self.controller._validate_file(mock_file, data_type)
        
        # Assertions
        self.assertTrue(result["valid"])
        self.assertEqual(len(result["errors"]), 0)
        mock_file.read.assert_called_once()
        mock_file.seek.assert_called_once_with(0)
    
    async def test_validate_file_invalid_extension(self):
        """Test file validation with invalid file extension"""
        # Arrange
        mock_file = Mock(spec=UploadFile)
        mock_file.filename = "test.txt"
        mock_file.size = 1024
        
        data_type = "demand"
        
        # Act
        result = await self.controller._validate_file(mock_file, data_type)
        
        # Assertions
        self.assertFalse(result["valid"])
        self.assertIn("File must be a CSV file", result["errors"])
    
    async def test_validate_file_too_large(self):
        """Test file validation with file too large"""
        # Arrange
        mock_file = Mock(spec=UploadFile)
        mock_file.filename = "test.csv"
        mock_file.size = 200 * 1024 * 1024  # 200MB
        
        data_type = "demand"
        
        # Act
        result = await self.controller._validate_file(mock_file, data_type)
        
        # Assertions
        self.assertFalse(result["valid"])
        self.assertIn("File size exceeds limit", result["errors"])
    
    async def test_validate_file_invalid_data_type(self):
        """Test file validation with invalid data type"""
        # Arrange
        mock_file = Mock(spec=UploadFile)
        mock_file.filename = "test.csv"
        mock_file.size = 1024
        
        data_type = "invalid"
        
        # Act
        result = await self.controller._validate_file(mock_file, data_type)
        
        # Assertions
        self.assertFalse(result["valid"])
        self.assertIn("Data type must be 'demand' or 'supply'", result["errors"])
    
    async def test_validate_file_empty(self):
        """Test file validation with empty file"""
        # Arrange
        mock_file = Mock(spec=UploadFile)
        mock_file.filename = "test.csv"
        mock_file.size = 0
        mock_file.read = AsyncMock(return_value=b"")
        
        data_type = "demand"
        
        # Act
        result = await self.controller._validate_file(mock_file, data_type)
        
        # Assertions
        self.assertFalse(result["valid"])
        self.assertIn("File is empty", result["errors"])


if __name__ == '__main__':
    unittest.main()
