"""
Queue Health Check Router for Scheduler Service
"""

from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any
import logging

from libs.audit_queue.unified_queue_service import UnifiedQueueService
from libs.audit_queue.simple_logger import AuditLogger
from libs.messaging.rabbitmq_manager import RabbitMQManager
from ..core.dependencies import get_unified_queue_service, get_audit_logger

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/queue", tags=["queue"])


@router.get("/health")
async def queue_health_check(unified_queue_service: UnifiedQueueService = Depends(get_unified_queue_service), audit_logger: AuditLogger = Depends(get_audit_logger)) -> Dict[str, Any]:
    """
    Check the health status of the audit queue system
    
    Returns:
        Dict containing queue health information
    """
    try:
        # Log user action for queue_health_check
        await audit_logger.log_user_action(
            service="scheduler",
            user_id="system",  # TODO: Replace with actual user_id from authentication
            action="queue_health_check",
            message=f"User accessed queue_health_check",
            success=True
        )
    except Exception as audit_error:
        # Don't fail the request if audit logging fails
        pass
    

    try:
        health_status = unified_queue_service.health_check()
        
        if health_status["status"] == "unhealthy":
            raise HTTPException(
                status_code=503,
                detail="Queue system is unhealthy",
                headers={"X-Queue-Status": "unhealthy"}
            )
        
        return {
            "status": "healthy",
            "queue_health": health_status,
            "message": "Queue system is operating normally"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Queue health check failed: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"Queue health check failed: {str(e)}"
        )


@router.get("/stats")
async def queue_stats(unified_queue_service: UnifiedQueueService = Depends(get_unified_queue_service), audit_logger: AuditLogger = Depends(get_audit_logger)) -> Dict[str, Any]:
    """
    Get detailed queue statistics and metrics
    
    Returns:
        Dict containing queue statistics
    """
    try:
        # Log user action for queue_stats
        await audit_logger.log_user_action(
            service="scheduler",
            user_id="system",  # TODO: Replace with actual user_id from authentication
            action="queue_stats",
            message=f"User accessed queue_stats",
            success=True
        )
    except Exception as audit_error:
        # Don't fail the request if audit logging fails
        pass
    

    try:
        stats = unified_queue_service.get_queue_status()
        
        return {
            "status": "success",
            "queue_stats": stats,
            "timestamp": stats.get("metrics", {}).get("last_processed")
        }
        
    except Exception as e:
        logger.error("Failed to get queue stats: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get queue stats: {str(e)}"
        )


@router.post("/cleanup")
async def cleanup_old_jobs(days: int = 7, unified_queue_service: UnifiedQueueService = Depends(get_unified_queue_service), audit_logger: AuditLogger = Depends(get_audit_logger)):
    """Clean up old job records"""
    try:
        # Log user action for queue_cleanup
        await audit_logger.log_user_action(
            service="scheduler",
            user_id="system",  # TODO: Replace with actual user_id from authentication
            action="queue_cleanup",
            message=f"User accessed cleanup_old_jobs",
            success=True
        )
    except Exception as audit_error:
        # Don't fail the request if audit logging fails
        pass
    
    try:
        cleaned_count = unified_queue_service.queue.cleanup_old_completed_jobs(days)
        
        return {
            "status": "success",
            "cleaned_jobs": cleaned_count,
            "retention_days": days,
            "message": f"Cleaned up {cleaned_count} old completed jobs"
        }
        
    except Exception as e:
        logger.error("Failed to cleanup old jobs: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to cleanup old jobs: {str(e)}"
        )


@router.get("/pending")
async def get_pending_jobs(limit: int = 100, unified_queue_service: UnifiedQueueService = Depends(get_unified_queue_service), audit_logger: AuditLogger = Depends(get_audit_logger)) -> Dict[str, Any]:
    """
    Get pending jobs that need to be processed
    
    Args:
        limit: Maximum number of jobs to return (default: 100)
    
    Returns:
        Dictionary containing pending jobs information
    """
    try:
        # Log user action for queue_pending_jobs
        await audit_logger.log_user_action(
            service="scheduler",
            user_id="system",  # TODO: Replace with actual user_id from authentication
            action="queue_pending_jobs",
            message=f"User accessed get_pending_jobs",
            success=True
        )
    except Exception as audit_error:
        # Don't fail the request if audit logging fails
        pass

    try:
        pending_jobs = unified_queue_service.queue.get_pending_jobs(limit)
        
        return {
            "status": "success",
            "pending_jobs": pending_jobs,
            "count": len(pending_jobs),
            "limit": limit
        }
        
    except Exception as e:
        logger.error("Failed to get pending jobs: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get pending jobs: {str(e)}"
        )


@router.get("/rabbitmq/health")
async def rabbitmq_health_check() -> Dict[str, Any]:
    """
    Check the health status of the RabbitMQ connection
    """
    try:
        manager = RabbitMQManager()
        is_connected = manager.connection and manager.connection.is_open
        
        status = "healthy" if is_connected else "unhealthy"
        
        return {
            "status": status,
            "connected": is_connected,
            "connection_params": {
                "host": manager.host,
                "port": manager.port,
                "vhost": manager.vhost
            }
        }
    except Exception as e:
        logger.error(f"RabbitMQ health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e)
        }

