
import time
import logging
from enum import Enum
from threading import Lock
from .rabbitmq_config import config

logger = logging.getLogger(__name__)

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitBreaker:
    """
    Circuit breaker pattern implementation.
    Prevents repeated calls to a failing service.
    """
    
    def __init__(self):
        self.failure_threshold = config.CIRCUIT_BREAKER_FAILURE_THRESHOLD
        self.recovery_timeout = config.CIRCUIT_BREAKER_RECOVERY_TIMEOUT
        self.success_threshold = config.CIRCUIT_BREAKER_SUCCESS_THRESHOLD
        
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = 0
        self.lock = Lock()
        
    def allow_request(self) -> bool:
        """Check if request is allowed based on circuit state"""
        with self.lock:
            if self.state == CircuitState.CLOSED:
                return True
                
            if self.state == CircuitState.OPEN:
                if time.time() - self.last_failure_time > self.recovery_timeout:
                    logger.info("Circuit breaker transitioning to HALF_OPEN")
                    self.state = CircuitState.HALF_OPEN
                    return True
                return False
                
            if self.state == CircuitState.HALF_OPEN:
                # Allow one request to test recovery
                return True
                
        return False
        
    def record_success(self):
        """Record a successful execution"""
        with self.lock:
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.success_threshold:
                    logger.info("Circuit breaker transitioning to CLOSED (recovered)")
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
                    self.success_count = 0
            
            elif self.state == CircuitState.CLOSED:
                # Reset failure count on success if we had some failures
                if self.failure_count > 0:
                    self.failure_count = 0
    
    def record_failure(self):
        """Record a failed execution"""
        with self.lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            
            if self.state == CircuitState.CLOSED:
                if self.failure_count >= self.failure_threshold:
                    logger.warning(f"Circuit breaker transitioning to OPEN after {self.failure_count} failures")
                    self.state = CircuitState.OPEN
            
            elif self.state == CircuitState.HALF_OPEN:
                logger.warning("Circuit breaker transitioning back to OPEN (recovery failed)")
                self.state = CircuitState.OPEN
                self.success_count = 0
