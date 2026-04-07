
import sys
import os
import time
import json
import logging
import threading
import uuid
import traceback
from datetime import datetime

# Add project root to path rigidly
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

print(f"DEBUG: sys.path: {sys.path}")

try:
    from libs.messaging.rabbitmq_manager import RabbitMQManager
    from libs.messaging.rabbitmq_config import RabbitMQConfig
    from libs.messaging.audit_publisher import AuditPublisher
    from libs.messaging.job_publisher import JobPublisher
    from libs.messaging.audit_logger import AuditLogger
    from libs.messaging.base_consumer import BaseConsumer
    from libs.messaging.message_models import JobTriggerMessage, AuditLogMessage
except ImportError as e:
    print("CRITICAL IMPORT ERROR")
    traceback.print_exc()
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("RabbitMQ_Tester")

# Mock Config to ensure we use localhost
os.environ["RABBITMQ_HOST"] = "localhost"
os.environ["RABBITMQ_PORT"] = "5672"
os.environ["USE_RABBITMQ_AUDIT"] = "true"
os.environ["USE_RABBITMQ_JOBS"] = "true"

class TestConsumer(BaseConsumer):
    def __init__(self, queue_name, routing_key, exchange):
        super().__init__(queue_name, exchange, routing_key)
        self.received_messages = []
        
    def process_message(self, message, properties):
        logger.info(f"Received message: {message}")
        self.received_messages.append(message)
        return True

def test_connectivity():
    logger.info(">>> TEST 1: Connectivity Check")
    manager = RabbitMQManager()
    conn = manager.get_connection()
    if conn and conn.is_open:
        logger.info("✅ Connection Successful")
        return True
    else:
        logger.error("❌ Connection Failed")
        return False

def test_topology():
    logger.info(">>> TEST 2: Topology Check (Exchanges & Queues)")
    try:
        manager = RabbitMQManager()
        channel = manager.get_channel()
        
        # Declarations should happen automatically in Manager/Publisher, but let's force them here via Publisher initialization
        audit_pub = AuditPublisher()
        job_pub = JobPublisher()
        
        # Verify by trying to declare passively (raises error if not exists)
        channel.exchange_declare(exchange="epms.audit", durable=True, exchange_type='topic', passive=True)
        logger.info("✅ Exchange 'epms.audit' exists")
        
        channel.exchange_declare(exchange="epms.jobs", durable=True, exchange_type='direct', passive=True)
        logger.info("✅ Exchange 'epms.jobs' exists")
        
        return True
    except Exception as e:
        logger.error(f"❌ Topology Check Failed: {e}")
        return False

def test_audit_round_trip():
    logger.info(">>> TEST 3: Audit Log Round Trip")
    
    # 1. Start Consumer
    consumer = TestConsumer(
        queue_name="test_audit_queue", 
        routing_key="audit.#", 
        exchange="epms.audit"
    )
    consumer.start()
    
    # Give consumer time to setup
    time.sleep(1)
    
    # 2. Publish Message
    audit_logger = AuditLogger(unified_queue_service=None) # No fallback for this test
    unique_id = str(uuid.uuid4())
    
    logger.info(f"Publishing audit log with ID: {unique_id}")
    audit_logger.log_system_operation(
        service="test_script",
        action="test_op",
        details={
            "message": "Running integration test",
            "correlation_id": unique_id
        }
    )
    
    # 3. Wait for Receipt
    time.sleep(2)
    consumer.stop()
    consumer.join()
    
    # 4. Verify
    found = False
    for msg in consumer.received_messages:
        # Message might be dict or json string
        if isinstance(msg, str):
            msg = json.loads(msg)
        
        # AuditLogger wraps it, so check payload
        if msg.get('correlation_id') == unique_id:
            found = True
            break
            
    if found:
        logger.info("✅ Audit Message Received & Verified")
        return True
    else:
        logger.error(f"❌ Audit Message Not Found. Received: {len(consumer.received_messages)}")
        return False

