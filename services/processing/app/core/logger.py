"""
Logger configuration for Processing Service
"""

import logging

def get_processing_logger(name: str = "processing_service") -> logging.Logger:
    """Get configured logger for processing service"""
    
    logger = logging.getLogger(name)
    
    # Avoid adding handlers if they already exist
    if logger.handlers:
        return logger
    
    # Set log level
    logger.setLevel(logging.INFO)
    
    # Create console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s:%(lineno)d] - %(message)s'
    )
    
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # Prevent propagation to root logger
    logger.propagate = False
    
    return logger

# Export the logger function
__all__ = ['get_processing_logger'] 