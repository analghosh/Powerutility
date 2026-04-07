"""
Examples of using the global cache service in EPMS
"""

from typing import Optional, Dict, Any
import logging
from .cache_service import get_cache_service, get_timeblock_cache, cache_set, cache_get

logger = logging.getLogger(__name__)


class ExampleService:
    """
    Example service showing how to use the global cache service
    """
    
    def __init__(self):
        """Initialize example service"""
        self.cache = get_cache_service()
        self.timeblock_cache = get_timeblock_cache()
    
    def cache_user_data(self, user_id: str, user_data: Dict[str, Any], ttl: int = 3600) -> bool:
        """
        Example: Cache user data
        
        Args:
            user_id: User ID
            user_data: User data to cache
            ttl: Time to live in seconds
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if self.cache:
                cache_key = f"user:{user_id}"
                return self.cache.set(cache_key, user_data, ttl)
            return False
        except Exception as e:
            logger.error(f"Error caching user data: {e}")
            return False
    
    def get_cached_user_data(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Example: Get cached user data
        
        Args:
            user_id: User ID
            
        Returns:
            User data or None if not found
        """
        try:
            if self.cache:
                cache_key = f"user:{user_id}"
                return self.cache.get(cache_key)
            return None
        except Exception as e:
            logger.error(f"Error getting cached user data: {e}")
            return None
    
    def cache_api_response(self, endpoint: str, params: Dict[str, Any], response_data: Any, ttl: int = 300) -> bool:
        """
        Example: Cache API response
        
        Args:
            endpoint: API endpoint
            params: Request parameters
            response_data: Response data to cache
            ttl: Time to live in seconds
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if self.cache:
                # Create cache key from endpoint and params
                param_str = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
                cache_key = f"api:{endpoint}:{hash(param_str)}"
                return self.cache.set(cache_key, response_data, ttl)
            return False
        except Exception as e:
            logger.error(f"Error caching API response: {e}")
            return False
    
    def get_cached_api_response(self, endpoint: str, params: Dict[str, Any]) -> Optional[Any]:
        """
        Example: Get cached API response
        
        Args:
            endpoint: API endpoint
            params: Request parameters
            
        Returns:
            Cached response or None if not found
        """
        try:
            if self.cache:
                param_str = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
                cache_key = f"api:{endpoint}:{hash(param_str)}"
                return self.cache.get(cache_key)
            return None
        except Exception as e:
            logger.error(f"Error getting cached API response: {e}")
            return None
    
    def get_timeblock_for_time_range(self, time_range: str) -> Optional[int]:
        """
        Example: Get timeblock using specialized timeblock cache
        
        Args:
            time_range: Time range string (e.g., "00:00-00:15")
            
        Returns:
            Timeblock number or None if not found
        """
        try:
            if self.timeblock_cache:
                return self.timeblock_cache.get_timeblock(time_range)
            return None
        except Exception as e:
            logger.error(f"Error getting timeblock: {e}")
            return None
    
    def cache_calculation_result(self, calculation_type: str, input_params: Dict[str, Any], result: Any, ttl: int = 1800) -> bool:
        """
        Example: Cache expensive calculation results
        
        Args:
            calculation_type: Type of calculation
            input_params: Input parameters for the calculation
            result: Calculation result
            ttl: Time to live in seconds
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if self.cache:
                # Create deterministic cache key
                params_hash = hash(str(sorted(input_params.items())))
                cache_key = f"calc:{calculation_type}:{params_hash}"
                return self.cache.set(cache_key, result, ttl)
            return False
        except Exception as e:
            logger.error(f"Error caching calculation result: {e}")
            return False
    
    def get_cached_calculation_result(self, calculation_type: str, input_params: Dict[str, Any]) -> Optional[Any]:
        """
        Example: Get cached calculation result
        
        Args:
            calculation_type: Type of calculation
            input_params: Input parameters for the calculation
            
        Returns:
            Cached result or None if not found
        """
        try:
            if self.cache:
                params_hash = hash(str(sorted(input_params.items())))
                cache_key = f"calc:{calculation_type}:{params_hash}"
                return self.cache.get(cache_key)
            return None
        except Exception as e:
            logger.error(f"Error getting cached calculation result: {e}")
            return None


# Standalone functions using convenience methods

