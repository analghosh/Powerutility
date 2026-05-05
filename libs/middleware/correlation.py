"""
Correlation ID middleware for request tracking
"""

import logging
from contextvars import ContextVar
from typing import Callable, Optional
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from .utils import generate_correlation_id

logger = logging.getLogger(__name__)

# Context variable to store correlation ID throughout request lifecycle
_correlation_id: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)


class CorrelationMiddleware(BaseHTTPMiddleware):
    """
    Middleware to inject and manage correlation IDs for request tracking
    """
    
    def __init__(self, app, header_name: str = "X-Correlation-ID"):
        super().__init__(app)
        self.header_name = header_name
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Process request and inject correlation ID
        
        Args:
            request: FastAPI request object
            call_next: Next middleware/endpoint in chain
            
        Returns:
            Response with correlation ID header
        """
        # Get correlation ID from request header or generate new one
        correlation_id = request.headers.get(self.header_name)
        
        if not correlation_id:
            correlation_id = generate_correlation_id()
            logger.debug(f"Generated new correlation ID: {correlation_id}")
        else:
            logger.debug(f"Using existing correlation ID: {correlation_id}")
        
        # Set correlation ID in context for use throughout request
        _correlation_id.set(correlation_id)
        
        # Add correlation ID to request state for easy access
        request.state.correlation_id = correlation_id
        
        try:
            # Process request
            response = await call_next(request)
            
            # Add correlation ID to response headers
            response.headers[self.header_name] = correlation_id
            
            return response
            
        except Exception as e:
            logger.error(f"Error in correlation middleware: {str(e)} | CorrelationID: {correlation_id}")
            raise
        finally:
            # Clean up context
            _correlation_id.set(None)


def get_correlation_id() -> Optional[str]:
    """
    Get current correlation ID from context
    
    Returns:
        Current correlation ID or None if not set
    """
    return _correlation_id.get()


def set_correlation_id(correlation_id: str) -> None:
    """
    Set correlation ID in context
    
    Args:
        correlation_id: Correlation ID to set
    """
    _correlation_id.set(correlation_id)
