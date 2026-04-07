"""
Unit tests for Provider Routes
Tests provider API endpoints with complete mocking
"""

import unittest
from unittest.mock import patch
from datetime import datetime

from fastapi.testclient import TestClient
from fastapi import FastAPI

from services.ingestion.app.api.v1.routes.providers import router
from libs.models.provider_models import (
    CreateProviderRequest, ProviderResponse, 
    ProviderListResponse, ProviderSummary, IngestionType, DataType
)


class TestProviderRoutes(unittest.TestCase):
    """Test cases for Provider Routes"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.app = FastAPI()
        self.app.include_router(router)
        self.client = TestClient(self.app)
        
        # Mock the page access dependency
        self.mock_user = {"user_id": "test_user", "username": "testuser"}
    
    @patch('services.ingestion.app.api.v1.routes.providers.require_page_access')
    @patch('services.ingestion.app.api.v1.routes.providers.provider_service')
    def test_get_providers_success(self, mock_service, mock_auth):
        """Test successful GET /providers"""
        # Mock authentication
        mock_auth.return_value = self.mock_user
        
        # Mock service response
        mock_providers = [
            ProviderSummary(
                id="test_id_1",
                name="Test Provider 1",
                code="TEST_PROVIDER_1",
                enabled=True
            )
        ]
        
        mock_service.get_all_providers.return_value = ProviderListResponse(
            providers=mock_providers,
            total=1
        )
        
        # Make request
        response = self.client.get("/providers")
        
        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["providers"]), 1)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["providers"][0]["name"], "Test Provider 1")
    
    @patch('services.ingestion.app.api.v1.routes.providers.require_page_access')
    @patch('services.ingestion.app.api.v1.routes.providers.provider_service')
    def test_get_providers_with_filters(self, mock_service, mock_auth):
        """Test GET /providers with filters"""
        # Mock authentication
        mock_auth.return_value = self.mock_user
        
        # Mock service response
        mock_service.get_all_providers.return_value = ProviderListResponse(
            providers=[],
            total=0
        )
        
        # Make request with filters
        response = self.client.get(
            "/providers?ingestion_type=UPLOAD&data_type=DEMAND&enabled=true&skip=0&limit=10"
        )
        
        # Assertions
        self.assertEqual(response.status_code, 200)
        mock_service.get_all_providers.assert_called_once_with(
            ingestion_type=IngestionType.UPLOAD,
            data_type=DataType.DEMAND,
            enabled=True,
            skip=0,
            limit=10
        )
    
    @patch('services.ingestion.app.api.v1.routes.providers.require_page_access')
    @patch('services.ingestion.app.api.v1.routes.providers.provider_service')
    def test_get_provider_by_id_success(self, mock_service, mock_auth):
        """Test successful GET /providers/{provider_id}"""
        # Mock authentication
        mock_auth.return_value = self.mock_user
        
        # Mock service response
        mock_provider = ProviderResponse(
            id="test_id",
            name="Test Provider",
            code="TEST_PROVIDER",
            enabled=True,
            ingestion_type=IngestionType.UPLOAD,
            data_type=DataType.DEMAND,
            schema=[],
            unique_columns=[],
            mapping=[],
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        
        mock_service.get_provider_by_id.return_value = mock_provider
        
        # Make request
        response = self.client.get("/providers/test_id")
        
        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["id"], "test_id")
        self.assertEqual(data["name"], "Test Provider")
        self.assertEqual(data["code"], "TEST_PROVIDER")
    
    @patch('services.ingestion.app.api.v1.routes.providers.require_page_access')
    @patch('services.ingestion.app.api.v1.routes.providers.provider_service')
    def test_get_provider_by_id_not_found(self, mock_service, mock_auth):
        """Test GET /providers/{provider_id} when not found"""
        # Mock authentication
        mock_auth.return_value = self.mock_user
        
        # Mock service response
        mock_service.get_provider_by_id.return_value = None
        
        # Make request
        response = self.client.get("/providers/nonexistent_id")
        
        # Assertions
        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertIn("not found", data["detail"])
    
    @patch('services.ingestion.app.api.v1.routes.providers.require_page_access')
    @patch('services.ingestion.app.api.v1.routes.providers.provider_service')
    def test_create_provider_success(self, mock_service, mock_auth):
        """Test successful POST /providers"""
        # Mock authentication
        mock_auth.return_value = self.mock_user
        
        # Mock service response
        mock_service.create_provider.return_value = "test_provider_id"
        
        # Create request data
        request_data = {
            "name": "Test Provider",
            "code": "TEST_PROVIDER",
            "enabled": True,
            "ingestion_type": "UPLOAD",
            "data_type": "DEMAND",
            "fields": [
                {
                    "field_name": "time_block",
                    "field_type": "integer",
                    "is_required": True,
                    "validation": [
                        {
                            "rule": "min_value",
                            "value": 1
                        }
                    ]
                }
            ],
            "unique_columns": ["time_block"],
            "mapping": [
                {
                    "provider_field": "time_block",
                    "serve_field": "timeblock"
                }
            ],
            "description": "Test provider"
        }
        
        # Make request
        response = self.client.post("/providers", json=request_data)
        
        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["message"], "Provider created successfully")
        self.assertEqual(data["provider_id"], "test_provider_id")
        self.assertEqual(data["provider_code"], "TEST_PROVIDER")
        
        # Verify service was called with correct parameters
        mock_service.create_provider.assert_called_once()
        call_args = mock_service.create_provider.call_args
        self.assertIsInstance(call_args[1]["request"], CreateProviderRequest)
        self.assertEqual(call_args[1]["created_by"], "test_user")
    
    @patch('services.ingestion.app.api.v1.routes.providers.require_page_access')
    @patch('services.ingestion.app.api.v1.routes.providers.provider_service')
    def test_create_provider_validation_error(self, mock_service, mock_auth):
        """Test POST /providers with validation error"""
        # Mock authentication
        mock_auth.return_value = self.mock_user
        
        # Mock service to raise ValueError
        mock_service.create_provider.side_effect = ValueError("Provider code already exists")
        
        # Create request data
        request_data = {
            "name": "Test Provider",
            "code": "TEST_PROVIDER",
            "ingestion_type": "UPLOAD",
            "data_type": "DEMAND",
            "fields": []
        }
        
        # Make request
        response = self.client.post("/providers", json=request_data)
        
        # Assertions
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("already exists", data["detail"])
    
    @patch('services.ingestion.app.api.v1.routes.providers.require_page_access')
    @patch('services.ingestion.app.api.v1.routes.providers.provider_service')
    def test_update_provider_success(self, mock_service, mock_auth):
        """Test successful PUT /providers/{provider_id}"""
        # Mock authentication
        mock_auth.return_value = self.mock_user
        
        # Mock service response
        mock_service.update_provider.return_value = True
        
        # Create request data
        request_data = {
            "name": "Updated Provider",
            "enabled": False
        }
        
        # Make request
        response = self.client.put("/providers/test_id", json=request_data)
        
        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["message"], "Provider updated successfully")
        self.assertEqual(data["provider_id"], "test_id")
        
        # Verify service was called
        mock_service.update_provider.assert_called_once_with("test_id", unittest.mock.ANY)
    
    @patch('services.ingestion.app.api.v1.routes.providers.require_page_access')
    @patch('services.ingestion.app.api.v1.routes.providers.provider_service')
    def test_update_provider_not_found(self, mock_service, mock_auth):
        """Test PUT /providers/{provider_id} when not found"""
        # Mock authentication
        mock_auth.return_value = self.mock_user
        
        # Mock service response
        mock_service.update_provider.return_value = False
        
        # Create request data
        request_data = {"name": "Updated Provider"}
        
        # Make request
        response = self.client.put("/providers/nonexistent_id", json=request_data)
        
        # Assertions
        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertIn("not found", data["detail"])
    
    @patch('services.ingestion.app.api.v1.routes.providers.require_page_access')
    @patch('services.ingestion.app.api.v1.routes.providers.provider_service')
    def test_enable_disable_provider_success(self, mock_service, mock_auth):
        """Test successful PATCH /providers/{provider_id}/enable-disable"""
        # Mock authentication
        mock_auth.return_value = self.mock_user
        
        # Mock service response
        mock_service.enable_disable_provider.return_value = True
        
        # Create request data
        request_data = {"enabled": False}
        
        # Make request
        response = self.client.patch("/providers/test_id/enable-disable", json=request_data)
        
        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["message"], "Provider disabled successfully")
        self.assertEqual(data["provider_id"], "test_id")
        self.assertEqual(data["enabled"], False)
        
        # Verify service was called
        mock_service.enable_disable_provider.assert_called_once_with("test_id", False)
    
    @patch('services.ingestion.app.api.v1.routes.providers.require_page_access')
    @patch('services.ingestion.app.api.v1.routes.providers.provider_service')
    def test_delete_provider_success(self, mock_service, mock_auth):
        """Test successful DELETE /providers/{provider_id}"""
        # Mock authentication
        mock_auth.return_value = self.mock_user
        
        # Mock service response
        mock_service.delete_provider.return_value = True
        
        # Make request
        response = self.client.delete("/providers/test_id")
        
        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["message"], "Provider deleted successfully")
        self.assertEqual(data["provider_id"], "test_id")
        
        # Verify service was called
        mock_service.delete_provider.assert_called_once_with("test_id")
    
    @patch('services.ingestion.app.api.v1.routes.providers.require_page_access')
    @patch('services.ingestion.app.api.v1.routes.providers.provider_service')
    def test_delete_provider_not_found(self, mock_service, mock_auth):
        """Test DELETE /providers/{provider_id} when not found"""
        # Mock authentication
        mock_auth.return_value = self.mock_user
        
        # Mock service response
        mock_service.delete_provider.return_value = False
        
        # Make request
        response = self.client.delete("/providers/nonexistent_id")
        
        # Assertions
        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertIn("not found", data["detail"])
    
    @patch('services.ingestion.app.api.v1.routes.providers.require_page_access')
    @patch('services.ingestion.app.api.v1.routes.providers.provider_service')
    def test_get_provider_by_code_success(self, mock_service, mock_auth):
        """Test successful GET /providers/by-code/{code}"""
        # Mock authentication
        mock_auth.return_value = self.mock_user
        
        # Mock service response
        mock_provider = ProviderResponse(
            id="test_id",
            name="Test Provider",
            code="TEST_PROVIDER",
            enabled=True,
            ingestion_type=IngestionType.UPLOAD,
            data_type=DataType.DEMAND,
            schema=[],
            unique_columns=[],
            mapping=[],
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        
        mock_service.get_provider_by_code.return_value = mock_provider
        
        # Make request
        response = self.client.get("/providers/by-code/TEST_PROVIDER")
        
        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["code"], "TEST_PROVIDER")
        self.assertEqual(data["name"], "Test Provider")
    
    @patch('services.ingestion.app.api.v1.routes.providers.require_page_access')
    @patch('services.ingestion.app.api.v1.routes.providers.provider_service')
    def test_get_providers_by_ingestion_type(self, mock_service, mock_auth):
        """Test GET /providers/by-ingestion-type/{ingestion_type}"""
        # Mock authentication
        mock_auth.return_value = self.mock_user
        
        # Mock service response
        mock_providers = [
            ProviderResponse(
                id="test_id",
                name="Test Provider",
                code="TEST_PROVIDER",
                enabled=True,
                ingestion_type=IngestionType.UPLOAD,
                data_type=DataType.DEMAND,
                schema=[],
                unique_columns=[],
                mapping=[],
                created_at=datetime.now(),
                updated_at=datetime.now()
            )
        ]
        
        mock_service.get_providers_by_ingestion_type.return_value = mock_providers
        
        # Make request
        response = self.client.get("/providers/by-ingestion-type/UPLOAD")
        
        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["ingestion_type"], "UPLOAD")
    
    @patch('services.ingestion.app.api.v1.routes.providers.require_page_access')
    @patch('services.ingestion.app.api.v1.routes.providers.provider_service')
    def test_get_providers_by_data_type(self, mock_service, mock_auth):
        """Test GET /providers/by-data-type/{data_type}"""
        # Mock authentication
        mock_auth.return_value = self.mock_user
        
        # Mock service response
        mock_providers = [
            ProviderResponse(
                id="test_id",
                name="Test Provider",
                code="TEST_PROVIDER",
                enabled=True,
                ingestion_type=IngestionType.UPLOAD,
                data_type=DataType.DEMAND,
                schema=[],
                unique_columns=[],
                mapping=[],
                created_at=datetime.now(),
                updated_at=datetime.now()
            )
        ]
        
        mock_service.get_providers_by_data_type.return_value = mock_providers
        
        # Make request
        response = self.client.get("/providers/by-data-type/DEMAND")
        
        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["data_type"], "DEMAND")
    
    @patch('services.ingestion.app.api.v1.routes.providers.require_page_access')
    @patch('services.ingestion.app.api.v1.routes.providers.provider_service')
    def test_get_provider_serve_table_success(self, mock_service, mock_auth):
        """Test GET /providers/{provider_id}/serve-table"""
        # Mock authentication
        mock_auth.return_value = self.mock_user
        
        # Mock service response
        mock_service.get_serve_table_for_provider.return_value = "demand_serve"
        
        # Make request
        response = self.client.get("/providers/test_id/serve-table")
        
        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["provider_id"], "test_id")
        self.assertEqual(data["serve_table"], "demand_serve")
    
    @patch('services.ingestion.app.api.v1.routes.providers.require_page_access')
    def test_get_ingestion_types(self, mock_auth):
        """Test GET /providers/ingestion-types/list"""
        # Mock authentication
        mock_auth.return_value = self.mock_user
        
        # Make request
        response = self.client.get("/providers/ingestion-types/list")
        
        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 4)
        
        # Check that all expected ingestion types are present
        values = [item["value"] for item in data]
        self.assertIn("UPLOAD", values)
        self.assertIn("API", values)
        self.assertIn("FTP", values)
        self.assertIn("SCRAPE", values)
    
    @patch('services.ingestion.app.api.v1.routes.providers.require_page_access')
    def test_get_data_types(self, mock_auth):
        """Test GET /providers/data-types/list"""
        # Mock authentication
        mock_auth.return_value = self.mock_user
        
        # Make request
        response = self.client.get("/providers/data-types/list")
        
        # Assertions
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 4)
        
        # Check that all expected data types are present
        values = [item["value"] for item in data]
        self.assertIn("DEMAND", values)
        self.assertIn("SUPPLY", values)
        self.assertIn("EXCHANGE", values)
        self.assertIn("BID", values)
        
        # Check that serve tables are included
        for item in data:
            self.assertIn("serve_table", item)


if __name__ == '__main__':
    unittest.main()