def cache_market_data(symbol: str, data: Dict[str, Any], ttl: int = 300) -> bool:
    """
    Example: Cache market data using convenience functions
    
    Args:
        symbol: Market symbol
        data: Market data
        ttl: Time to live in seconds
        
    Returns:
        True if successful, False otherwise
    """
    cache_key = f"market:{symbol}"
    return cache_set(cache_key, data, ttl)


def get_cached_market_data(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Example: Get cached market data using convenience functions
    
    Args:
        symbol: Market symbol
        
    Returns:
        Market data or None if not found
    """
    cache_key = f"market:{symbol}"
    return cache_get(cache_key)


def cache_weather_forecast(location: str, forecast_data: Dict[str, Any], ttl: int = 1800) -> bool:
    """
    Example: Cache weather forecast data
    
    Args:
        location: Location identifier
        forecast_data: Weather forecast data
        ttl: Time to live in seconds
        
    Returns:
        True if successful, False otherwise
    """
    cache_key = f"weather:{location}"
    return cache_set(cache_key, forecast_data, ttl)


def get_cached_weather_forecast(location: str) -> Optional[Dict[str, Any]]:
    """
    Example: Get cached weather forecast
    
    Args:
        location: Location identifier
        
    Returns:
        Weather forecast or None if not found
    """
    cache_key = f"weather:{location}"
    return cache_get(cache_key)


# FastAPI endpoint examples

async def example_fastapi_endpoint_with_cache(request):
    """
    Example FastAPI endpoint using cache service
    """
    from libs.middleware import get_cache_from_request
    
    # Get cache service from request
    cache = get_cache_from_request(request)
    
    if cache:
        # Try to get cached response
        cache_key = f"endpoint_data:{request.url.path}"
        cached_data = cache.get(cache_key)
        
        if cached_data:
            logger.info("Returning cached data")
            return cached_data
        
        # Generate new data (expensive operation)
        new_data = {"message": "This is expensive data", "timestamp": "2024-01-01"}
        
        # Cache the result
        cache.set(cache_key, new_data, ttl=300)  # Cache for 5 minutes
        
        return new_data
    else:
        # Fallback when cache is not available
        return {"message": "Cache not available", "timestamp": "2024-01-01"}


async def example_fastapi_dependency_injection():
    """
    Example using FastAPI dependency injection
    """
    from libs.middleware import get_cache_dependency
    from fastapi import Depends
    
    def get_data_with_cache(cache_service = Depends(get_cache_dependency)):
        if cache_service:
            # Use cache service
            cached_result = cache_service.get("some_key")
            if cached_result:
                return cached_result
            
            # Generate and cache new result
            new_result = {"data": "generated"}
            cache_service.set("some_key", new_result, ttl=600)
            return new_result
        
        # Fallback without cache
        return {"data": "generated_without_cache"}
    
    return get_data_with_cache


# Usage in processors (like ExchangeProcessor)

class ExampleProcessor:
    """
    Example processor using global cache service
    """
    
    def __init__(self):
        """Initialize processor with cache service"""
        self.cache = get_cache_service()
        self.timeblock_cache = get_timeblock_cache()
    
    def process_data_with_cache(self, data_id: str, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Example: Process data with caching
        
        Args:
            data_id: Data identifier
            raw_data: Raw data to process
            
        Returns:
            Processed data
        """
        try:
            # Check if processed data is already cached
            cache_key = f"processed:{data_id}"
            
            if self.cache:
                cached_result = self.cache.get(cache_key)
                if cached_result:
                    logger.info(f"Using cached processed data for {data_id}")
                    return cached_result
            
            # Process data (expensive operation)
            processed_data = self._expensive_processing(raw_data)
            
            # Cache the result
            if self.cache:
                self.cache.set(cache_key, processed_data, ttl=1800)  # Cache for 30 minutes
                logger.info(f"Cached processed data for {data_id}")
            
            return processed_data
            
        except Exception as e:
            logger.error(f"Error processing data with cache: {e}")
            # Fallback to processing without cache
            return self._expensive_processing(raw_data)
    
    def _expensive_processing(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """Simulate expensive data processing"""
        # This would be your actual processing logic
        return {
            "processed": True,
            "original_data": raw_data,
            "processing_timestamp": "2024-01-01T00:00:00Z"
        }
    
    def get_timeblock_with_cache(self, time_range: str) -> Optional[int]:
        """
        Example: Get timeblock using cache
        
        Args:
            time_range: Time range string
            
        Returns:
            Timeblock number or None
        """
        if self.timeblock_cache:
            return self.timeblock_cache.get_timeblock(time_range)
        return None