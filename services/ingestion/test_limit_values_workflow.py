#!/usr/bin/env python3
"""
Test script for limit_values file upload workflow
Tests the complete workflow from file upload to approval
"""

import asyncio
import requests
import json
import logging
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
INGESTION_SERVICE_URL = "http://localhost:8002"
PROCESSING_SERVICE_URL = "http://localhost:8003"
JWT_TOKEN = "your-jwt-token-here"  # Replace with actual token
SAMPLE_CSV_PATH = "sample_limit_values.csv"

class LimitValuesWorkflowTester:
    def __init__(self, jwt_token: str):
        self.jwt_token = jwt_token
        self.headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Content-Type": "application/json"
        }
        self.upload_id = None
        self.approval_id = None
    
    def test_file_upload(self):
        """Test file upload to ingestion service"""
        logger.info("=== Testing File Upload ===")
        
        if not Path(SAMPLE_CSV_PATH).exists():
            logger.error(f"Sample CSV file not found: {SAMPLE_CSV_PATH}")
            return False
        
        url = f"{INGESTION_SERVICE_URL}/file-upload/upload"
        
        with open(SAMPLE_CSV_PATH, 'rb') as f:
            files = {'file': f}
            data = {
                'data_type': 'limit_values',
                'provider_type': 'limit_values_upload_provider'
            }
            headers = {'Authorization': f'Bearer {self.jwt_token}'}
            
            try:
                response = requests.post(url, files=files, data=data, headers=headers)
                logger.info(f"Upload response status: {response.status_code}")
                
                if response.status_code == 201:
                    result = response.json()
                    self.upload_id = result.get('upload_id')
                    self.approval_id = result.get('approval_id')
                    
                    logger.info(f"Upload successful!")
                    logger.info(f"Upload ID: {self.upload_id}")
                    logger.info(f"Approval ID: {self.approval_id}")
                    logger.info(f"Status: {result.get('status')}")
                    logger.info(f"Message: {result.get('status_message')}")
                    
                    return True
                else:
                    logger.error(f"Upload failed: {response.text}")
                    return False
                    
            except Exception as e:
                logger.error(f"Upload error: {e}")
                return False
    
    def test_upload_status(self):
        """Test upload status check"""
        if not self.upload_id:
            logger.error("No upload ID available")
            return False
        
        logger.info("=== Testing Upload Status ===")
        
        url = f"{INGESTION_SERVICE_URL}/file-upload/upload/{self.upload_id}/status"
        
        try:
            response = requests.get(url, headers=self.headers)
            logger.info(f"Status check response: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"Upload status: {result.get('status')}")
                logger.info(f"Status message: {result.get('status_message')}")
                logger.info(f"File name: {result.get('file_name')}")
                logger.info(f"File size: {result.get('file_size')} bytes")
                return True
            else:
                logger.error(f"Status check failed: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Status check error: {e}")
            return False
    
    def test_list_pending_approvals(self):
        """Test listing pending approvals"""
        logger.info("=== Testing List Pending Approvals ===")
        
        url = f"{PROCESSING_SERVICE_URL}/v2/limit-values/pending"
        
        try:
            response = requests.get(url, headers=self.headers)
            logger.info(f"Pending approvals response: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                approvals = result.get('data', [])
                logger.info(f"Found {len(approvals)} pending approvals")
                
                for approval in approvals:
                    logger.info(f"Approval ID: {approval.get('_id')}")
                    logger.info(f"Title: {approval.get('title')}")
                    logger.info(f"Status: {approval.get('status')}")
                    logger.info(f"Requested by: {approval.get('requested_username')}")
                    logger.info(f"Total items: {approval.get('counts', {}).get('total_items', 0)}")
                    logger.info("---")
                
                return True
            else:
                logger.error(f"List approvals failed: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"List approvals error: {e}")
            return False
    
    def test_get_latest_limit_values(self):
        """Test getting latest limit values"""
        logger.info("=== Testing Get Latest Limit Values ===")
        
        url = f"{PROCESSING_SERVICE_URL}/v2/limit-values/latest"
        
        try:
            response = requests.get(url, headers=self.headers)
            logger.info(f"Latest limit values response: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                records = result.get('data', {}).get('records', [])
                logger.info(f"Found {len(records)} latest limit values records")
                
                if records:
                    sample_record = records[0]
                    logger.info(f"Sample record: {json.dumps(sample_record, indent=2, default=str)}")
                
                return True
            else:
                logger.error(f"Get latest limit values failed: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Get latest limit values error: {e}")
            return False
    
    def test_approve_request(self):
        """Test approving a request"""
        if not self.approval_id:
            logger.error("No approval ID available")
            return False
        
        logger.info("=== Testing Approve Request ===")
        
        url = f"{PROCESSING_SERVICE_URL}/v2/limit-values/approve/{self.approval_id}"
        
        try:
            response = requests.post(url, headers=self.headers)
            logger.info(f"Approve response: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"Approval successful!")
                logger.info(f"Status: {result.get('data', {}).get('status')}")
                logger.info(f"Upserts: {result.get('data', {}).get('upserts', 0)}")
                logger.info(f"Deletes: {result.get('data', {}).get('deletes', 0)}")
                return True
            else:
                logger.error(f"Approve failed: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Approve error: {e}")
            return False
    
    def test_reject_request(self):
        """Test rejecting a request (for demonstration)"""
        logger.info("=== Testing Reject Request (Demo) ===")
        logger.info("Note: This is just for demonstration - no actual rejection will happen")
        
        # This would be used if we had another pending request
        # url = f"{PROCESSING_SERVICE_URL}/v2/limit-values/reject/{approval_id}"
        # data = {"reason": "Test rejection"}
        # response = requests.post(url, headers=self.headers, json=data)
        
        return True
    
    def run_all_tests(self):
        """Run all tests in sequence"""
        logger.info("Starting Limit Values Workflow Tests")
        logger.info("=" * 50)
        
        tests = [
            ("File Upload", self.test_file_upload),
            ("Upload Status", self.test_upload_status),
            ("List Pending Approvals", self.test_list_pending_approvals),
            ("Get Latest Limit Values", self.test_get_latest_limit_values),
            ("Approve Request", self.test_approve_request),
            ("Reject Request Demo", self.test_reject_request),
        ]
        
        results = []
        for test_name, test_func in tests:
            logger.info(f"\n--- {test_name} ---")
            try:
                success = test_func()
                results.append((test_name, success))
                if success:
                    logger.info(f"✅ {test_name} PASSED")
                else:
                    logger.error(f"❌ {test_name} FAILED")
            except Exception as e:
                logger.error(f"❌ {test_name} ERROR: {e}")
                results.append((test_name, False))
        
        # Summary
        logger.info("\n" + "=" * 50)
        logger.info("TEST SUMMARY")
        logger.info("=" * 50)
        
        passed = sum(1 for _, success in results if success)
        total = len(results)
        
        for test_name, success in results:
            status = "PASS" if success else "FAIL"
            logger.info(f"{test_name}: {status}")
        
        logger.info(f"\nOverall: {passed}/{total} tests passed")
        
        if passed == total:
            logger.info("🎉 All tests passed!")
        else:
            logger.warning(f"⚠️  {total - passed} tests failed")
        
        return passed == total


def main():
    """Main function"""
    print("Limit Values Workflow Test")
    print("=" * 30)
    print("Make sure you have:")
    print("1. JWT token for authentication")
    print("2. Ingestion service running on port 8002")
    print("3. Processing service running on port 8003")
    print("4. Provider configuration created")
    print("5. Sample CSV file in the same directory")
    print()
    
    # Get JWT token from user
    jwt_token = input("Enter your JWT token (or press Enter to use placeholder): ").strip()
    if not jwt_token:
        jwt_token = JWT_TOKEN
        print(f"Using placeholder token: {jwt_token}")
        print("Note: Replace with actual token for real testing")
    
    # Run tests
    tester = LimitValuesWorkflowTester(jwt_token)
    success = tester.run_all_tests()
    
    if success:
        print("\n🎉 All tests completed successfully!")
        return 0
    else:
        print("\n❌ Some tests failed. Check the logs above.")
        return 1


if __name__ == "__main__":
    exit(main())