def test_job_round_trip():
    logger.info(">>> TEST 4: Job Trigger Round Trip")
    
    # 1. Start Consumer
    consumer = TestConsumer(
        queue_name="test_job_queue", 
        routing_key="job.trigger", 
        exchange="epms.jobs"
    )
    consumer.start()
    
    # Give consumer time to setup
    time.sleep(1)
    
    # 2. Publish Message
    job_pub = JobPublisher()
    unique_payload = {"test_id": str(uuid.uuid4())}
    
    logger.info(f"Publishing job trigger with payload: {unique_payload}")
    job_pub.publish_job_trigger(
        job_type="test_processing",
        source_service="tester",
        payload=unique_payload
    )
    
    # 3. Wait for Receipt
    time.sleep(2)
    consumer.stop()
    consumer.join()
    
    # 4. Verify
    found = False
    for msg in consumer.received_messages:
        if isinstance(msg, str):
            msg = json.loads(msg)
            
        if msg.get('payload') == unique_payload:
            found = True
            break
            
    if found:
        logger.info("✅ Job Trigger Received & Verified")
        return True
    else:
        logger.error("❌ Job Trigger Not Found")
        return False

def test_circuit_breaker_logic():
    logger.info(">>> TEST 5: Circuit Breaker Simulation")
    
    # This is a unit-test style verification of the logic, as we can't easily kill RMQ from here
    from libs.messaging.circuit_breaker import CircuitBreaker, CircuitState
    
    # Needs to mock config for this test or rely on default
    # But since we instantiating manually, we can pass args if constructor supported it.
    # The constructor takes no args and reads from config.
    # We can rely on default config which reads env vars.
    
    cb = CircuitBreaker()
    # Override settings explicitly for test speed
    cb.failure_threshold = 2
    cb.recovery_timeout = 1
    
    # 1. Normal State
    if cb.state != CircuitState.CLOSED:
        logger.error("❌ Initial state should be CLOSED")
        return False
    
    # 2. Simulate Failures
    cb.record_failure() 
    cb.record_failure() # Threshold reached
    
    if cb.state != CircuitState.OPEN:
        logger.error(f"❌ State should be OPEN after failures. Got: {cb.state}")
        return False
    logger.info("✅ Circuit Breaker Opened on failures")
    
    # 3. Verify Allow Request (Should be False)
    if cb.allow_request():
        logger.error("❌ Circuit Breaker allowed request while OPEN")
        return False
    logger.info("✅ Circuit Breaker blocked request")
    
    # 4. Wait for Recovery Timeout
    logger.info("Waiting for recovery timeout (1s)...")
    time.sleep(1.1)
    
    # 5. Verify Half-Open (allow_request triggers state check)
    if not cb.allow_request():
         logger.error("❌ Circuit Breaker should allow 1 request in HALF_OPEN")
         return False
         
    if cb.state != CircuitState.HALF_OPEN:
        logger.error("❌ State should be HALF_OPEN")
        return False
    logger.info("✅ Circuit Breaker moved to HALF_OPEN")
    
    # 6. Simulate Success
    cb.record_success()
    
    if cb.state != CircuitState.CLOSED:
        logger.error("❌ State should be CLOSED after success")
        return False
    logger.info("✅ Circuit Breaker Recovered to CLOSED")
    
    return True

if __name__ == "__main__":
    logger.info("Starting Comprehensive RabbitMQ Verification...")
    
    success = True
    
    if not test_connectivity(): success = False
    if not test_topology(): success = False
    if not test_audit_round_trip(): success = False
    if not test_job_round_trip(): success = False
    if not test_circuit_breaker_logic(): success = False
    
    if success:
        logger.info("\n🎉 ALL TESTS PASSED! RabbitMQ Integration is Healthy.")
        sys.exit(0)
    else:
        logger.error("\n⚠️ SOME TESTS FAILED. Please review logs.")
        sys.exit(1)
