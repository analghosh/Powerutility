"""
Cache TTL Configuration
"""

from typing import Dict

# Default TTL configuration for different key patterns
DEFAULT_TTL_CONFIG: Dict[str, int] = {
    # User-related cache - 30 minutes
    "user:*": 1800,
    
    # Session cache - 2 hours  
    "session:*": 7200,
    
    # Temporary cache - 5 minutes
    "temp:*": 300,
    
    # API response cache - 15 minutes
    "api:*": 900,
    
    # Database query cache - 1 hour
    "db:*": 3600,
    
    # File cache - 24 hours
    "file:*": 86400,
    
    # Configuration cache - 10 minutes
    "config:*": 600
}


def get_ttl_config() -> Dict[str, int]:
    """
    Get TTL configuration from environment or return defaults
    
    Returns:
        Dictionary mapping key patterns to TTL values in seconds
    """
    # You can extend this to read from environment variables or config files
    # For example:
    # import os
    # import json
    # 
    # config_str = os.getenv('CACHE_TTL_CONFIG')
    # if config_str:
    #     try:
    #         return json.loads(config_str)
    #     except json.JSONDecodeError:
    #         pass
    
    return DEFAULT_TTL_CONFIG.copy()


def init_cache_with_config(
    redis_host: str = "localhost",
    redis_port: int = 6379,
    redis_db: int = 0,
    redis_password: str = None,
    default_ttl: int = 3600
):
    """
    Initialize cache service with TTL configuration
    
    Args:
        redis_host: Redis server host
        redis_port: Redis server port
        redis_db: Redis database number
        redis_password: Redis password
        default_ttl: Default TTL for keys not matching any pattern
    """
    from .cache_service import init_cache_service
    
    ttl_config = get_ttl_config()
    
    return init_cache_service(
        redis_host=redis_host,
        redis_port=redis_port,
        redis_db=redis_db,
        redis_password=redis_password,
        default_ttl=default_ttl,
        ttl_config=ttl_config
    )