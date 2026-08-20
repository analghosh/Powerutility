"""
Specific health check implementations for common services
"""

import asyncio
import logging
from typing import Any, Dict, Optional
from .health_checker import ComponentHealth, HealthStatus

logger = logging.getLogger(__name__)


# Health checks used to build a brand-new client on every call -- connect, probe, close --
# for both Redis and MongoDB. On processing that put a single /health at 145 ms and made
# the endpoint an unauthenticated load amplifier. The clients are pooled here instead.
#
# Keyed by the running event loop as well as the URL: both drivers bind their connection
# pool to the loop that created it, and a client built on a dead loop silently fails every
# operation. This codebase has been bitten by exactly that before.
_CLIENTS: Dict[Any, Any] = {}


def _loop_key(kind: str, url: str) -> Any:
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:      # no running loop (sync call site)
        loop_id = 0
    return (kind, url, loop_id)


async def check_redis_health(redis_url: Optional[str] = None) -> ComponentHealth:
    """
    Check Redis connection health
    
    Args:
        redis_url: Redis connection URL (optional)
        
    Returns:
        ComponentHealth object
    """
    try:
        import redis.asyncio as redis
        
        # Use provided URL or default
        url = redis_url or "redis://localhost:6379"
        
        # Reuse the pooled client rather than connecting per call.
        key = _loop_key("redis", url)
        client = _CLIENTS.get(key)
        if client is None:
            client = redis.from_url(url, decode_responses=True,
                                    socket_connect_timeout=5, socket_timeout=5)
            _CLIENTS[key] = client

        # Test connection with ping
        await client.ping()
        
        # Get Redis info
        info = await client.info()
        redis_version = info.get("redis_version", "unknown")
        connected_clients = info.get("connected_clients", 0)
        
        # Deliberately not closed: the client is pooled and reused by the next check.

        return ComponentHealth(
            name="redis",
            status=HealthStatus.HEALTHY,
            message="Redis connection successful",
            details={
                "version": redis_version,
                "connected_clients": connected_clients,
                "url": url.split("@")[-1] if "@" in url else url  # Hide credentials
            }
        )
        
    except ImportError:
        return ComponentHealth(
            name="redis",
            status=HealthStatus.UNHEALTHY,
            message="Redis client not installed (pip install redis)"
        )
    except Exception as e:
        return ComponentHealth(
            name="redis",
            status=HealthStatus.UNHEALTHY,
            message=f"Redis connection failed: {str(e)}"
        )


async def check_mongodb_health(mongo_url: Optional[str] = None) -> ComponentHealth:
    """
    Check MongoDB connection health
    
    Args:
        mongo_url: MongoDB connection URL (optional)
        
    Returns:
        ComponentHealth object
    """
    try:
        from pymongo import AsyncMongoClient
        
        # Use provided URL or default
        url = mongo_url or "mongodb://localhost:27017"
        
        # Reuse the pooled client rather than connecting per call.
        key = _loop_key("mongodb", url)
        client = _CLIENTS.get(key)
        if client is None:
            client = AsyncMongoClient(
                url,
                serverSelectionTimeoutMS=5000,
                maxPoolSize=2,  # Limit pool for health checks
                minPoolSize=0,
                maxIdleTimeMS=10000
            )
            _CLIENTS[key] = client

        # Test connection
        await client.admin.command('ping')
        
        # Get server info
        server_info = await client.admin.command('buildInfo')
        version = server_info.get("version", "unknown")
        
        # Get database stats
        db_names = await client.list_database_names()
        
        # Deliberately not closed: the client is pooled and reused by the next check.

        return ComponentHealth(
            name="mongodb",
            status=HealthStatus.HEALTHY,
            message="MongoDB connection successful",
            details={
                "version": version,
                "databases": len(db_names),
                "url": url.split("@")[-1] if "@" in url else url.replace("mongodb://", "")
            }
        )
        
    except ImportError:
        return ComponentHealth(
            name="mongodb",
            status=HealthStatus.UNHEALTHY,
            message="MongoDB client not installed (pip install pymongo)"
        )
    except Exception as e:
        return ComponentHealth(
            name="mongodb",
            status=HealthStatus.UNHEALTHY,
            message=f"MongoDB connection failed: {str(e)}"
        )


