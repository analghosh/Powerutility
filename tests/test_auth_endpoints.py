#!/usr/bin/env python3
"""
Test script to verify audit logging through actual API endpoints
"""

import requests
import json
from datetime import datetime

# Configuration
BASE_URL = "http://localhost:8000"  # Adjust to your auth service URL
AUTH_ENDPOINT = f"{BASE_URL}/api/v1/auth"

def test_login_endpoint():
    """Test login endpoint audit logging"""
    print("\n🔐 Testing Login Endpoint Audit Logging...")
    
    # Test successful login
    login_data = {
        "username": "test.user@example.com",  # Should be masked in logs
        "password": "testpassword123"
    }
    
    try:
        response = requests.post(f"{AUTH_ENDPOINT}/login", json=login_data)
        print(f"Login Response Status: {response.status_code}")
        
        if response.status_code == 200:
            print("✅ Login successful - check logs for masked PII")
        elif response.status_code == 401:
            print("✅ Login failed (expected) - check logs for security event")
        else:
            print(f"⚠️ Unexpected response: {response.status_code}")
            
    except requests.exceptions.ConnectionError:
        print("❌ Cannot connect to auth service. Make sure it's running.")
    except Exception as e:
        print(f"❌ Login test failed: {e}")

def test_password_validation():
    """Test password validation endpoint"""
    print("\n🔒 Testing Password Validation Endpoint...")
    
    validation_data = {
        "password": "test123",
        "user_id": "test_user_with_email@domain.com"  # Should be masked
    }
    
    try:
        response = requests.post(f"{AUTH_ENDPOINT}/validate-password", json=validation_data)
        print(f"Password Validation Status: {response.status_code}")
        print("✅ Check logs for system operation with masked user_id")
        
    except requests.exceptions.ConnectionError:
        print("❌ Cannot connect to auth service")
    except Exception as e:
        print(f"❌ Password validation test failed: {e}")

def test_password_policy():
    """Test password policy endpoint"""
    print("\n📋 Testing Password Policy Endpoint...")
    
    try:
        response = requests.get(f"{AUTH_ENDPOINT}/password-policy")
        print(f"Password Policy Status: {response.status_code}")
        print("✅ Check logs for system operation")
        
    except requests.exceptions.ConnectionError:
        print("❌ Cannot connect to auth service")
    except Exception as e:
        print(f"❌ Password policy test failed: {e}")

def main():
    """Run all endpoint tests"""
    print("🧪 Testing Auth Service Audit Logging")
    print("=" * 50)
    print(f"Target URL: {BASE_URL}")
    print(f"Timestamp: {datetime.now()}")
    
    test_login_endpoint()
    test_password_validation()
    test_password_policy()
    
    print(f"\n📊 Test Summary:")
    print("1. Check MongoDB audit_logs collection for new entries")
    print("2. Verify PII data (emails, IPs) are masked with ***")
    print("3. Check log files for file-based logging")
    print("4. Look for correlation IDs and proper categorization")

if __name__ == "__main__":
    main()