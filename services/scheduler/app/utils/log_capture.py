"""
Log Capture Utility for Job Execution
Captures and stores execution logs for display in the dashboard
"""

import logging
import io
import sys
from datetime import datetime
from typing import List, Dict, Any, Optional
from contextlib import contextmanager
import contextvars

# Context variable to hold the current log capture instance
_current_log_capture = contextvars.ContextVar('current_log_capture', default=None)

class LogCapture:
    """Utility class to capture logs during job execution"""
    
    def __init__(self):
        self.logs: List[Dict[str, Any]] = []
    
    def add_log(self, level: str, message: str, timestamp: datetime = None):
        """Add a log entry manually"""
        if timestamp is None:
            timestamp = datetime.now()
        
        self.logs.append({
            "timestamp": timestamp.isoformat(),
            "level": level,
            "message": str(message)
        })
    
    def get_logs(self) -> List[Dict[str, Any]]:
        """Get all captured logs"""
        return self.logs.copy()
    
    def clear_logs(self):
        """Clear all captured logs"""
        self.logs.clear()
    
    @contextmanager
    def capture_execution_logs(self):
        """
        Context manager to capture logs during job execution using contextvars.
        This does NOT redirect sys.stdout/stderr globally.
        It relies on the ContextAwareLogCaptureHandler being attached to the logger.
        """
        token = _current_log_capture.set(self)
        try:
            yield self
        finally:
            _current_log_capture.reset(token)

class ContextAwareLogCaptureHandler(logging.Handler):
    """
    Custom logging handler that captures logs to the current ContextVar LogCapture.
    This allows thread-safe/async-safe capturing of logs for specific jobs.
    """
    
    def emit(self, record):
        """Emit a log record by adding it to the current log capture if available"""
        try:
            # Get the current log capture from context
            log_capture = _current_log_capture.get()
            
            if log_capture:
                level = record.levelname
                message = self.format(record)
                timestamp = datetime.fromtimestamp(record.created)
                log_capture.add_log(level, message, timestamp)
        except Exception:
            # Don't let logging errors break the job execution
            pass

# Create a global instance of the handler
# This should be attached to the root logger (or specific loggers) at application startup
context_aware_handler = ContextAwareLogCaptureHandler()
