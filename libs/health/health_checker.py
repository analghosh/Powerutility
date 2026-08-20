"""
Comprehensive health check service for EPMS applications
"""

import asyncio
import logging
import os
import time
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional, List
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class HealthStatus(str, Enum):
    """Health status enumeration"""
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DEGRADED = "degraded"


class ComponentHealth(BaseModel):
    """Individual component health status"""
    name: str
    status: HealthStatus
    message: str
    response_time_ms: Optional[float] = None
    details: Optional[Dict[str, Any]] = None


class HealthChecker:
    """Comprehensive health checker for application dependencies"""
    
    # How long a completed result stays servable, in seconds. `/health` is unauthenticated
    # and every request used to run the full dependency sweep, so an anonymous flood was a
    # denial-of-service amplifier: measured on processing, 30 concurrent anonymous
    # /health requests took real authenticated API calls from 8 ms to 1,700 ms median --
    # 221x -- because the probes share the event loop with live traffic.
    #
    # Caching removes the amplification rather than making each probe cheaper: any number
    # of requests inside the window collapse onto one real sweep. Five seconds is well
    # inside what a load balancer or monitor needs, and set to 0 to disable.
    DEFAULT_CACHE_TTL_SECONDS = 5.0

    def __init__(self, cache_ttl_seconds: Optional[float] = None):
        self.checks = {}
        self.timeout = 5.0  # Default timeout for health checks
        if cache_ttl_seconds is None:
            try:
                cache_ttl_seconds = float(os.getenv("HEALTH_CACHE_TTL_SECONDS",
                                                    self.DEFAULT_CACHE_TTL_SECONDS))
            except (TypeError, ValueError):
                cache_ttl_seconds = self.DEFAULT_CACHE_TTL_SECONDS
        self.cache_ttl_seconds = max(0.0, cache_ttl_seconds)
        self._cached_result: Optional[Dict[str, Any]] = None
        self._cached_at: float = 0.0
        self._probe_lock: Optional[asyncio.Lock] = None
    
    def register_check(self, name: str, check_func, timeout: Optional[float] = None):
        """
        Register a health check function
        
        Args:
            name: Name of the component to check
            check_func: Async function that returns ComponentHealth
            timeout: Timeout for this specific check
        """
        self.checks[name] = {
            "func": check_func,
            "timeout": timeout or self.timeout
        }
    
    async def check_component(self, name: str, check_func, timeout: float) -> ComponentHealth:
        """
        Check individual component health with timeout
        
        Args:
            name: Component name
            check_func: Health check function
            timeout: Timeout in seconds
            
        Returns:
            ComponentHealth object
        """
        start_time = datetime.now()
        
        try:
            # Run health check with timeout
            result = await asyncio.wait_for(check_func(), timeout=timeout)
            
            # Calculate response time
            response_time = (datetime.now() - start_time).total_seconds() * 1000
            
            if isinstance(result, ComponentHealth):
                result.response_time_ms = response_time
                return result
            else:
                # If function returns boolean or other type
                status = HealthStatus.HEALTHY if result else HealthStatus.UNHEALTHY
                return ComponentHealth(
                    name=name,
                    status=status,
                    message="OK" if result else "Check failed",
                    response_time_ms=response_time
                )
                
        except asyncio.TimeoutError:
            response_time = (datetime.now() - start_time).total_seconds() * 1000
            return ComponentHealth(
                name=name,
                status=HealthStatus.UNHEALTHY,
                message=f"Timeout after {timeout}s",
                response_time_ms=response_time
            )
        except Exception as e:
            response_time = (datetime.now() - start_time).total_seconds() * 1000
            logger.error(f"Health check failed for {name}: {str(e)}")
            return ComponentHealth(
                name=name,
                status=HealthStatus.UNHEALTHY,
                message=f"Error: {str(e)}",
                response_time_ms=response_time
            )
    
    def _fresh_cache(self) -> Optional[Dict[str, Any]]:
        """The cached result if it is still inside the TTL, else None."""
        if self.cache_ttl_seconds <= 0 or self._cached_result is None:
            return None
        if (time.monotonic() - self._cached_at) >= self.cache_ttl_seconds:
            return None
        return self._cached_result

    async def check_all(self) -> Dict[str, Any]:
        """
        Run all registered health checks, reusing a recent result when one exists.

        Single-flight is the point, not just the TTL. Without the lock, sixty requests
        arriving on a cold cache would each start their own sweep -- exactly the behaviour
        being fixed -- and only then populate the cache. With it, the first caller probes
        and the other fifty-nine await that same probe.
        """
        cached = self._fresh_cache()
        if cached is not None:
            return cached

        # Created lazily: the lock must belong to the running loop, and this object is
        # constructed at import time, before there is one.
        if self._probe_lock is None:
            self._probe_lock = asyncio.Lock()

        async with self._probe_lock:
            # Re-check inside the lock: while we waited, the holder may have refreshed it.
            cached = self._fresh_cache()
            if cached is not None:
                return cached

            result = await self._check_all_uncached()
            if self.cache_ttl_seconds > 0:
                self._cached_result = result
                self._cached_at = time.monotonic()
            return result

    async def _check_all_uncached(self) -> Dict[str, Any]:
        """
        Run all registered health checks
        
        Returns:
            Dictionary with overall health status and component details
        """
        if not self.checks:
            return {
                "status": HealthStatus.HEALTHY,
                "message": "No health checks registered",
                "timestamp": datetime.now().isoformat(),
                "components": {}
            }
        
        # Run all health checks concurrently
        tasks = []
        for name, check_config in self.checks.items():
            task = self.check_component(
                name, 
                check_config["func"], 
                check_config["timeout"]
            )
            tasks.append((name, task))
        
        # Wait for all checks to complete
        results = {}
        for name, task in tasks:
            results[name] = await task
        
        # Determine overall health status
        overall_status = self._determine_overall_status(results)
        
        # Count healthy/unhealthy components
        healthy_count = sum(1 for r in results.values() if r.status == HealthStatus.HEALTHY)
        total_count = len(results)
        
        return {
            "status": overall_status,
            "message": self._get_overall_message(overall_status, healthy_count, total_count),
            "timestamp": datetime.now().isoformat(),
            "components": {name: result.dict() for name, result in results.items()},
            "summary": {
                "total_checks": total_count,
                "healthy": healthy_count,
                "unhealthy": total_count - healthy_count
            }
        }
    
    def _determine_overall_status(self, results: Dict[str, ComponentHealth]) -> HealthStatus:
        """Determine overall health status from component results"""
        if not results:
            return HealthStatus.HEALTHY
        
        unhealthy_count = sum(1 for r in results.values() if r.status == HealthStatus.UNHEALTHY)
        degraded_count = sum(1 for r in results.values() if r.status == HealthStatus.DEGRADED)
        
        if unhealthy_count > 0:
            return HealthStatus.UNHEALTHY
        elif degraded_count > 0:
            return HealthStatus.DEGRADED
        else:
            return HealthStatus.HEALTHY
    
    def _get_overall_message(self, status: HealthStatus, healthy: int, total: int) -> str:
        """Get overall health message"""
        if status == HealthStatus.HEALTHY:
            return f"All systems operational ({healthy}/{total} components healthy)"
        elif status == HealthStatus.DEGRADED:
            return f"Some components degraded ({healthy}/{total} components healthy)"
        else:
            return f"System unhealthy ({healthy}/{total} components healthy)"


# Global health checker instance
health_checker = HealthChecker()