async def check_database_health(db_url: Optional[str] = None) -> ComponentHealth:
    """
    Check SQL database connection health (PostgreSQL/MySQL/SQLite)
    
    Args:
        db_url: Database connection URL (optional)
        
    Returns:
        ComponentHealth object
    """
    try:
        import asyncpg
        import aiomysql
        import aiosqlite
        
        # Default to SQLite if no URL provided
        url = db_url or "sqlite:///./app.db"
        
        if url.startswith("postgresql://") or url.startswith("postgres://"):
            return await _check_postgresql(url)
        elif url.startswith("mysql://"):
            return await _check_mysql(url)
        elif url.startswith("sqlite://"):
            return await _check_sqlite(url)
        else:
            return ComponentHealth(
                name="database",
                status=HealthStatus.UNHEALTHY,
                message=f"Unsupported database URL: {url}"
            )
            
    except ImportError as e:
        return ComponentHealth(
            name="database",
            status=HealthStatus.UNHEALTHY,
            message=f"Database client not installed: {str(e)}"
        )


async def _check_postgresql(url: str) -> ComponentHealth:
    """Check PostgreSQL connection"""
    try:
        import asyncpg
        
        conn = await asyncpg.connect(url)
        result = await conn.fetchval("SELECT version()")
        await conn.close()
        
        return ComponentHealth(
            name="database",
            status=HealthStatus.HEALTHY,
            message="PostgreSQL connection successful",
            details={"type": "postgresql", "version": result}
        )
    except Exception as e:
        return ComponentHealth(
            name="database",
            status=HealthStatus.UNHEALTHY,
            message=f"PostgreSQL connection failed: {str(e)}"
        )


async def _check_mysql(url: str) -> ComponentHealth:
    """Check MySQL connection"""
    try:
        import aiomysql
        
        # Parse MySQL URL (simplified)
        conn = await aiomysql.connect(host='localhost', port=3306, user='root')
        await conn.ensure_closed()
        
        return ComponentHealth(
            name="database",
            status=HealthStatus.HEALTHY,
            message="MySQL connection successful",
            details={"type": "mysql"}
        )
    except Exception as e:
        return ComponentHealth(
            name="database",
            status=HealthStatus.UNHEALTHY,
            message=f"MySQL connection failed: {str(e)}"
        )


async def _check_sqlite(url: str) -> ComponentHealth:
    """Check SQLite connection"""
    try:
        import aiosqlite
        
        db_path = url.replace("sqlite:///", "")
        async with aiosqlite.connect(db_path) as db:
            await db.execute("SELECT 1")
        
        return ComponentHealth(
            name="database",
            status=HealthStatus.HEALTHY,
            message="SQLite connection successful",
            details={"type": "sqlite", "path": db_path}
        )
    except Exception as e:
        return ComponentHealth(
            name="database",
            status=HealthStatus.UNHEALTHY,
            message=f"SQLite connection failed: {str(e)}"
        )


async def check_application_health() -> ComponentHealth:
    """
    Check application-specific health metrics
    
    Returns:
        ComponentHealth object
    """
    try:
        import psutil
        import os
        
        # Get system metrics
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        # Check if metrics are within acceptable ranges
        status = HealthStatus.HEALTHY
        issues = []
        
        if cpu_percent > 90:
            status = HealthStatus.DEGRADED
            issues.append(f"High CPU usage: {cpu_percent}%")
        
        if memory.percent > 90:
            status = HealthStatus.DEGRADED
            issues.append(f"High memory usage: {memory.percent}%")
        
        if disk.percent > 90:
            status = HealthStatus.DEGRADED
            issues.append(f"High disk usage: {disk.percent}%")
        
        message = "Application metrics normal"
        if issues:
            message = f"Performance issues detected: {', '.join(issues)}"
        
        return ComponentHealth(
            name="application",
            status=status,
            message=message,
            details={
                "cpu_percent": round(cpu_percent, 2),
                "memory_percent": round(memory.percent, 2),
                "disk_percent": round(disk.percent, 2),
                "process_id": os.getpid(),
                "available_memory_gb": round(memory.available / (1024**3), 2)
            }
        )
        
    except ImportError:
        return ComponentHealth(
            name="application",
            status=HealthStatus.DEGRADED,
            message="System monitoring not available (pip install psutil)",
            details={"process_id": os.getpid()}
        )
    except Exception as e:
        return ComponentHealth(
            name="application",
            status=HealthStatus.UNHEALTHY,
            message=f"Application health check failed: {str(e)}"
        )