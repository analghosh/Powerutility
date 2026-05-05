"""
Logging middleware with correlation ID support for request tracing
"""

import logging
import time
import json
import sys
import os
from typing import Callable, Optional, List
from fastapi import Request, Response, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from .correlation import get_correlation_id

logger = logging.getLogger(__name__)


class Colors:
    """ANSI color codes for console output"""
    # Reset
    RESET = '\033[0m'
    
    # Regular colors
    BLACK = '\033[30m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'
    
    # Bright colors
    BRIGHT_BLACK = '\033[90m'
    BRIGHT_RED = '\033[91m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_MAGENTA = '\033[95m'
    BRIGHT_CYAN = '\033[96m'
    BRIGHT_WHITE = '\033[97m'
    
    # Background colors
    BG_RED = '\033[41m'
    BG_GREEN = '\033[42m'
    BG_YELLOW = '\033[43m'
    BG_BLUE = '\033[44m'
    
    # Styles
    BOLD = '\033[1m'
    DIM = '\033[2m'
    UNDERLINE = '\033[4m'
    
    @classmethod
    def is_supported(cls) -> bool:
        """Check if terminal supports colors"""
        # Check if stdout is a TTY
        if not (hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()):
            return False
        
        # For Windows, check for modern terminal support
        if sys.platform == 'win32':
            return (
                'ANSICON' in os.environ or 
                'WT_SESSION' in os.environ or  # Windows Terminal
                'TERM_PROGRAM' in os.environ or  # VS Code, etc.
                'COLORTERM' in os.environ or  # Generic color terminal
                os.environ.get('TERM', '').startswith('xterm')  # xterm-like terminals
            )
        
        # For Unix-like systems, assume color support if it's a TTY
        return True


def colorize(text: str, color: str, bold: bool = False) -> str:
    """Add color to text if terminal supports it"""
    if not Colors.is_supported():
        return text
    
    style = Colors.BOLD if bold else ''
    return f"{style}{color}{text}{Colors.RESET}"


def get_method_color(method: str) -> str:
    """Get color for HTTP method"""
    method_colors = {
        'GET': Colors.BRIGHT_GREEN,
        'POST': Colors.BRIGHT_BLUE,
        'PUT': Colors.BRIGHT_YELLOW,
        'PATCH': Colors.BRIGHT_MAGENTA,
        'DELETE': Colors.BRIGHT_RED,
        'HEAD': Colors.BRIGHT_CYAN,
        'OPTIONS': Colors.BRIGHT_WHITE
    }
    return method_colors.get(method, Colors.WHITE)


def get_status_color(status_code: int) -> str:
    """Get color for HTTP status code"""
    if status_code < 300:
        return Colors.BRIGHT_GREEN
    elif status_code < 400:
        return Colors.BRIGHT_CYAN
    elif status_code < 500:
        return Colors.BRIGHT_YELLOW
    else:
        return Colors.BRIGHT_RED


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware for comprehensive request/response logging with correlation ID support
    """
    
    def __init__(
        self,
        app: ASGIApp,
        log_requests: bool = True,
        log_responses: bool = True,
        log_body: bool = False,
        exclude_paths: Optional[List[str]] = None,
        max_body_size: int = 1000
    ):
        super().__init__(app)
        self.log_requests = log_requests
        self.log_responses = log_responses
        self.log_body = log_body
        self.max_body_size = max_body_size
        self.exclude_paths = exclude_paths or [
            "/health",
            "/docs",
            "/redoc", 
            "/openapi.json",
            "/favicon.ico",
            "/static"
        ]
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request with comprehensive logging"""
        
        # Skip logging for excluded paths
        if any(request.url.path.startswith(path) for path in self.exclude_paths):
            return await call_next(request)
        
        # Get correlation ID (should be set by CorrelationMiddleware)
        correlation_id = get_correlation_id() or getattr(request.state, 'correlation_id', 'no-correlation-id')
        
        # Start timing
        start_time = time.time()
        
        # Log incoming request
        if self.log_requests:
            await self._log_request(request, correlation_id)
        
        # Process request
        try:
            from fastapi import HTTPException
            response = await call_next(request)
            
            # Calculate processing time
            process_time = time.time() - start_time
            
            # Add process time to response headers
            response.headers["X-Process-Time"] = f"{process_time:.4f}"
            
            # Log response
            if self.log_responses:
                await self._log_response(request, response, correlation_id, process_time)
            
            return response
            
        except HTTPException as e:
            # This is a controlled error (like 401, 404), not a server crash.
            # Log it appropriately but let FastAPI handle the response generation.
            process_time = time.time() - start_time
            self._log_http_exception(request, e, correlation_id, process_time)
            raise  # Re-raise for FastAPI to handle
            
        except Exception as e:
            process_time = time.time() - start_time
            
            # Log error with full context
            self._log_error(request, e, correlation_id, process_time)
            
            # Re-raise the exception
            raise
    
    async def _log_request(self, request: Request, correlation_id: str):
        """Log incoming request details"""
        
        # Get client information
        client_ip = self._get_client_ip(request)
        user_agent = request.headers.get("User-Agent", "Unknown")
        
        # Get user context if available
        user_info = self._get_user_info(request)
        
        # Prepare base log data
        log_data = {
            "correlation_id": correlation_id,
            "method": request.method,
            "url": str(request.url),
            "path": request.url.path,
            "query_params": dict(request.query_params),
            "client_ip": client_ip,
            "user_agent": user_agent,
            **user_info
        }
        
        # Add headers (filtered for security)
        if self.log_body:
            log_data["headers"] = self._filter_headers(dict(request.headers))
        
        # Log request body for POST/PUT/PATCH (if enabled and body is small)
        if self.log_body and request.method in ["POST", "PUT", "PATCH"]:
            try:
                body = await request.body()
                if body and len(body) <= self.max_body_size:  # Only log small bodies
                    log_data["body"] = self._parse_body(body)
                elif body:
                    log_data["body"] = f"<body too large: {len(body)} bytes>"
            except Exception as e:
                log_data["body_error"] = str(e)
        
        # Create colored log message
        method_colored = colorize(request.method, get_method_color(request.method), bold=True)
        path_colored = colorize(request.url.path, Colors.BRIGHT_WHITE)
        arrow_colored = colorize("→", Colors.BRIGHT_CYAN, bold=True)
        
        message = f"{arrow_colored} {method_colored} {path_colored}"
        
        # Add user info if available
        if user_info.get("username"):
            user_colored = colorize(f"[{user_info['username']}]", Colors.BRIGHT_MAGENTA)
            message += f" {user_colored}"
        
        # Add client IP
        ip_colored = colorize(f"({client_ip})", Colors.DIM)
        message += f" {ip_colored}"
        
        # Log with appropriate level
        logger.info(message, extra=log_data)
    
    async def _log_response(
        self, 
        request: Request, 
        response: Response, 
        correlation_id: str, 
        process_time: float
    ):
        """Log response details"""
        
        # Get user context if available
        user_info = self._get_user_info(request)
        
        log_data = {
            "correlation_id": correlation_id,
            "method": request.method,
            "url": str(request.url),
            "path": request.url.path,
            "status_code": response.status_code,
            "process_time": f"{process_time:.4f}s",
            **user_info
        }
        
        # Add response headers (filtered)
        if self.log_body:
            log_data["response_headers"] = self._filter_headers(dict(response.headers))
        
        # Determine log level and create colored message based on status code
        method_colored = colorize(request.method, get_method_color(request.method), bold=True)
        path_colored = colorize(request.url.path, Colors.BRIGHT_WHITE)
        status_colored = colorize(str(response.status_code), get_status_color(response.status_code), bold=True)
        time_colored = colorize(f"({process_time:.3f}s)", Colors.DIM)
        
        if response.status_code >= 500:
            log_level = logging.ERROR
            symbol = colorize("✗", Colors.BRIGHT_RED, bold=True)
        elif response.status_code >= 400:
            log_level = logging.WARNING
            symbol = colorize("⚠", Colors.BRIGHT_YELLOW, bold=True)
        else:
            log_level = logging.INFO
            symbol = colorize("✓", Colors.BRIGHT_GREEN, bold=True)
        
        message = f"{symbol} {method_colored} {path_colored} → {status_colored} {time_colored}"
        
        # Add user info if available
        if user_info.get("username"):
            user_colored = colorize(f"[{user_info['username']}]", Colors.BRIGHT_MAGENTA)
            message += f" {user_colored}"
        
        logger.log(log_level, message, extra=log_data)
    
    def _log_http_exception(
        self,
        request: Request,
        exc: "HTTPException",
        correlation_id: str,
        process_time: float
    ):
        """Log controlled HTTP exceptions (e.g., 4xx errors)."""
        user_info = self._get_user_info(request)
        
        log_data = {
            "correlation_id": correlation_id,
            "method": request.method,
            "url": str(request.url),
            "path": request.url.path,
            "status_code": exc.status_code,
            "process_time": f"{process_time:.4f}s",
            "error": exc.detail,
            **user_info
        }

        log_level = logging.WARNING if exc.status_code < 500 else logging.ERROR
        symbol = colorize("⚠", Colors.BRIGHT_YELLOW, bold=True) if exc.status_code < 500 else colorize("✗", Colors.BRIGHT_RED, bold=True)

        method_colored = colorize(request.method, get_method_color(request.method), bold=True)
        path_colored = colorize(request.url.path, Colors.BRIGHT_WHITE)
        status_colored = colorize(str(exc.status_code), get_status_color(exc.status_code), bold=True)
        time_colored = colorize(f"({process_time:.3f}s)", Colors.DIM)
        detail_colored = colorize(exc.detail, Colors.BRIGHT_YELLOW)

        message = f"{symbol} {method_colored} {path_colored} → {status_colored} {time_colored}: {detail_colored}"
        
        # exc_info=False because this is a controlled exception, not a traceback-worthy error
        logger.log(log_level, message, extra=log_data, exc_info=False)


    def _log_error(
        self, 
        request: Request, 
        error: Exception, 
        correlation_id: str, 
        process_time: float
    ):
        """Log error details"""
        
        user_info = self._get_user_info(request)
        
        log_data = {
            "correlation_id": correlation_id,
            "method": request.method,
            "url": str(request.url),
            "path": request.url.path,
            "process_time": f"{process_time:.4f}s",
            "error": str(error),
            "error_type": type(error).__name__,
            **user_info
        }
        
        # Create colored error message
        method_colored = colorize(request.method, get_method_color(request.method), bold=True)
        path_colored = colorize(request.url.path, Colors.BRIGHT_WHITE)
        error_colored = colorize("ERROR", Colors.BRIGHT_RED, bold=True)
        time_colored = colorize(f"({process_time:.3f}s)", Colors.DIM)
        explosion_colored = colorize("💥", Colors.BRIGHT_RED, bold=True)
        error_msg_colored = colorize(str(error), Colors.BRIGHT_RED)
        
        message = f"{explosion_colored} {method_colored} {path_colored} → {error_colored} {time_colored}: {error_msg_colored}"
        
        # Add user info if available
        if user_info.get("username"):
            user_colored = colorize(f"[{user_info['username']}]", Colors.BRIGHT_MAGENTA)
            message += f" {user_colored}"
        
        logger.error(message, extra=log_data, exc_info=True)
    
    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP address"""
        # Check for forwarded headers first
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip
        
        # Fallback to client host
        if hasattr(request, "client") and request.client:
            return request.client.host
        
        return "unknown"
    
    def _get_user_info(self, request: Request) -> dict:
        """Extract user information from request state"""
        user_info = {}
        
        # Try to get user from request state (set by auth middleware)
        if hasattr(request.state, 'user'):
            user = request.state.user
            if isinstance(user, dict):
                user_info.update({
                    "user_id": user.get("user_id"),
                    "username": user.get("username"),
                    "roles": user.get("roles", [])
                })
        
        return user_info
    
    def _filter_headers(self, headers: dict) -> dict:
        """Filter sensitive headers from logging"""
        sensitive_headers = {
            "authorization", "cookie", "x-api-key", "x-auth-token"
        }
        
        filtered = {}
        for key, value in headers.items():
            if key.lower() in sensitive_headers:
                filtered[key] = "[REDACTED]"
            else:
                filtered[key] = value
        
        return filtered
    
    def _parse_body(self, body: bytes) -> any:
        """Parse request body for logging"""
        try:
            # Try to parse as JSON
            decoded = body.decode('utf-8')
            return json.loads(decoded)
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Fallback to truncated string
            try:
                decoded = body.decode('utf-8', errors='ignore')
                return decoded[:self.max_body_size] + ("..." if len(decoded) > self.max_body_size else "")
            except Exception:
                return f"<binary data: {len(body)} bytes>"


class CorrelationIDFilter(logging.Filter):
    """
    Logging filter to add correlation ID to all log records
    """
    
    def filter(self, record):
        """Add correlation ID to log record"""
        # Get correlation ID from context or record
        correlation_id = getattr(record, 'correlation_id', None)
        
        if not correlation_id:
            correlation_id = get_correlation_id()
        
        # Set correlation ID (or default)
        record.correlation_id = correlation_id or "no-correlation-id"
        
        return True


def setup_logging_middleware(app, config: Optional[dict] = None):
    """
    Setup logging middleware with configuration
    
    Args:
        app: FastAPI application
        config: Optional configuration dict
        
    Returns:
        Configured LoggingMiddleware instance
    """
    config = config or {}
    
    middleware = LoggingMiddleware(
        app=app,
        log_requests=config.get("log_requests", True),
        log_responses=config.get("log_responses", True),
        log_body=config.get("log_body", False),
        exclude_paths=config.get("exclude_paths", None),
        max_body_size=config.get("max_body_size", 1000)
    )
    
    return middleware


class ColoredFormatter(logging.Formatter):
    """Custom formatter with color support"""
    
    def __init__(self, fmt=None, datefmt=None, use_colors=True):
        super().__init__(fmt, datefmt)
        self.use_colors = use_colors and Colors.is_supported()
    
    def format(self, record):
        if not self.use_colors:
            return super().format(record)
        
        # Color the log level
        level_colors = {
            'DEBUG': Colors.BRIGHT_BLACK,
            'INFO': Colors.BRIGHT_BLUE,
            'WARNING': Colors.BRIGHT_YELLOW,
            'ERROR': Colors.BRIGHT_RED,
            'CRITICAL': Colors.BG_RED + Colors.BRIGHT_WHITE
        }
        
        original_levelname = record.levelname
        if record.levelname in level_colors:
            record.levelname = colorize(record.levelname, level_colors[record.levelname], bold=True)
        
        # Color the logger name
        original_name = record.name
        if hasattr(record, 'name'):
            record.name = colorize(record.name, Colors.BRIGHT_CYAN)
        
        # Color the correlation ID
        original_correlation_id = getattr(record, 'correlation_id', 'no-correlation-id')
        if hasattr(record, 'correlation_id'):
            record.correlation_id = colorize(f"[{original_correlation_id}]", Colors.BRIGHT_MAGENTA)
        
        # Format the message
        formatted = super().format(record)
        
        # Restore original values
        record.levelname = original_levelname
        record.name = original_name
        if hasattr(record, 'correlation_id'):
            record.correlation_id = original_correlation_id
        
        return formatted


def configure_logging(
    level: str = "INFO",
    format_type: str = "text",
    include_correlation: bool = True,
    use_colors: bool = True
):
    """
    Configure application logging with correlation ID support and colors
    
    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR)
        format_type: Format type ("json", "text", or "colored")
        include_correlation: Whether to include correlation ID filter
        use_colors: Whether to use colored output (auto-detected if terminal supports it)
    """
    
    # Auto-detect color support
    use_colors = use_colors and Colors.is_supported()
    
    # Create formatter based on type
    if format_type.lower() == "json":
        formatter = logging.Formatter(
            '{"timestamp": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", '
            '"correlation_id": "%(correlation_id)s", "message": "%(message)s"}'
        )
    elif format_type.lower() == "colored" or (format_type.lower() == "text" and use_colors):
        formatter = ColoredFormatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(correlation_id)s - %(message)s',
            use_colors=use_colors
        )
    else:
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - [%(correlation_id)s] - %(message)s'
        )
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))
    
    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Create console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    # Add correlation ID filter if requested
    if include_correlation:
        console_handler.addFilter(CorrelationIDFilter())
    
    root_logger.addHandler(console_handler)
    
    # Configure specific loggers
    logging.getLogger("uvicorn.access").disabled = True  # Disable uvicorn access logs (we handle this)
    logging.getLogger("fastapi").setLevel(logging.WARNING)
    
    # Log configuration info
    if use_colors:
        config_msg = colorize("✨ Colored logging enabled", Colors.BRIGHT_GREEN, bold=True)
    else:
        config_msg = "Logging configured (colors disabled)"
    
    root_logger.info(config_msg)
    
    return root_logger
