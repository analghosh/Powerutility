"""
Health service for Processing Service
"""

import os
import logging
from typing import Dict, Any
from libs.health import HealthChecker, ComponentHealth, HealthStatus
from libs.health.checks import (
    check_redis_health,
    check_mongodb_health, 
    check_database_health,
    check_application_health
)

logger = logging.getLogger(__name__)


class ProcessingHealthService:
    """Health service for Processing Service with all dependency checks"""
    
    def __init__(self):
        self.health_checker = HealthChecker()
        self._register_health_checks()
    
    def _register_health_checks(self):
        """Register all health checks for processing service"""
        
        # Application health check
        self.health_checker.register_check(
            "application",
            check_application_health,
            timeout=3.0
        )
        
        # Redis health check
        from ..core.config import settings
        redis_url = f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}"
        if settings.REDIS_PASSWORD:
            redis_url = f"redis://:{settings.REDIS_PASSWORD}@{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}"
        
        self.health_checker.register_check(
            "redis",
            lambda: check_redis_health(redis_url),
            timeout=5.0
        )
        
        # MongoDB health check
        mongo_url = settings.MONGODB_URI
        self.health_checker.register_check(
            "mongodb", 
            lambda: check_mongodb_health(mongo_url),
            timeout=5.0
        )
        
        # Database health check (if using SQL database)
        db_url = os.getenv("DATABASE_URL")
        if db_url:
            self.health_checker.register_check(
                "database",
                lambda: check_database_health(db_url),
                timeout=5.0
            )
        
        # Note: Auth service health check removed - processing service should work
        # independently. Auth validation happens per-request, not as a dependency.
    

    
    async def get_health_status(self) -> Dict[str, Any]:
        """
        Get comprehensive health status
        
        Returns:
            Dictionary with health status and component details
        """
        try:
            result = await self.health_checker.check_all()
            
            # Add service-specific information
            from ..core.config import settings
            environment = settings.ENVIRONMENT
            result["service"] = "processing-service"
            result["version"] = "1.0.0"
            result["environment"] = environment
            
            return result
            
        except Exception as e:
            logger.error(f"Health check failed: {str(e)}")
            return {
                "status": HealthStatus.UNHEALTHY,
                "message": f"Health check system error: {str(e)}",
                "service": "processing-service",
                "version": "1.0.0",
                "environment": "unknown",
                "base_path": "",
                "components": {},
                "summary": {"total_checks": 0, "healthy": 0, "unhealthy": 1}
            }
    
    async def get_simple_health(self) -> Dict[str, Any]:
        """
        Get simple health status (for basic health checks)
        
        Returns:
            Simple health response
        """
        try:
            full_health = await self.get_health_status()
            
            return {
                "status": full_health["status"],
                "service": full_health["service"],
                "version": full_health["version"],
                "environment": full_health.get("environment", "unknown"),
                "base_path": full_health.get("base_path", ""),
                "timestamp": full_health["timestamp"],
                "healthy_components": full_health["summary"]["healthy"],
                "total_components": full_health["summary"]["total_checks"]
            }
            
        except Exception as e:
            logger.error(f"Simple health check failed: {str(e)}")
            return {
                "status": HealthStatus.UNHEALTHY,
                "service": "processing-service",
                "version": "1.0.0",
                "environment": "unknown",
                "base_path": "",
                "timestamp": datetime.now().isoformat(),
                "message": f"Health check failed: {str(e)}"
            }


# Global health service instance
processing_health_service = ProcessingHealthService()