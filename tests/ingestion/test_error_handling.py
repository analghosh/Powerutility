"""
Unit tests for error handling system
"""

import pytest
from datetime import datetime
from fastapi import HTTPException, status
from fastapi.responses import JSONResponse

from services.ingestion.app.core.error_handling import (
    ProviderError,
    DataValidationError,
    ConfigurationError,
    DataSourceError,
    ProcessingError,
    ProviderResponseFormatter,
    ErrorHandler,
    FastAPIErrorHandler
)


class TestProviderError:
    """Test ProviderError base class"""
    
    def test_provider_error_creation(self):
        """Test basic ProviderError creation"""
        error = ProviderError("Test error", "test_provider", "TEST_ERROR")
        
        assert str(error) == "Test error"
        assert error.message == "Test error"
        assert error.provider_type == "test_provider"
        assert error.error_code == "TEST_ERROR"
    
    def test_provider_error_without_optional_params(self):
        """Test ProviderError with minimal parameters"""
        error = ProviderError("Test error")
        
        assert str(error) == "Test error"
        assert error.message == "Test error"
        assert error.provider_type == ""
        assert error.error_code == ""


class TestDataValidationError:
    """Test DataValidationError class"""
    
    def test_validation_error_creation(self):
        """Test DataValidationError creation"""
        validation_errors = ["Field 'name' is required", "Field 'age' must be numeric"]
        error = DataValidationError(
            "Validation failed", 
            validation_errors, 
            "test_provider"
        )
        
        assert error.message == "Validation failed"
        assert error.provider_type == "test_provider"
        assert error.error_code == "VALIDATION_ERROR"
        assert error.validation_errors == validation_errors
    
    def test_validation_error_without_validation_errors(self):
        """Test DataValidationError without validation errors"""
        error = DataValidationError("Validation failed", provider_type="test_provider")
        
        assert error.validation_errors == []


class TestConfigurationError:
    """Test ConfigurationError class"""
    
    def test_configuration_error_creation(self):
        """Test ConfigurationError creation"""
        error = ConfigurationError(
            "Invalid configuration", 
            "database_url", 
            "test_provider"
        )
        
        assert error.message == "Invalid configuration"
        assert error.provider_type == "test_provider"
        assert error.error_code == "CONFIG_ERROR"
        assert error.config_field == "database_url"


class TestDataSourceError:
    """Test DataSourceError class"""
    
    def test_data_source_error_creation(self):
        """Test DataSourceError creation"""
        error = DataSourceError(
            "Connection failed", 
            "api_endpoint", 
            "test_provider"
        )
        
        assert error.message == "Connection failed"
        assert error.provider_type == "test_provider"
        assert error.error_code == "SOURCE_ERROR"
        assert error.source_type == "api_endpoint"


class TestProcessingError:
    """Test ProcessingError class"""
    
    def test_processing_error_creation(self):
        """Test ProcessingError creation"""
        error = ProcessingError(
            "Processing failed", 
            "data_transformation", 
            "test_provider"
        )
        
        assert error.message == "Processing failed"
        assert error.provider_type == "test_provider"
        assert error.error_code == "PROCESSING_ERROR"
        assert error.processing_stage == "data_transformation"


class TestProviderResponseFormatter:
    """Test ProviderResponseFormatter class"""
    
    def test_success_response_basic(self):
        """Test basic success response formatting"""
        data = {"id": 123, "name": "test"}
        response = ProviderResponseFormatter.success_response(data)
        
        assert response["success"] is True
        assert response["message"] == "Operation successful"
        assert response["data"] == data
        assert "timestamp" in response
        assert "provider" not in response
    
    def test_success_response_with_provider_info(self):
        """Test success response with provider info"""
        data = {"id": 123}
        provider_info = {"provider_type": "test_data", "version": "1.0"}
        response = ProviderResponseFormatter.success_response(
            data, 
            "Custom message", 
            provider_info
        )
        
        assert response["success"] is True
        assert response["message"] == "Custom message"
        assert response["data"] == data
        assert response["provider"] == provider_info
    
    def test_error_response_with_provider_error(self):
        """Test error response with ProviderError"""
        error = DataValidationError(
            "Validation failed",
            ["Field 'name' is required"],
            "test_provider"
        )
        response = ProviderResponseFormatter.error_response(error)
        
        assert response["success"] is False
        assert response["message"] == "Operation failed"
        assert response["error"]["type"] == "DataValidationError"
        assert response["error"]["message"] == "Validation failed"
        assert response["error"]["provider_type"] == "test_provider"
        assert response["error"]["error_code"] == "VALIDATION_ERROR"
        assert response["error"]["validation_errors"] == ["Field 'name' is required"]
    
    def test_error_response_with_generic_exception(self):
        """Test error response with generic exception"""
        error = ValueError("Generic error")
        response = ProviderResponseFormatter.error_response(error)
        
        assert response["success"] is False
        assert response["error"]["type"] == "ValueError"
        assert response["error"]["message"] == "Generic error"
        assert response["error"]["provider_type"] == "unknown"
        assert response["error"]["error_code"] == "UNKNOWN_ERROR"
    
    def test_validation_error_response(self):
        """Test validation error response formatting"""
        validation_errors = ["Field 'name' is required", "Field 'age' must be numeric"]
        response = ProviderResponseFormatter.validation_error_response(
            validation_errors,
            "Custom validation message"
        )
        
        assert response["success"] is False
        assert response["message"] == "Custom validation message"
        assert response["error"]["type"] == "DataValidationError"
        assert response["error"]["message"] == "Data validation failed"
        assert response["error"]["validation_errors"] == validation_errors
        assert response["error"]["error_code"] == "VALIDATION_ERROR"


