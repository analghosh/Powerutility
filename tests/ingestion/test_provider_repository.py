"""
Unit tests for Provider Repository
Tests provider repository functionality with complete mocking
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime
from bson import ObjectId

from libs.repository.provider_repository import ProviderRepository
from libs.models.provider_models import (
    ProviderEntity, IngestionType, DataType, SchemaField, FieldType,
    FieldMapping, FieldValidationRule, ValidationRuleType
)


class TestProviderRepository(unittest.TestCase):
    """Test cases for ProviderRepository"""
    
    def setUp(self):
        """Set up test fixtures"""
        with patch('libs.repository.provider_repository.BaseRepository'):
            self.repository = ProviderRepository("mongodb://test", "test_db")
            self.repository.collection = Mock()
            self.repository.db = Mock()
    
    def test_create_provider_success(self):
        """Test successful provider creation"""
        # Mock collection insert_one
        mock_result = Mock()
        mock_result.inserted_id = ObjectId()
        self.repository.collection.insert_one.return_value = mock_result
        
        # Create test provider
        provider = ProviderEntity(
            name="Test Provider",
            code="TEST_PROVIDER",
            enabled=True,
            ingestion_type=IngestionType.UPLOAD,
            data_type=DataType.DEMAND,
            schema=[],
            unique_columns=[],
            mapping=[]
        )
        
        # Call method
        result = self.repository.create_provider(provider)
        
        # Assertions
        self.assertIsInstance(result, str)
        self.repository.collection.insert_one.assert_called_once()
        
        # Verify the data passed to insert_one
        call_args = self.repository.collection.insert_one.call_args[0][0]
        self.assertEqual(call_args["name"], "Test Provider")
        self.assertEqual(call_args["code"], "TEST_PROVIDER")
        self.assertEqual(call_args["enabled"], True)
        self.assertEqual(call_args["ingestion_type"], "UPLOAD")
        self.assertEqual(call_args["data_type"], "DEMAND")
    
    def test_get_provider_by_id_success(self):
        """Test successful provider retrieval by ID"""
        # Mock collection find_one
        mock_doc = {
            "_id": ObjectId(),
            "name": "Test Provider",
            "code": "TEST_PROVIDER",
            "enabled": True,
            "ingestion_type": "UPLOAD",
            "data_type": "DEMAND",
            "fields": [],
            "unique_columns": [],
            "mapping": [],
            "created_at": datetime.now(),
            "updated_at": datetime.now()
        }
        self.repository.collection.find_one.return_value = mock_doc
        
        # Call method
        result = self.repository.get_provider_by_id(str(ObjectId()))
        
        # Assertions
        self.assertIsInstance(result, ProviderEntity)
        self.assertEqual(result.name, "Test Provider")
        self.assertEqual(result.code, "TEST_PROVIDER")
        self.assertEqual(result.enabled, True)
        self.assertEqual(result.ingestion_type, IngestionType.UPLOAD)
        self.assertEqual(result.data_type, DataType.DEMAND)
    
    def test_get_provider_by_id_not_found(self):
        """Test provider retrieval by ID when not found"""
        self.repository.collection.find_one.return_value = None
        
        # Call method
        result = self.repository.get_provider_by_id(str(ObjectId()))
        
        # Assertions
        self.assertIsNone(result)
    
    def test_get_provider_by_code_success(self):
        """Test successful provider retrieval by code"""
        # Mock collection find_one
        mock_doc = {
            "_id": ObjectId(),
            "name": "Test Provider",
            "code": "TEST_PROVIDER",
            "enabled": True,
            "ingestion_type": "UPLOAD",
            "data_type": "DEMAND",
            "fields": [],
            "unique_columns": [],
            "mapping": [],
            "created_at": datetime.now(),
            "updated_at": datetime.now()
        }
        self.repository.collection.find_one.return_value = mock_doc
        
        # Call method
        result = self.repository.get_provider_by_code("TEST_PROVIDER")
        
        # Assertions
        self.assertIsInstance(result, ProviderEntity)
        self.assertEqual(result.code, "TEST_PROVIDER")
        self.repository.collection.find_one.assert_called_once_with({"code": "TEST_PROVIDER"})
    
    def test_get_all_providers_success(self):
        """Test successful retrieval of all providers"""
        # Mock collection find
        mock_docs = [
            {
                "_id": ObjectId(),
                "name": "Test Provider 1",
                "code": "TEST_PROVIDER_1",
                "enabled": True,
                "ingestion_type": "UPLOAD",
                "data_type": "DEMAND",
                "fields": [],
                "unique_columns": [],
                "mapping": [],
                "created_at": datetime.now(),
                "updated_at": datetime.now()
            },
            {
                "_id": ObjectId(),
                "name": "Test Provider 2",
                "code": "TEST_PROVIDER_2",
                "enabled": False,
                "ingestion_type": "API",
                "data_type": "SUPPLY",
                "fields": [],
                "unique_columns": [],
                "mapping": [],
                "created_at": datetime.now(),
                "updated_at": datetime.now()
            }
        ]
        
        mock_cursor = Mock()
        mock_cursor.__iter__ = Mock(return_value=iter(mock_docs))
        self.repository.collection.find.return_value = mock_cursor
        
        # Call method
        result = self.repository.get_all_providers()
        
        # Assertions
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], ProviderEntity)
        self.assertIsInstance(result[1], ProviderEntity)
        self.assertEqual(result[0].name, "Test Provider 1")
        self.assertEqual(result[1].name, "Test Provider 2")
    
    def test_get_all_providers_with_filters(self):
        """Test retrieval of providers with filters"""
        # Mock collection find
        mock_cursor = Mock()
        mock_cursor.__iter__ = Mock(return_value=iter([]))
        self.repository.collection.find.return_value = mock_cursor
        
        # Call method with filters
        result = self.repository.get_all_providers(
            ingestion_type=IngestionType.UPLOAD,
            data_type=DataType.DEMAND,
            enabled=True,
            skip=10,
            limit=50
        )
        
        # Assertions
        self.repository.collection.find.assert_called_once()
        call_args = self.repository.collection.find.call_args
        
        # Verify query filters
        query = call_args[0][0]
        self.assertEqual(query["ingestion_type"], "UPLOAD")
        self.assertEqual(query["data_type"], "DEMAND")
        self.assertEqual(query["enabled"], True)
        
        # Verify pagination
        self.assertEqual(call_args[1]["skip"], 10)
        self.assertEqual(call_args[1]["limit"], 50)
    
    def test_count_providers_success(self):
        """Test successful provider counting"""
        self.repository.collection.count_documents.return_value = 5
        
        # Call method
        result = self.repository.count_providers()
        
        # Assertions
        self.assertEqual(result, 5)
        self.repository.collection.count_documents.assert_called_once_with({})
    
    def test_count_providers_with_filters(self):
        """Test provider counting with filters"""
        self.repository.collection.count_documents.return_value = 3
        
        # Call method with filters
        result = self.repository.count_providers(
            ingestion_type=IngestionType.UPLOAD,
            data_type=DataType.DEMAND,
            enabled=True
        )
        
        # Assertions
        self.assertEqual(result, 3)
        call_args = self.repository.collection.count_documents.call_args[0][0]
        self.assertEqual(call_args["ingestion_type"], "UPLOAD")
        self.assertEqual(call_args["data_type"], "DEMAND")
        self.assertEqual(call_args["enabled"], True)
    
    def test_update_provider_success(self):
        """Test successful provider update"""
        # Mock collection update_one
        mock_result = Mock()
        mock_result.modified_count = 1
        self.repository.collection.update_one.return_value = mock_result
        
        # Call method
        updates = {"name": "Updated Provider", "enabled": False}
        result = self.repository.update_provider("test_id", updates)
        
        # Assertions
        self.assertTrue(result)
        self.repository.collection.update_one.assert_called_once()
        
        # Verify the update call
        call_args = self.repository.collection.update_one.call_args
        self.assertEqual(call_args[0][0], {"_id": ObjectId("test_id")})
        self.assertIn("$set", call_args[0][1])
        self.assertEqual(call_args[0][1]["$set"]["name"], "Updated Provider")
        self.assertEqual(call_args[0][1]["$set"]["enabled"], False)
        self.assertIn("updated_at", call_args[0][1]["$set"])
    
    def test_update_provider_not_found(self):
        """Test provider update when provider not found"""
        # Mock collection update_one
        mock_result = Mock()
        mock_result.modified_count = 0
        self.repository.collection.update_one.return_value = mock_result
        
        # Call method
        updates = {"name": "Updated Provider"}
        result = self.repository.update_provider("test_id", updates)
        
        # Assertions
        self.assertFalse(result)
    
    def test_delete_provider_success(self):
        """Test successful provider deletion"""
        # Mock collection delete_one
        mock_result = Mock()
        mock_result.deleted_count = 1
        self.repository.collection.delete_one.return_value = mock_result
        
        # Call method
        result = self.repository.delete_provider("test_id")
        
        # Assertions
        self.assertTrue(result)
        self.repository.collection.delete_one.assert_called_once_with({"_id": ObjectId("test_id")})
    
    def test_delete_provider_not_found(self):
        """Test provider deletion when provider not found"""
        # Mock collection delete_one
        mock_result = Mock()
        mock_result.deleted_count = 0
        self.repository.collection.delete_one.return_value = mock_result
        
        # Call method
        result = self.repository.delete_provider("test_id")
        
        # Assertions
        self.assertFalse(result)
    
    def test_enable_disable_provider_success(self):
        """Test successful provider enable/disable"""
        # Mock update_provider method
        self.repository.update_provider = Mock(return_value=True)
        
        # Call method
        result = self.repository.enable_disable_provider("test_id", False)
        
        # Assertions
        self.assertTrue(result)
        self.repository.update_provider.assert_called_once_with("test_id", {"enabled": False})
    
    def test_get_providers_by_ingestion_type(self):
        """Test getting providers by ingestion type"""
        # Mock get_all_providers method
        self.repository.get_all_providers = Mock(return_value=[])
        
        # Call method
        result = self.repository.get_providers_by_ingestion_type(IngestionType.UPLOAD)
        
        # Assertions
        self.repository.get_all_providers.assert_called_once_with(ingestion_type=IngestionType.UPLOAD)
    
    def test_get_providers_by_data_type(self):
        """Test getting providers by data type"""
        # Mock get_all_providers method
        self.repository.get_all_providers = Mock(return_value=[])
        
        # Call method
        result = self.repository.get_providers_by_data_type(DataType.DEMAND)
        
        # Assertions
        self.repository.get_all_providers.assert_called_once_with(data_type=DataType.DEMAND)
    
    def test_get_enabled_providers(self):
        """Test getting enabled providers"""
        # Mock get_all_providers method
        self.repository.get_all_providers = Mock(return_value=[])
        
        # Call method
        result = self.repository.get_enabled_providers()
        
        # Assertions
        self.repository.get_all_providers.assert_called_once_with(enabled=True)
    
    def test_check_code_exists_true(self):
        """Test checking code existence when code exists"""
        self.repository.collection.count_documents.return_value = 1
        
        # Call method
        result = self.repository.check_code_exists("TEST_PROVIDER")
        
        # Assertions
        self.assertTrue(result)
        self.repository.collection.count_documents.assert_called_once_with({"code": "TEST_PROVIDER"})
    
    def test_check_code_exists_false(self):
        """Test checking code existence when code doesn't exist"""
        self.repository.collection.count_documents.return_value = 0
        
        # Call method
        result = self.repository.check_code_exists("TEST_PROVIDER")
        
        # Assertions
        self.assertFalse(result)
    
    def test_check_code_exists_with_exclude_id(self):
        """Test checking code existence with exclude ID"""
        self.repository.collection.count_documents.return_value = 0
        
        # Call method
        result = self.repository.check_code_exists("TEST_PROVIDER", exclude_id="exclude_id")
        
        # Assertions
        self.assertFalse(result)
        call_args = self.repository.collection.count_documents.call_args[0][0]
        self.assertEqual(call_args["code"], "TEST_PROVIDER")
        self.assertEqual(call_args["_id"], {"$ne": ObjectId("exclude_id")})


if __name__ == '__main__':
    unittest.main()
