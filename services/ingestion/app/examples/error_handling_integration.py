"""
Error Handling Integration Examples
Shows how to integrate the error handling system with controllers and services
"""

import logging
from typing import Dict, Any, Optional
from fastapi import HTTPException, status
from fastapi.responses import JSONResponse

from services.ingestion.app.core.error_handling import (
    ErrorHandler,
    ProviderResponseFormatter,
    DataValidationError,
    ConfigurationError,
    DataSourceError,
    ProcessingError,
    ProviderError
)

logger = logging.getLogger(__name__)


class EnhancedFileUploadController:
    """
    Example of FileUploadController with integrated error handling
    """
    
    def __init__(self):
        self.provider_type = "test_data"
    
    async def upload_single_file_with_error_handling(
        self, 
        file, 
        data_type: str, 
        provider_type: str
    ) -> Dict[str, Any]:
        """
        Upload single file with comprehensive error handling
        """
        try:
            # Validate input parameters
            if not data_type or not provider_type:
                validation_errors = []
                if not data_type:
                    validation_errors.append("data_type is required")
                if not provider_type:
                    validation_errors.append("provider_type is required")
                
                return ErrorHandler.handle_validation_error(
                    operation="file_upload",
                    validation_errors=validation_errors,
                    provider_type=provider_type
                )
            
            # Validate file
            if not file or not file.filename:
                return ErrorHandler.handle_validation_error(
                    operation="file_upload",
                    validation_errors=["File is required and must have a filename"],
                    provider_type=provider_type
                )
            
            # Process file upload
            result = await self._process_file_upload(file, data_type, provider_type)
            
            # Return success response
            return ProviderResponseFormatter.success_response(
                data=result,
                message="File uploaded successfully",
                provider_info={"provider_type": provider_type, "data_type": data_type}
            )
            
        except DataValidationError as e:
            logger.warning(f"Validation error in file upload: {e}")
            return ErrorHandler.handle_validation_error(
                operation="file_upload",
                validation_errors=e.validation_errors,
                provider_type=provider_type
            )
            
        except ConfigurationError as e:
            logger.error(f"Configuration error in file upload: {e}")
            return ErrorHandler.handle_configuration_error(
                operation="file_upload",
                config_field=e.config_field,
                message=e.message,
                provider_type=provider_type
            )
            
        except ProcessingError as e:
            logger.error(f"Processing error in file upload: {e}")
            return ErrorHandler.handle_provider_error(
                operation="file_upload",
                error=e,
                provider_type=provider_type
            )
            
        except Exception as e:
            logger.error(f"Unexpected error in file upload: {e}", exc_info=True)
            return ErrorHandler.handle_provider_error(
                operation="file_upload",
                error=e,
                provider_type=provider_type
            )
    
    async def _process_file_upload(self, file, data_type: str, provider_type: str) -> Dict[str, Any]:
        """
        Simulate file processing with potential errors
        """
        # Simulate configuration validation
        if provider_type not in ["test_data", "scada_data", "api_data", "ftp_data"]:
            raise ConfigurationError(
                message=f"Unsupported provider type: {provider_type}",
                config_field="provider_type",
                provider_type=provider_type
            )
        
        # Simulate data validation
        if data_type not in ["demand", "supply"]:
            raise DataValidationError(
                message=f"Unsupported data type: {data_type}",
                validation_errors=[f"data_type must be 'demand' or 'supply', got '{data_type}'"],
                provider_type=provider_type
            )
        
        # Simulate file processing
        if file.size > 10 * 1024 * 1024:  # 10MB limit
            raise DataValidationError(
                message="File too large",
                validation_errors=["File size exceeds 10MB limit"],
                provider_type=provider_type
            )
        
        # Simulate processing error
        if file.filename and "error" in file.filename.lower():
            raise ProcessingError(
                message="Simulated processing error",
                processing_stage="file_validation",
                provider_type=provider_type
            )
        
        return {
            "upload_id": "12345",
            "filename": file.filename,
            "size": file.size,
            "data_type": data_type,
            "provider_type": provider_type,
            "status": "uploaded"
        }


class EnhancedRawDataService:
    """
    Example of RawDataService with integrated error handling
    """
    
    def __init__(self):
        self.provider_type = "test_data"
    
    async def process_csv_upload_with_error_handling(
        self, 
        file_content: str, 
        data_type: str, 
        provider_type: str
    ) -> Dict[str, Any]:
        """
        Process CSV upload with comprehensive error handling
        """
        try:
            # Validate input
            if not file_content or not file_content.strip():
                return ErrorHandler.handle_validation_error(
                    operation="csv_processing",
                    validation_errors=["File content is empty"],
                    provider_type=provider_type
                )
            
            # Process CSV
            result = await self._process_csv_content(file_content, data_type, provider_type)
            
            return ProviderResponseFormatter.success_response(
                data=result,
                message="CSV processed successfully",
                provider_info={"provider_type": provider_type, "data_type": data_type}
            )
            
        except DataValidationError as e:
            return ErrorHandler.handle_validation_error(
                operation="csv_processing",
                validation_errors=e.validation_errors,
                provider_type=provider_type
            )
            
        except Exception as e:
            return ErrorHandler.handle_provider_error(
                operation="csv_processing",
                error=e,
                provider_type=provider_type
            )
    
    async def _process_csv_content(self, content: str, data_type: str, provider_type: str) -> Dict[str, Any]:
        """
        Simulate CSV processing with potential errors
        """
        lines = content.strip().split('\n')
        
        if len(lines) < 2:
            raise DataValidationError(
                message="Invalid CSV format",
                validation_errors=["CSV must have at least a header and one data row"],
                provider_type=provider_type
            )
        
        # Simulate data source error
        if "invalid_source" in content.lower():
            raise DataSourceError(
                message="Invalid data source detected",
                source_type="csv_file",
                provider_type=provider_type
            )
        
        return {
            "rows_processed": len(lines) - 1,
            "columns": len(lines[0].split(',')),
            "data_type": data_type,
            "provider_type": provider_type
        }


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


# Example usage in FastAPI routes
def example_route_integration():
    """
    Example of how to use error handling in FastAPI routes
    """
    from fastapi import APIRouter, UploadFile, File, Form
    
    router = APIRouter()
    controller = EnhancedFileUploadController()
    
    @router.post("/upload")
    async def upload_file(
        file: UploadFile = File(...),
        data_type: str = Form(...),
        provider_type: str = Form(...)
    ):
        """
        Upload file endpoint with error handling
        """
        result = await controller.upload_single_file_with_error_handling(
            file=file,
            data_type=data_type,
            provider_type=provider_type
        )
        
        # If there's an error, return appropriate HTTP response
        if not result.get("success", False):
            return FastAPIErrorHandler.handle_http_exception(result)
        
        # Return success response
        return result
