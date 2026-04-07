"""
Error handling system for raw data providers
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from fastapi import HTTPException, status
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class ProviderError(Exception):
    """Base exception for provider errors"""
    
    def __init__(self, message: str, provider_type: str = "", error_code: str = ""):
        self.message = message
        self.provider_type = provider_type
        self.error_code = error_code
        super().__init__(self.message)


class DataValidationError(ProviderError):
    """Data validation error"""
    
    def __init__(self, message: str, validation_errors: Optional[List[str]] = None, provider_type: str = ""):
        self.validation_errors = validation_errors or []
        super().__init__(message, provider_type, "VALIDATION_ERROR")


class ConfigurationError(ProviderError):
    """Configuration error"""
    
    def __init__(self, message: str, config_field: str = "", provider_type: str = ""):
        self.config_field = config_field
        super().__init__(message, provider_type, "CONFIG_ERROR")


class DataSourceError(ProviderError):
    """Data source error"""
    
    def __init__(self, message: str, source_type: str = "", provider_type: str = ""):
        self.source_type = source_type
        super().__init__(message, provider_type, "SOURCE_ERROR")


class ProcessingError(ProviderError):
    """Data processing error"""
    
    def __init__(self, message: str, processing_stage: str = "", provider_type: str = ""):
        self.processing_stage = processing_stage
        super().__init__(message, provider_type, "PROCESSING_ERROR")


class ProviderResponseFormatter:
    """Standardized response formatting for all providers"""
    
    @staticmethod
    def success_response(data: Any, message: str = "Operation successful", provider_info: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Format successful response"""
        response = {
            "success": True,
            "message": message,
            "data": data,
            "timestamp": datetime.now().isoformat()
        }
        
        if provider_info:
            response["provider"] = provider_info
        
        return response
    
    @staticmethod
    def error_response(error: Exception, message: str = "Operation failed", provider_info: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Format error response"""
        response = {
            "success": False,
            "message": message,
            "error": {
                "type": error.__class__.__name__,
                "message": str(error),
                "provider_type": getattr(error, 'provider_type', 'unknown'),
                "error_code": getattr(error, 'error_code', 'UNKNOWN_ERROR')
            },
            "timestamp": datetime.now().isoformat()
        }
        
        # Add specific error details based on error type
        if isinstance(error, DataValidationError):
            response["error"]["validation_errors"] = error.validation_errors
        elif isinstance(error, ConfigurationError):
            response["error"]["config_field"] = error.config_field
        elif isinstance(error, DataSourceError):
            response["error"]["source_type"] = error.source_type
        elif isinstance(error, ProcessingError):
            response["error"]["processing_stage"] = error.processing_stage
        
        if provider_info:
            response["provider"] = provider_info
        
        return response
    
    @staticmethod
    def validation_error_response(validation_errors: List[str], message: str = "Validation failed", provider_info: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Format validation error response"""
        response = {
            "success": False,
            "message": message,
            "error": {
                "type": "DataValidationError",
                "message": "Data validation failed",
                "validation_errors": validation_errors,
                "error_code": "VALIDATION_ERROR"
            },
            "timestamp": datetime.now().isoformat()
        }
        
        if provider_info:
            response["provider"] = provider_info
        
        return response


class ErrorHandler:
    """Centralized error handling for providers"""
    
    @staticmethod
    def handle_provider_error(operation: str, error: Exception, provider_type: str = "") -> Dict[str, Any]:
        """Handle provider errors with appropriate logging and response"""
        logger.error("Provider error in %s: %s", operation, str(error), exc_info=True)
        
        # Create error response
        if isinstance(error, ProviderError):
            return ProviderResponseFormatter.error_response(
                error, 
                f"{operation} failed", 
                {"provider_type": provider_type}
            )
        else:
            # Wrap unknown errors
            wrapped_error = ProviderError(
                message=str(error),
                provider_type=provider_type,
                error_code="UNKNOWN_ERROR"
            )
            return ProviderResponseFormatter.error_response(
                wrapped_error,
                f"{operation} failed due to unexpected error",
                {"provider_type": provider_type}
            )
    
    @staticmethod
    def handle_validation_error(operation: str, validation_errors: List[str], provider_type: str = "") -> Dict[str, Any]:
        """Handle validation errors"""
        logger.warning("Validation error in %s: %s", operation, validation_errors)
        
        return ProviderResponseFormatter.validation_error_response(
            validation_errors,
            f"{operation} validation failed",
            {"provider_type": provider_type}
        )
    
    @staticmethod
    def handle_configuration_error(operation: str, config_field: str, message: str, provider_type: str = "") -> Dict[str, Any]:
        """Handle configuration errors"""
        logger.error("Configuration error in %s: %s", operation, message)
        
        error = ConfigurationError(
            message=message,
            config_field=config_field,
            provider_type=provider_type
        )
        
        return ProviderResponseFormatter.error_response(
            error,
            f"{operation} configuration error",
            {"provider_type": provider_type}
        )


class FastAPIErrorHandler:
    """
    FastAPI-specific error handling utilities
    """
    
    @staticmethod
    def handle_http_exception(error_response: Dict[str, Any]) -> JSONResponse:
        """
        Convert error response to FastAPI JSONResponse
        """
        status_code = status.HTTP_400_BAD_REQUEST
        
        # Determine status code based on error type
        if "error" in error_response:
            error_type = error_response["error"].get("type", "")
            if error_type == "ConfigurationError":
                status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            elif error_type == "DataSourceError":
                status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            elif error_type == "ProcessingError":
                status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        
        return JSONResponse(
            status_code=status_code,
            content=error_response
        )
    
    @staticmethod
    def create_http_exception_from_error(error_response: Dict[str, Any]) -> HTTPException:
        """
        Create HTTPException from error response
        """
        status_code = status.HTTP_400_BAD_REQUEST
        
        if "error" in error_response:
            error_type = error_response["error"].get("type", "")
            if error_type == "ConfigurationError":
                status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            elif error_type == "DataSourceError":
                status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            elif error_type == "ProcessingError":
                status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        
        return HTTPException(
            status_code=status_code,
            detail=error_response
        )
