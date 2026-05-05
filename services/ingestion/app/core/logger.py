"""
Logger configuration for Ingestion Service
"""

import logging
import sys
import json
import traceback
from datetime import datetime
from typing import Optional, Dict, Any
from .config import settings


def get_ingestion_logger(name: str = "ingestion_service") -> logging.Logger:
    """Get configured logger for ingestion service"""
    
    logger = logging.getLogger(name)
    
    # Avoid adding handlers if they already exist
    if logger.handlers:
        return logger
    
    # Set log level
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    logger.setLevel(log_level)
    
    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    
    # Create formatter with enhanced information
    if settings.LOG_FORMAT.lower() == "json":
        formatter = logging.Formatter(
            '{"timestamp": "%(asctime)s", "level": "%(levelname)s", "service": "ingestion", "message": "%(message)s", "module": "%(name)s", "function": "%(funcName)s", "line": %(lineno)d, "thread": "%(threadName)s"}'
        )
    else:
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s:%(lineno)d] - %(message)s'
        )
    
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # Create file handler for errors
    try:
        # Ensure logs directory exists
        import os
        os.makedirs("logs", exist_ok=True)
        error_handler = logging.FileHandler("logs/ingestion_errors.log")
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        logger.addHandler(error_handler)
    except Exception:
        # If logs directory doesn't exist, skip file handler
        pass
    
    # Prevent propagation to root logger
    logger.propagate = False
    
    return logger


def log_ingestion_event(logger: logging.Logger, event_type: str, upload_id: str, details: Optional[Dict[str, Any]] = None):
    """
    Log ingestion-specific events with structured data
    
    Args:
        logger: Logger instance
        event_type: Type of event (upload_started, processing_completed, etc.)
        upload_id: Upload identifier
        details: Additional event details
    """
    event_data = {
        "event_type": event_type,
        "upload_id": upload_id,
        "timestamp": datetime.now().isoformat(),
        "service": "ingestion"
    }
    
    if details:
        event_data.update(details)
    
    if settings.LOG_FORMAT.lower() == "json":
        logger.info(json.dumps(event_data))
    else:
        logger.info("INGESTION_EVENT: %s - %s - %s", event_type, upload_id, details or "")


# Export the logger function
__all__ = ['get_ingestion_logger', 'log_ingestion_event'] 
