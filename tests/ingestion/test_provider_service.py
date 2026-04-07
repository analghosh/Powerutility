"""
Unit tests for Provider Service
Tests provider management functionality with complete mocking
"""

import unittest
from unittest.mock import Mock, patch
from datetime import datetime

from services.ingestion.app.services.provider_service import ProviderService
from libs.models.provider_models import (
    CreateProviderRequest, UpdateProviderRequest, ProviderEntity, 
    ProviderResponse, ProviderSummary, IngestionType, DataType, SchemaField, FieldType,
    FieldMapping, FieldValidationRule, ValidationRuleType
)


class TestProviderService(unittest.TestCase):
    """Test cases for ProviderService"""
    
    def setUp(self):
        """Set up test fixtures"""
        with patch('services.ingestion.app.services.provider_service.ProviderRepository'):
            self.service = ProviderService()
            self.service.repository = Mock()
    
    def test_get_serve_table_for_data_type(self):
        """Test getting serve table for data type"""
        # Test demand data type
        serve_table = self.service.get_serve_table_for_data_type(DataType.DEMAND)
        self.assertEqual(serve_table, "demand_serve")
        
        # Test supply data type
        serve_table = self.service.get_serve_table_for_data_type(DataType.SUPPLY)
        self.assertEqual(serve_table, "supply_serve")
        
        # Test exchange data type
        serve_table = self.service.get_serve_table_for_data_type(DataType.EXCHANGE)
        self.assertEqual(serve_table, "exchange_serve")
        
        # Test bid data type
        serve_table = self.service.get_serve_table_for_data_type(DataType.BID)
        self.assertEqual(serve_table, "bid_serve")
    
    def test_create_provider_success(self):
        """Test successful provider creation"""
        # Mock repository methods
        self.service.repository.check_code_exists.return_value = False
        self.service.repository.create_provider.return_value = "test_provider_id"
        
        # Create test request
        request = CreateProviderRequest(
            name="Test Provider",
            code="TEST_PROVIDER",
            enabled=True,
            ingestion_type=IngestionType.UPLOAD,
            data_type=DataType.DEMAND,
            schema=[
                SchemaField(
                    field_name="time_block",
                    field_type=FieldType.INTEGER,
                    is_required=True,
                    validation=[
                        FieldValidationRule(
                            rule=ValidationRuleType.MIN_VALUE,
                            value=1
                        )
                    ]
                )
            ],
            unique_columns=["time_block"],
            mapping=[
                FieldMapping(
                    provider_field="time_block",
                    serve_field="timeblock"
                )
            ],
            description="Test provider"
        )
        
        # Call method
        result = self.service.create_provider(request, "test_user")
        
        # Assertions
        self.assertEqual(result, "test_provider_id")
        self.service.repository.check_code_exists.assert_called_once_with("TEST_PROVIDER")
        self.service.repository.create_provider.assert_called_once()
    
    def test_create_provider_duplicate_code(self):
        """Test provider creation with duplicate code"""
        # Mock repository to return duplicate code exists
        self.service.repository.check_code_exists.return_value = True
        
        request = CreateProviderRequest(
            name="Test Provider",
            code="TEST_PROVIDER",
            ingestion_type=IngestionType.UPLOAD,
            data_type=DataType.DEMAND,
            schema=[]
        )
        
        # Call method and expect ValueError
        with self.assertRaises(ValueError) as context:
            self.service.create_provider(request)
        
        self.assertIn("already exists", str(context.exception))
    
    def test_create_provider_empty_schema(self):
        """Test provider creation with empty schema"""
        request = CreateProviderRequest(
            name="Test Provider",
            code="TEST_PROVIDER",
            ingestion_type=IngestionType.UPLOAD,
            data_type=DataType.DEMAND,
            schema=[]
        )
        
        # Call method and expect ValueError
        with self.assertRaises(ValueError) as context:
            self.service.create_provider(request)
        
        self.assertIn("cannot be empty", str(context.exception))
    
    def test_create_provider_duplicate_field_names(self):
        """Test provider creation with duplicate field names"""
        request = CreateProviderRequest(
            name="Test Provider",
            code="TEST_PROVIDER",
            ingestion_type=IngestionType.UPLOAD,
            data_type=DataType.DEMAND,
            schema=[
                SchemaField(field_name="time_block", field_type=FieldType.INTEGER),
                SchemaField(field_name="time_block", field_type=FieldType.STRING)
            ]
        )
        
        # Call method and expect ValueError
        with self.assertRaises(ValueError) as context:
            self.service.create_provider(request)
        
        self.assertIn("Duplicate field name", str(context.exception))
    
    def test_create_provider_invalid_mapping(self):
        """Test provider creation with invalid field mapping"""
        request = CreateProviderRequest(
            name="Test Provider",
            code="TEST_PROVIDER",
            ingestion_type=IngestionType.UPLOAD,
            data_type=DataType.DEMAND,
            schema=[
                SchemaField(field_name="time_block", field_type=FieldType.INTEGER)
            ],
            mapping=[
                FieldMapping(
                    provider_field="invalid_field",
                    serve_field="timeblock"
                )
            ]
        )
        
        # Call method and expect ValueError
        with self.assertRaises(ValueError) as context:
            self.service.create_provider(request)
        
        self.assertIn("unknown field", str(context.exception))
    
    def test_get_provider_by_id_success(self):
        """Test successful provider retrieval by ID"""
        # Mock provider entity
        mock_provider = ProviderEntity(
            id="test_id",
            name="Test Provider",
            code="TEST_PROVIDER",
            enabled=True,
            ingestion_type=IngestionType.UPLOAD,
            data_type=DataType.DEMAND,
            schema=[],
            unique_columns=[],
            mapping=[]
        )
        
        self.service.repository.get_provider_by_id.return_value = mock_provider
        
        # Call method
        result = self.service.get_provider_by_id("test_id")
        
        # Assertions
        self.assertIsInstance(result, ProviderResponse)
        self.assertEqual(result.id, "test_id")
        self.assertEqual(result.name, "Test Provider")
        self.assertEqual(result.code, "TEST_PROVIDER")
        self.service.repository.get_provider_by_id.assert_called_once_with("test_id")
    
    def test_get_provider_by_id_not_found(self):
        """Test provider retrieval by ID when not found"""
        self.service.repository.get_provider_by_id.return_value = None
        
        # Call method
        result = self.service.get_provider_by_id("nonexistent_id")
        
        # Assertions
        self.assertIsNone(result)
    
    def test_get_all_providers_success(self):
        """Test successful retrieval of all providers"""
        # Mock provider entities
        mock_providers = [
            ProviderEntity(
                id="test_id_1",
                name="Test Provider 1",
                code="TEST_PROVIDER_1",
                enabled=True,
                ingestion_type=IngestionType.UPLOAD,
                data_type=DataType.DEMAND,
                schema=[],
                unique_columns=[],
                mapping=[]
            ),
            ProviderEntity(
                id="test_id_2",
                name="Test Provider 2",
                code="TEST_PROVIDER_2",
                enabled=False,
                ingestion_type=IngestionType.API,
                data_type=DataType.SUPPLY,
                schema=[],
                unique_columns=[],
                mapping=[]
            )
        ]
        
        self.service.repository.get_all_providers.return_value = mock_providers
        self.service.repository.count_providers.return_value = 2
        
        # Call method
        result = self.service.get_all_providers()
        
        # Assertions
        self.assertEqual(len(result.providers), 2)
        self.assertEqual(result.total, 2)
        self.assertIsInstance(result.providers[0], ProviderSummary)
        self.assertIsInstance(result.providers[1], ProviderSummary)
    
    def test_update_provider_success(self):
        """Test successful provider update"""
        # Mock existing provider
        mock_provider = ProviderEntity(
            id="test_id",
            name="Test Provider",
            code="TEST_PROVIDER",
            enabled=True,
            ingestion_type=IngestionType.UPLOAD,
            data_type=DataType.DEMAND,
            schema=[],
            unique_columns=[],
            mapping=[]
        )
        
        self.service.repository.get_provider_by_id.return_value = mock_provider
        self.service.repository.check_code_exists.return_value = False
        self.service.repository.update_provider.return_value = True
        
        # Create update request
        request = UpdateProviderRequest(
            name="Updated Provider",
            enabled=False
        )
        
        # Call method
        result = self.service.update_provider("test_id", request)
        
        # Assertions
        self.assertTrue(result)
        self.service.repository.update_provider.assert_called_once()
    
    def test_update_provider_not_found(self):
        """Test provider update when provider not found"""
        self.service.repository.get_provider_by_id.return_value = None
        
        request = UpdateProviderRequest(name="Updated Provider")
        
        # Call method and expect ValueError
        with self.assertRaises(ValueError) as context:
            self.service.update_provider("nonexistent_id", request)
        
        self.assertIn("not found", str(context.exception))
    
    def test_update_provider_duplicate_code(self):
        """Test provider update with duplicate code"""
        mock_provider = ProviderEntity(
            id="test_id",
            name="Test Provider",
            code="TEST_PROVIDER",
            enabled=True,
            ingestion_type=IngestionType.UPLOAD,
            data_type=DataType.DEMAND,
            schema=[],
            unique_columns=[],
            mapping=[]
        )
        
        self.service.repository.get_provider_by_id.return_value = mock_provider
        self.service.repository.check_code_exists.return_value = True
        
        request = UpdateProviderRequest(code="DUPLICATE_CODE")
        
        # Call method and expect ValueError
        with self.assertRaises(ValueError) as context:
            self.service.update_provider("test_id", request)
        
        self.assertIn("already exists", str(context.exception))
    
    def test_delete_provider_success(self):
        """Test successful provider deletion"""
        mock_provider = ProviderEntity(
            id="test_id",
            name="Test Provider",
            code="TEST_PROVIDER",
            enabled=True,
            ingestion_type=IngestionType.UPLOAD,
            data_type=DataType.DEMAND,
            schema=[],
            unique_columns=[],
            mapping=[]
        )
        
        self.service.repository.get_provider_by_id.return_value = mock_provider
        self.service.repository.delete_provider.return_value = True
        
        # Call method
        result = self.service.delete_provider("test_id")
        
        # Assertions
        self.assertTrue(result)
        self.service.repository.delete_provider.assert_called_once_with("test_id")
    
    def test_delete_provider_not_found(self):
        """Test provider deletion when provider not found"""
        self.service.repository.get_provider_by_id.return_value = None
        
        # Call method and expect ValueError
        with self.assertRaises(ValueError) as context:
            self.service.delete_provider("nonexistent_id")
        
        self.assertIn("not found", str(context.exception))
    
    def test_enable_disable_provider_success(self):
        """Test successful provider enable/disable"""
        mock_provider = ProviderEntity(
            id="test_id",
            name="Test Provider",
            code="TEST_PROVIDER",
            enabled=True,
            ingestion_type=IngestionType.UPLOAD,
            data_type=DataType.DEMAND,
            schema=[],
            unique_columns=[],
            mapping=[]
        )
        
        self.service.repository.get_provider_by_id.return_value = mock_provider
        self.service.repository.enable_disable_provider.return_value = True
        
        # Call method
        result = self.service.enable_disable_provider("test_id", False)
        
        # Assertions
        self.assertTrue(result)
        self.service.repository.enable_disable_provider.assert_called_once_with("test_id", False)
    
    def test_enable_disable_provider_not_found(self):
        """Test provider enable/disable when provider not found"""
        self.service.repository.get_provider_by_id.return_value = None
        
        # Call method and expect ValueError
        with self.assertRaises(ValueError) as context:
            self.service.enable_disable_provider("nonexistent_id", False)
        
        self.assertIn("not found", str(context.exception))
    
    def test_get_serve_table_for_provider_success(self):
        """Test getting serve table for provider"""
        mock_provider = ProviderEntity(
            id="test_id",
            name="Test Provider",
            code="TEST_PROVIDER",
            enabled=True,
            ingestion_type=IngestionType.UPLOAD,
            data_type=DataType.DEMAND,
            schema=[],
            unique_columns=[],
            mapping=[]
        )
        
        self.service.repository.get_provider_by_id.return_value = mock_provider
        
        # Call method
        result = self.service.get_serve_table_for_data_type(DataType.DEMAND)
        
        # Assertions
        self.assertEqual(result, "demand_serve")
    
    def test_get_serve_table_for_provider_not_found(self):
        """Test getting serve table for non-existent provider"""
        self.service.repository.get_provider_by_id.return_value = None
        
        # Call method
        result = self.service.get_serve_table_for_data_type(DataType.SUPPLY)
        
        # Assertions
        self.assertIsNone(result)
    
    def test_validate_schema_empty(self):
        """Test schema validation with empty schema"""
        with self.assertRaises(ValueError) as context:
            self.service._validate_schema([])
        
        self.assertIn("cannot be empty", str(context.exception))
    
    def test_validate_schema_duplicate_fields(self):
        """Test schema validation with duplicate field names"""
        schema = [
            SchemaField(field_name="time_block", field_type=FieldType.INTEGER),
            SchemaField(field_name="time_block", field_type=FieldType.STRING)
        ]
        
        with self.assertRaises(ValueError) as context:
            self.service._validate_schema(schema)
        
        self.assertIn("Duplicate field name", str(context.exception))
    
    def test_validate_field_mappings_invalid_field(self):
        """Test field mapping validation with invalid field"""
        schema = [SchemaField(field_name="time_block", field_type=FieldType.INTEGER)]
        mappings = [
            FieldMapping(provider_field="invalid_field", serve_field="timeblock")
        ]
        
        with self.assertRaises(ValueError) as context:
            self.service._validate_field_mappings(schema, mappings, DataType.DEMAND)
        
        self.assertIn("unknown field", str(context.exception))
    
    def test_entity_to_response_conversion(self):
        """Test conversion from entity to response"""
        entity = ProviderEntity(
            id="test_id",
            name="Test Provider",
            code="TEST_PROVIDER",
            enabled=True,
            ingestion_type=IngestionType.UPLOAD,
            data_type=DataType.DEMAND,
            schema=[],
            unique_columns=[],
            mapping=[],
            description="Test description",
            version="1.0.0",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            created_by="test_user"
        )
        
        # Call method
        result = self.service._entity_to_response(entity)
        
        # Assertions
        self.assertIsInstance(result, ProviderResponse)
        self.assertEqual(result.id, "test_id")
        self.assertEqual(result.name, "Test Provider")
        self.assertEqual(result.code, "TEST_PROVIDER")
        self.assertEqual(result.enabled, True)
        self.assertEqual(result.ingestion_type, IngestionType.UPLOAD)
        self.assertEqual(result.data_type, DataType.DEMAND)
        self.assertEqual(result.description, "Test description")
        self.assertEqual(result.version, "1.0.0")
        self.assertEqual(result.created_by, "test_user")
    
    def test_entity_to_summary_conversion(self):
        """Test conversion from entity to summary"""
        entity = ProviderEntity(
            id="test_id",
            name="Test Provider",
            code="TEST_PROVIDER",
            enabled=True,
            ingestion_type=IngestionType.UPLOAD,
            data_type=DataType.DEMAND,
            schema=[],
            unique_columns=[],
            mapping=[],
            description="Test description",
            version="1.0.0",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            created_by="test_user"
        )
        
        # Call method
        result = self.service._entity_to_summary(entity)
        
        # Assertions
        self.assertIsInstance(result, ProviderSummary)
        self.assertEqual(result.id, "test_id")
        self.assertEqual(result.name, "Test Provider")
        self.assertEqual(result.code, "TEST_PROVIDER")
        self.assertEqual(result.enabled, True)
        # Summary should only include basic fields, not detailed ones
        self.assertFalse(hasattr(result, 'ingestion_type'))
        self.assertFalse(hasattr(result, 'data_type'))
        self.assertFalse(hasattr(result, 'description'))
        self.assertFalse(hasattr(result, 'schema'))
        self.assertFalse(hasattr(result, 'unique_columns'))
        self.assertFalse(hasattr(result, 'mapping'))
        self.assertFalse(hasattr(result, 'version'))
        self.assertFalse(hasattr(result, 'created_by'))
    
    def test_validate_provider_type_data_type_combination_success(self):
        """Test successful provider type and data type combination validation"""
        # Arrange
        provider_type = "TEST_DATA"
        data_type = "demand"
        
        mock_provider = ProviderEntity(
            id="test_id",
            name="Test Provider",
            code="TEST_DATA",
            enabled=True,
            ingestion_type=IngestionType.UPLOAD,
            data_type=DataType.DEMAND,
            schema=[],
            unique_columns=[],
            mapping=[]
        )
        
        self.mock_repository.get_provider_by_code_and_data_type.return_value = mock_provider
        
        # Act
        result = self.provider_service.validate_provider_type_data_type_combination(
            provider_type, data_type
        )
        
        # Assertions
        self.assertTrue(result["valid"])
        self.assertEqual(result["provider_id"], "test_id")
        self.assertEqual(result["provider_name"], "Test Provider")
        self.assertEqual(result["provider_type"], provider_type)
        self.assertEqual(result["data_type"], data_type)
        self.assertEqual(result["ingestion_type"], IngestionType.UPLOAD)
        self.mock_repository.get_provider_by_code_and_data_type.assert_called_once_with(
            provider_type, "DEMAND"
        )
    
    def test_validate_provider_type_data_type_combination_invalid_data_type(self):
        """Test validation with invalid data type"""
        # Arrange
        provider_type = "TEST_DATA"
        data_type = "invalid_type"
        
        # Act
        result = self.provider_service.validate_provider_type_data_type_combination(
            provider_type, data_type
        )
        
        # Assertions
        self.assertFalse(result["valid"])
        self.assertIn("Invalid data type", result["error"])
        self.assertEqual(result["provider_type"], provider_type)
        self.assertEqual(result["data_type"], data_type)
        self.mock_repository.get_provider_by_code_and_data_type.assert_not_called()
    
    def test_validate_provider_type_data_type_combination_provider_not_found(self):
        """Test validation when provider is not found"""
        # Arrange
        provider_type = "NONEXISTENT"
        data_type = "demand"
        
        self.mock_repository.get_provider_by_code_and_data_type.return_value = None
        
        # Act
        result = self.provider_service.validate_provider_type_data_type_combination(
            provider_type, data_type
        )
        
        # Assertions
        self.assertFalse(result["valid"])
        self.assertIn("No provider found", result["error"])
        self.assertEqual(result["provider_type"], provider_type)
        self.assertEqual(result["data_type"], data_type)
        self.mock_repository.get_provider_by_code_and_data_type.assert_called_once_with(
            provider_type, "DEMAND"
        )
    
    def test_validate_provider_type_data_type_combination_provider_disabled(self):
        """Test validation when provider is disabled"""
        # Arrange
        provider_type = "TEST_DATA"
        data_type = "demand"
        
        mock_provider = ProviderEntity(
            id="test_id",
            name="Test Provider",
            code="TEST_DATA",
            enabled=False,  # Disabled
            ingestion_type=IngestionType.UPLOAD,
            data_type=DataType.DEMAND,
            schema=[],
            unique_columns=[],
            mapping=[]
        )
        
        self.mock_repository.get_provider_by_code_and_data_type.return_value = mock_provider
        
        # Act
        result = self.provider_service.validate_provider_type_data_type_combination(
            provider_type, data_type
        )
        
        # Assertions
        self.assertFalse(result["valid"])
        self.assertIn("is disabled", result["error"])
        self.assertEqual(result["provider_id"], "test_id")
        self.assertEqual(result["provider_type"], provider_type)
        self.assertEqual(result["data_type"], data_type)
    
    def test_validate_provider_type_data_type_combination_wrong_ingestion_type(self):
        """Test validation when provider doesn't support UPLOAD ingestion type"""
        # Arrange
        provider_type = "API_DATA"
        data_type = "demand"
        
        mock_provider = ProviderEntity(
            id="test_id",
            name="API Provider",
            code="API_DATA",
            enabled=True,
            ingestion_type=IngestionType.API,  # Not UPLOAD
            data_type=DataType.DEMAND,
            schema=[],
            unique_columns=[],
            mapping=[]
        )
        
        self.mock_repository.get_provider_by_code_and_data_type.return_value = mock_provider
        
        # Act
        result = self.provider_service.validate_provider_type_data_type_combination(
            provider_type, data_type
        )
        
        # Assertions
        self.assertFalse(result["valid"])
        self.assertIn("does not support file upload", result["error"])
        self.assertEqual(result["ingestion_type"], IngestionType.API)
        self.assertEqual(result["provider_type"], provider_type)
        self.assertEqual(result["data_type"], data_type)
    
    def test_validate_provider_type_data_type_combination_exception(self):
        """Test validation when an exception occurs"""
        # Arrange
        provider_type = "TEST_DATA"
        data_type = "demand"
        
        self.mock_repository.get_provider_by_code_and_data_type.side_effect = Exception("Database error")
        
        # Act
        result = self.provider_service.validate_provider_type_data_type_combination(
            provider_type, data_type
        )
        
        # Assertions
        self.assertFalse(result["valid"])
        self.assertIn("Validation error", result["error"])
        self.assertEqual(result["provider_type"], provider_type)
        self.assertEqual(result["data_type"], data_type)


if __name__ == '__main__':
    unittest.main()
