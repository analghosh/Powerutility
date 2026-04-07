"""
Audit Logger Controller
Business logic for audit log retrieval and processing
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from ..services.audit_logger_service import audit_logger_service

logger = logging.getLogger(__name__)


class AuditLoggerController:
    """Controller for audit log operations"""

    async def get_audit_logs(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get audit logs based on filters
        
        Args:
            filters: Dictionary containing filter parameters
                - type: str - Filter type ("authentication", "roles", or "data")
                - skip: int - Number of records to skip
                - limit: int - Maximum number of records to return
                
        Returns:
            Dictionary containing:
                - logs: List of audit log entries with enriched user data
                - total: Total count of matching records
        """
        try:
            logger.info(f"Fetching audit logs with filters: {filters}")
            
            # Delegate to service layer
            result = await audit_logger_service.fetch_audit_logs(filters)
            
            logger.info(f"Successfully fetched {len(result.get('logs', []))} audit logs")
            
            return result
            
        except Exception as e:
            logger.error(f"Error in audit logger controller: {e}")
            raise


# Create singleton instance
audit_logger_controller = AuditLoggerController()
