"""
Global Error Handler Middleware
Provides consistent error handling across the application
"""

import logging
from typing import Dict, Any
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..exceptions.custom_exceptions import (
    SchedulerServiceException,
    JobNotFoundException,
    JobExecutionException,
    RepositoryException,
    ValidationException,
    ServiceUnavailableException
)


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """Middleware to handle exceptions globally"""
    
    def __init__(self, app):
        super().__init__(app)
        self.logger = logging.getLogger(__name__)
    
    async def dispatch(self, request: Request, call_next):
        """Handle requests and catch exceptions"""
        try:
            response = await call_next(request)
            return response
        except Exception as exc:
            return await self._handle_exception(request, exc)
    
    async def _handle_exception(self, request: Request, exc: Exception) -> JSONResponse:
        """Handle different types of exceptions"""
        
        # Log the exception
        self.logger.error(
            f"Exception in {request.method} {request.url}: {str(exc)}",
            exc_info=True
        )
        
        # Handle custom exceptions
        if isinstance(exc, JobNotFoundException):
            return self._create_error_response(
                status_code=404,
                error_code=exc.error_code,
                message=exc.message,
                details=exc.details
            )
        
        elif isinstance(exc, ValidationException):
            return self._create_error_response(
                status_code=400,
                error_code=exc.error_code,
                message=exc.message,
                details=exc.details
            )
        
        elif isinstance(exc, ServiceUnavailableException):
            return self._create_error_response(
                status_code=503,
                error_code=exc.error_code,
                message=exc.message,
                details=exc.details
            )
        
        elif isinstance(exc, (JobExecutionException, RepositoryException)):
            return self._create_error_response(
                status_code=500,
                error_code=exc.error_code,
                message=exc.message,
                details=exc.details
            )
        
        elif isinstance(exc, SchedulerServiceException):
            return self._create_error_response(
                status_code=500,
                error_code=exc.error_code,
                message=exc.message,
                details=exc.details
            )
        
        # Handle FastAPI HTTPException
        elif isinstance(exc, HTTPException):
            return self._create_error_response(
                status_code=exc.status_code,
                error_code="HTTP_ERROR",
                message=exc.detail,
                details={}
            )
        
        # Handle unexpected exceptions
        else:
            return self._create_error_response(
                status_code=500,
                error_code="INTERNAL_ERROR",
                message="An unexpected error occurred",
                details={"exception_type": type(exc).__name__}
            )
    
    def _create_error_response(
        self, 
        status_code: int, 
        error_code: str, 
        message: str, 
        details: Dict[str, Any]
    ) -> JSONResponse:
        """Create a standardized error response"""
        
        error_response = {
            "error": {
                "code": error_code,
                "message": message,
                "details": details
            },
            "status": "error",
            "timestamp": self._get_current_timestamp()
        }
        
        return JSONResponse(
            status_code=status_code,
            content=error_response
        )
    
    def _get_current_timestamp(self) -> str:
        """Get current timestamp in ISO format"""
        from datetime import datetime
        return datetime.now().isoformat()