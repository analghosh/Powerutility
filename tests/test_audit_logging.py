#!/usr/bin/env python3
"""
Test script to verify secure audit logging functionality
"""

import sys
import os
import asyncio
from datetime import datetime

# Add project root to path
sys.path.append('.')

from libs.audit_queue.simple_logger import audit_logger, log_user_action, log_security_event

async def test_audit_logging():
    """Test all audit logging methods"""
    
    print("🔍 Testing Secure Audit Logging System")
    print("=" * 50)
    
    # Test 1: User Action Logging
    print("\n1. Testing User Action Logging...")
    try:
        log_id = audit_logger.log_user_action(
            service="auth",
            user_id="test_user_123",
            action="login_test",
            message="Test user login for verification",
            session_id="test_session_456",
            client_ip="192.168.1.100",
            user_email="test.user@example.com",  # This should be masked
            phone_number="555-123-4567"  # This should be masked
        )
        print(f"✅ User Action Log ID: {log_id}")
    except Exception as e:
        print(f"❌ User Action Logging Failed: {e}")
    
    # Test 2: Security Event Logging
    print("\n2. Testing Security Event Logging...")
    try:
        log_id = audit_logger.log_security_event(
            service="auth",
            event_type="login_success",
            message="Test security event with PII data: john.doe@company.com",
            user_id="test_user_123",
            client_ip="10.0.0.1",  # This should be masked
            username="john.doe@company.com",  # This should be masked
            success=True
        )
        print(f"✅ Security Event Log ID: {log_id}")
    except Exception as e:
        print(f"❌ Security Event Logging Failed: {e}")
    
    # Test 3: System Operation Logging
    print("\n3. Testing System Operation Logging...")
    try:
        log_id = audit_logger.log_system_operation(
            service="auth",
            operation="password_validation",
            message="Testing system operation logging",
            correlation_id="test_job_789",
            success=True
        )
        print(f"✅ System Operation Log ID: {log_id}")
    except Exception as e:
        print(f"❌ System Operation Logging Failed: {e}")
    
    # Test 4: Error Event Logging
    print("\n4. Testing Error Event Logging...")
    try:
        log_id = audit_logger.log_error_event(
            service="auth",
            error_name="TestError",
            error_message="Test error with sensitive data: user@secret.com",
            user_id="test_user_123",
            correlation_id="test_error_001"
        )
        print(f"✅ Error Event Log ID: {log_id}")
    except Exception as e:
        print(f"❌ Error Event Logging Failed: {e}")
    
    # Test 5: Scheduled Job Logging
    print("\n5. Testing Scheduled Job Logging...")
    try:
        log_id = audit_logger.log_scheduled_job(
            service="auth",
            job_id="test_job_20241221_120000",
            status="completed",
            message="Test scheduled job completion",
            job_name="Test Audit Job",
            duration_ms=1500
        )
        print(f"✅ Scheduled Job Log ID: {log_id}")
    except Exception as e:
        print(f"❌ Scheduled Job Logging Failed: {e}")
    
    # Test 6: Convenience Functions
    print("\n6. Testing Convenience Functions...")
    try:
        log_id = log_user_action(
            service="auth",
            user_id="test_user_456",
            action="profile_access",
            message="Testing convenience function"
        )
        print(f"✅ Convenience Function Log ID: {log_id}")
    except Exception as e:
        print(f"❌ Convenience Function Failed: {e}")
    
    print(f"\n🎉 Audit Logging Test Completed at {datetime.now()}")
    print("\n📋 Next Steps:")
    print("1. Check your MongoDB audit_logs collection")
    print("2. Check log files in your configured directory")
    print("3. Verify PII data is masked in the logs")
    print("4. Test with real API endpoints")

if __name__ == "__main__":
    asyncio.run(test_audit_logging())