class TestErrorHandler:
    """Test ErrorHandler class"""
    
    def test_handle_provider_error_with_provider_error(self, mocker):
        """Test handling ProviderError"""
        mock_logger = mocker.patch('services.ingestion.app.core.error_handling.logger')
        
        error = DataValidationError(
            "Validation failed",
            ["Field 'name' is required"],
            "test_provider"
        )
        
        response = ErrorHandler.handle_provider_error(
            "test_operation",
            error,
            "test_provider"
        )
        
        assert response["success"] is False
        assert response["message"] == "test_operation failed"
        assert response["error"]["type"] == "DataValidationError"
        assert response["provider"]["provider_type"] == "test_provider"
        mock_logger.error.assert_called_once()
    
    def test_handle_provider_error_with_generic_exception(self, mocker):
        """Test handling generic exception"""
        mock_logger = mocker.patch('services.ingestion.app.core.error_handling.logger')
        
        error = ValueError("Generic error")
        
        response = ErrorHandler.handle_provider_error(
            "test_operation",
            error,
            "test_provider"
        )
        
        assert response["success"] is False
        assert response["error"]["type"] == "ProviderError"
        assert response["error"]["message"] == "Generic error"
        assert response["error"]["provider_type"] == "test_provider"
        assert response["error"]["error_code"] == "UNKNOWN_ERROR"
        mock_logger.error.assert_called_once()
    
    def test_handle_validation_error(self, mocker):
        """Test handling validation error"""
        mock_logger = mocker.patch('services.ingestion.app.core.error_handling.logger')
        
        validation_errors = ["Field 'name' is required"]
        
        response = ErrorHandler.handle_validation_error(
            "test_operation",
            validation_errors,
            "test_provider"
        )
        
        assert response["success"] is False
        assert response["message"] == "test_operation validation failed"
        assert response["error"]["validation_errors"] == validation_errors
        assert response["provider"]["provider_type"] == "test_provider"
        mock_logger.warning.assert_called_once()
    
    def test_handle_configuration_error(self, mocker):
        """Test handling configuration error"""
        mock_logger = mocker.patch('services.ingestion.app.core.error_handling.logger')
        
        response = ErrorHandler.handle_configuration_error(
            "test_operation",
            "database_url",
            "Invalid database URL",
            "test_provider"
        )
        
        assert response["success"] is False
        assert response["message"] == "test_operation configuration error"
        assert response["error"]["type"] == "ConfigurationError"
        assert response["error"]["config_field"] == "database_url"
        assert response["provider"]["provider_type"] == "test_provider"
        mock_logger.error.assert_called_once()


class TestFastAPIErrorHandler:
    """Test FastAPIErrorHandler class"""
    
    def test_handle_http_exception_success_response(self):
        """Test handling success response"""
        success_response = {
            "success": True,
            "message": "Operation successful",
            "data": {"id": 123}
        }
        
        # This should work with success response, but return a 400 status
        response = FastAPIErrorHandler.handle_http_exception(success_response)
        
        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
    
    def test_handle_http_exception_validation_error(self):
        """Test handling validation error response"""
        error_response = {
            "success": False,
            "error": {
                "type": "DataValidationError",
                "message": "Validation failed"
            }
        }
        
        response = FastAPIErrorHandler.handle_http_exception(error_response)
        
        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
    
    def test_handle_http_exception_configuration_error(self):
        """Test handling configuration error response"""
        error_response = {
            "success": False,
            "error": {
                "type": "ConfigurationError",
                "message": "Configuration failed"
            }
        }
        
        response = FastAPIErrorHandler.handle_http_exception(error_response)
        
        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    
    def test_handle_http_exception_data_source_error(self):
        """Test handling data source error response"""
        error_response = {
            "success": False,
            "error": {
                "type": "DataSourceError",
                "message": "Data source failed"
            }
        }
        
        response = FastAPIErrorHandler.handle_http_exception(error_response)
        
        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    
    def test_handle_http_exception_processing_error(self):
        """Test handling processing error response"""
        error_response = {
            "success": False,
            "error": {
                "type": "ProcessingError",
                "message": "Processing failed"
            }
        }
        
        response = FastAPIErrorHandler.handle_http_exception(error_response)
        
        assert isinstance(response, JSONResponse)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    
    def test_create_http_exception_from_error_validation(self):
        """Test creating HTTPException from validation error"""
        error_response = {
            "success": False,
            "error": {
                "type": "DataValidationError",
                "message": "Validation failed"
            }
        }
        
        exception = FastAPIErrorHandler.create_http_exception_from_error(error_response)
        
        assert isinstance(exception, HTTPException)
        assert exception.status_code == status.HTTP_400_BAD_REQUEST
        assert exception.detail == error_response
    
    def test_create_http_exception_from_error_configuration(self):
        """Test creating HTTPException from configuration error"""
        error_response = {
            "success": False,
            "error": {
                "type": "ConfigurationError",
                "message": "Configuration failed"
            }
        }
        
        exception = FastAPIErrorHandler.create_http_exception_from_error(error_response)
        
        assert isinstance(exception, HTTPException)
        assert exception.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR