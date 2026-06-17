"""
Provider Management Models
Models for managing providers with different ingestion types and data types
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from bson import ObjectId
from pydantic import BaseModel, Field, ConfigDict, field_validator


class IngestionType(str, Enum):
    """Ingestion type enumeration"""
    UPLOAD = "upload"
    API = "api"
    API_V2 = "api-v2"
    FTP = "ftp"
    SCRAPE = "scrape"


class DataType(str, Enum):
    """Data type enumeration"""
    DEMAND = "demand"
    SUPPLY = "supply"
    EXCHANGE = "exchange"
    EXCHANGE_TRADE = "exchange_trade"
    BID = "bid"
    WEATHER = "weather"
    PLANTS = "plants"
    RENEWABLE = "renewable",
    LIMIT_VALUES = "limit_values",
    EXCHANGE_DSM = "exchange_dsm"


class FieldType(str, Enum):
    """Field type enumeration"""
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    DATETIME = "datetime"
    BOOLEAN = "boolean"
    DATE = "date"
    TIME = "time"
    OBJECT = "object"
    DICT = "dict"
    LIST = "list"
    ARRAY = "array"
    JSON = "json"


class ValidationRuleType(str, Enum):
    """Validation rule type enumeration"""
    REQUIRED = "required"
    MIN_LENGTH = "min_length"
    MAX_LENGTH = "max_length"
    MIN_VALUE = "min_value"
    MAX_VALUE = "max_value"
    PATTERN = "pattern"
    EMAIL = "email"
    URL = "url"
    CUSTOM = "custom"


class FieldValidationRule(BaseModel):
    """Individual field validation rule"""
    rule: ValidationRuleType = Field(..., description="Validation rule type")
    value: Any = Field(..., description="Rule value")
    message: Optional[str] = Field(None, description="Custom error message")
    
    @field_validator('rule', mode='before')
    @classmethod
    def validate_rule(cls, v):
        """Convert string to ValidationRuleType enum"""
        if isinstance(v, str):
            try:
                return ValidationRuleType(v.lower())
            except ValueError as exc:
                # Try to find a matching enum value
                for rule_type in ValidationRuleType:
                    if rule_type.value.lower() == v.lower():
                        return rule_type
                raise ValueError(f"Invalid rule: {v}. Valid rules: {[r.value for r in ValidationRuleType]}") from exc
        return v
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "rule": "required",
                "value": True,
                "message": "This field is required"
            }
        }
    )


class SchemaField(BaseModel):
    """Schema field definition"""
    field_name: str = Field(..., description="Field name")
    field_type: FieldType = Field(..., description="Field data type")
    is_required: bool = Field(default=False, description="Whether field is required")
    validation: List[FieldValidationRule] = Field(
        default_factory=list,
        description="Field validation rules"
    )
    description: Optional[str] = Field(None, description="Field description")
    default_value: Optional[Any] = Field(None, description="Default value if not provided")
    
    @field_validator('field_type', mode='before')
    @classmethod
    def validate_field_type(cls, v):
        """Convert string to FieldType enum"""
        if isinstance(v, str):
            try:
                return FieldType(v.lower())
            except ValueError as exc:
                # Try to find a matching enum value
                for field_type in FieldType:
                    if field_type.value.lower() == v.lower():
                        return field_type
                raise ValueError(f"Invalid field_type: {v}. Valid types: {[t.value for t in FieldType]}") from exc
        return v
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "field_name": "time_block",
                "field_type": "integer",
                "is_required": True,
                "validation": [
                    {"rule": "min_value", "value": 1, "message": "Time block must be at least 1"},
                    {"rule": "max_value", "value": 48, "message": "Time block cannot exceed 48"}
                ],
                "description": "Time block number (1-48)",
                "default_value": None
            }
        }
    )


class FieldMapping(BaseModel):
    """Field mapping between provider and serve fields"""
    provider_field: str = Field(..., description="Provider field name")
    serve_field: str = Field(..., description="Serve field name")
    transformation: Optional[str] = Field(None, description="Optional transformation rule")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "provider_field": "time_block",
                "serve_field": "time_block",
                "transformation": "uppercase"
            }
        }
    )


class ProviderEntity(BaseModel):
    """Provider entity for database storage"""
    model_config = ConfigDict(
        validate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={
            ObjectId: str,
            datetime: lambda v: v.isoformat()
        }
    )
    
    # MongoDB ID
    id: Optional[Any] = Field(default_factory=ObjectId, alias="_id")
    
    # Provider identification
    name: str = Field(..., description="Provider name")
    code: str = Field(..., description="Unique provider code")
    enabled: bool = Field(default=True, description="Whether provider is enabled")
    
    # Data configuration
    ingestion_type: IngestionType = Field(..., description="Ingestion type")
    data_type: DataType = Field(..., description="Data type")
    
    # Schema definition
    fields: List[SchemaField] = Field(..., description="Provider schema fields")
    unique_columns: List[str] = Field(
        default_factory=list,
        description="Unique column names for data validation"
    )
    
    # Field mapping
    mapping: List[FieldMapping] = Field(
        default_factory=list,
        description="Field mapping between provider and serve fields"
    )
    
    # Metadata
    description: Optional[str] = Field(None, description="Provider description")
    version: str = Field(default="1.0.0", description="Provider version")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Provider metadata")
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    created_by: Optional[str] = Field(None, description="User who created this provider")
    
    @field_validator('fields', mode='before')
    @classmethod
    def validate_fields(cls, v):
        """Validate and convert schema field"""
        if isinstance(v, list):
            result = []
            for item in v:
                if isinstance(item, dict):
                    result.append(SchemaField(**item))
                elif isinstance(item, SchemaField):
                    result.append(item)
                else:
                    # Try to convert unknown types to dict first, then to SchemaField
                    try:
                        if hasattr(item, 'model_dump'):
                            result.append(SchemaField(**item.model_dump()))
                        elif hasattr(item, 'dict'):
                            result.append(SchemaField(**item.dict()))
                        else:
                            # Last resort: try to convert to dict
                            result.append(SchemaField(**dict(item)))
                    except Exception as exc:
                        # If all else fails, raise a clear error
                        raise ValueError(f"Cannot convert {type(item)} to SchemaField. Expected dict or SchemaField instance.") from exc
            return result
        return v
    
    @field_validator('mapping', mode='before')
    @classmethod
    def validate_mapping(cls, v):
        """Validate and convert mapping field"""
        if isinstance(v, list):
            result = []
            for item in v:
                if isinstance(item, dict):
                    result.append(FieldMapping(**item))
                elif isinstance(item, FieldMapping):
                    result.append(item)
                else:
                    # Try to convert unknown types to dict first, then to FieldMapping
                    try:
                        if hasattr(item, 'model_dump'):
                            result.append(FieldMapping(**item.model_dump()))
                        elif hasattr(item, 'dict'):
                            result.append(FieldMapping(**item.dict()))
                        else:
                            # Last resort: try to convert to dict
                            result.append(FieldMapping(**dict(item)))
                    except Exception as exc:
                        # If all else fails, raise a clear error
                        raise ValueError(f"Cannot convert {type(item)} to FieldMapping. Expected dict or FieldMapping instance.") from exc
            return result
        return v
    
    @field_validator('ingestion_type', mode='before')
    @classmethod
    def validate_ingestion_type(cls, v):
        """Convert string to IngestionType enum"""
        if isinstance(v, str):
            try:
                return IngestionType(v.lower())
            except ValueError as exc:
                # Try to find a matching enum value
                for ingestion_type in IngestionType:
                    if ingestion_type.value.lower() == v.lower():
                        return ingestion_type
                raise ValueError(f"Invalid ingestion_type: {v}. Valid types: {[t.value for t in IngestionType]}") from exc
        return v
    
    @field_validator('data_type', mode='before')
    @classmethod
    def validate_data_type(cls, v):
        """Convert string to DataType enum"""
        if isinstance(v, str):
            try:
                return DataType(v.lower())
            except ValueError as exc:
                # Try to find a matching enum value
                for data_type in DataType:
                    if data_type.value.lower() == v.lower():
                        return data_type
                raise ValueError(f"Invalid data_type: {v}. Valid types: {[t.value for t in DataType]}") from exc
        return v
    
    def __init__(self, **data):
        """Initialize provider entity with uppercase code"""
        if 'code' in data and data['code']:
            data['code'] = data['code'].upper()
        super().__init__(**data)


class CreateProviderRequest(BaseModel):
    """Request model for creating a provider"""
    name: str = Field(..., description="Provider name")
    code: str = Field(..., description="Unique provider code")
    enabled: bool = Field(default=True, description="Whether provider is enabled")
    ingestion_type: IngestionType = Field(..., description="Ingestion type")
    data_type: DataType = Field(..., description="Data type")
    fields: List[SchemaField] = Field(..., description="Provider schema fields")
    unique_columns: List[str] = Field(
        default_factory=list,
        description="Unique column names for data validation"
    )
    mapping: List[FieldMapping] = Field(
        default_factory=list,
        description="Field mapping between provider and serve fields"
    )
    description: Optional[str] = Field(None, description="Provider description")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Provider metadata")
    
    def __init__(self, **data):
        """Initialize provider entity with uppercase code"""
        if 'code' in data and data['code']:
            data['code'] = data['code'].upper()
        super().__init__(**data)
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "SCADA Demand Data Provider",
                "code": "SCADA_DEMAND",
                "enabled": True,
                "ingestion_type": "upload",
                "data_type": "demand",
                "fields": [
                    {
                        "field_name": "time_block",
                        "field_type": "integer",
                        "is_required": True,
                        "validation": [
                            {"rule": "min_value", "value": 1, "message": "Time block must be at least 1"},
                            {"rule": "max_value", "value": 48, "message": "Time block cannot exceed 48"}
                        ],
                        "description": "Time block number (1-48)",
                        "default_value": None
                    },
                    {
                        "field_name": "schedule_date",
                        "field_type": "datetime",
                        "is_required": True,
                        "validation": [
                            {"rule": "required", "value": True, "message": "Schedule date is required"}
                        ],
                        "description": "Schedule date and time",
                        "default_value": None
                    },
                    {
                        "field_name": "actual_demand",
                        "field_type": "float",
                        "is_required": True,
                        "validation": [
                            {"rule": "required", "value": True, "message": "Actual demand is required"},
                            {"rule": "min_value", "value": 0, "message": "Demand must be non-negative"}
                        ],
                        "description": "Actual demand value in MW",
                        "default_value": None
                    },
                    {
                        "field_name": "forecast_demand",
                        "field_type": "float",
                        "is_required": False,
                        "validation": [
                            {"rule": "min_value", "value": 0, "message": "Forecast demand must be non-negative"}
                        ],
                        "description": "Forecast demand value in MW",
                        "default_value": 0.0
                    }
                ],
                "unique_columns": ["time_block", "schedule_date"],
                "mapping": [
                    {"provider_field": "time_block", "serve_field": "time_block", "transformation": None},
                    {"provider_field": "schedule_date", "serve_field": "schedule_date", "transformation": None},
                    {"provider_field": "actual_demand", "serve_field": "actual_demand", "transformation": None},
                    {"provider_field": "forecast_demand", "serve_field": "forecast_demand", "transformation": None}
                ],
                "description": "Provider for CSV uploads of demand data with time blocks and schedule dates",
                "metadata": {
                    "source_system": "scada",
                    "data_frequency": "hourly",
                    "retention_days": 90,
                    "quality_threshold": 0.95
                }
            }
        }
    )


class UpdateProviderRequest(BaseModel):
    """Request model for updating a provider"""
    name: Optional[str] = Field(None, description="Provider name")
    enabled: Optional[bool] = Field(None, description="Whether provider is enabled")
    ingestion_type: Optional[IngestionType] = Field(None, description="Ingestion type")
    data_type: Optional[DataType] = Field(None, description="Data type")
    fields: Optional[List[SchemaField]] = Field(None, description="Provider schema fields")
    unique_columns: Optional[List[str]] = Field(None, description="Unique column names")
    mapping: Optional[List[FieldMapping]] = Field(None, description="Field mapping")
    description: Optional[str] = Field(None, description="Provider description")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Provider metadata")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Updated CSV Demand Provider",
                "enabled": False,
                "description": "Updated provider description for testing purposes",
                "metadata": {
                    "source_system": "updated_scada",
                    "data_frequency": "hourly",
                    "retention_days": 120
                }
            }
        }
    )


class ProviderResponse(BaseModel):
    """Response model for provider data"""
    id: str = Field(..., description="Provider ID")
    name: str = Field(..., description="Provider name")
    code: str = Field(..., description="Provider code")
    enabled: bool = Field(..., description="Whether provider is enabled")
    ingestion_type: IngestionType = Field(..., description="Ingestion type")
    data_type: DataType = Field(..., description="Data type")
    fields: List[SchemaField] = Field(..., description="Provider schema fields")
    unique_columns: List[str] = Field(..., description="Unique column names")
    mapping: List[FieldMapping] = Field(..., description="Field mapping")
    description: Optional[str] = Field(None, description="Provider description")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Provider metadata")
    version: str = Field(..., description="Provider version")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    created_by: Optional[str] = Field(None, description="Creator user")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "507f1f77bcf86cd799439011",
                "name": "CSV Demand Provider",
                "code": "CSV_DEMAND",
                "enabled": True,
                "ingestion_type": "upload",
                "data_type": "demand",
                "fields": [
                    {
                        "field_name": "time_block",
                        "field_type": "integer",
                        "is_required": True,
                        "validation": [
                            {"rule": "min_value", "value": 1, "message": "Time block must be at least 1"},
                            {"rule": "max_value", "value": 48, "message": "Time block cannot exceed 48"}
                        ],
                        "description": "Time block number (1-48)",
                        "default_value": None
                    },
                    {
                        "field_name": "schedule_date",
                        "field_type": "datetime",
                        "is_required": True,
                        "validation": [
                            {"rule": "required", "value": True, "message": "Schedule date is required"}
                        ],
                        "description": "Schedule date and time",
                        "default_value": None
                    },
                    {
                        "field_name": "actual_demand",
                        "field_type": "float",
                        "is_required": True,
                        "validation": [
                            {"rule": "required", "value": True, "message": "Actual demand is required"},
                            {"rule": "min_value", "value": 0, "message": "Demand must be non-negative"}
                        ],
                        "description": "Actual demand value in MW",
                        "default_value": None
                    }
                ],
                "unique_columns": ["time_block", "schedule_date"],
                "mapping": [
                    {"provider_field": "time_block", "serve_field": "time_block", "transformation": None},
                    {"provider_field": "schedule_date", "serve_field": "schedule_date", "transformation": None},
                    {"provider_field": "actual_demand", "serve_field": "actual_demand", "transformation": None}
                ],
                "description": "Provider for CSV uploads of demand data with time blocks and schedule dates",
                "metadata": {
                    "source_system": "csv_upload",
                    "data_frequency": "daily",
                    "retention_days": 60,
                    "validation_rules": ["time_block_range", "demand_positive"]
                },
                "version": "1.0.0",
                "created_at": "2024-01-15T10:30:00Z",
                "updated_at": "2024-01-15T10:30:00Z",
                "created_by": "user123"
            }
        }
    )


class ProviderSummary(BaseModel):
    """Summary model for provider list"""
    id: str = Field(..., description="Provider ID")
    name: str = Field(..., description="Provider name")
    code: str = Field(..., description="Provider code")
    ingestion_type: IngestionType = Field(..., description="Ingestion type")    
    data_type: DataType = Field(..., description="Data type")
    enabled: bool = Field(..., description="Whether provider is enabled")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "507f1f77bcf86cd799439011",
                "name": "CSV Demand Provider",
                "code": "CSV_DEMAND",
                "enabled": True
            }
        }
    )


class ProviderListResponse(BaseModel):
    """Response model for provider list"""
    providers: List[ProviderSummary] = Field(..., description="List of providers")
    total: int = Field(..., description="Total number of providers")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "providers": [
                    {
                        "id": "507f1f77bcf86cd799439011",
                        "name": "CSV Demand Provider",
                        "code": "CSV_DEMAND",
                        "enabled": True
                    },
                    {
                        "id": "507f1f77bcf86cd799439012",
                        "name": "API Supply Provider",
                        "code": "API_SUPPLY",
                        "enabled": False
                    }
                ],
                "total": 2
            }
        }
    )


class EnableDisableRequest(BaseModel):
    """Request model for enabling/disabling provider"""
    enabled: bool = Field(..., description="Whether to enable or disable the provider")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "enabled": False
            }
        }
    )


class CreateProviderResponse(BaseModel):
    """Response model for provider creation"""
    message: str = Field(..., description="Success message")
    provider_id: str = Field(..., description="Created provider ID")
    provider_code: str = Field(..., description="Provider code")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Provider created successfully",
                "provider_id": "507f1f77bcf86cd799439011",
                "provider_code": "CSV_DEMAND"
            }
        }
    )


class UpdateProviderResponse(BaseModel):
    """Response model for provider update"""
    message: str = Field(..., description="Success message")
    provider_id: str = Field(..., description="Updated provider ID")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Provider updated successfully",
                "provider_id": "507f1f77bcf86cd799439011"
            }
        }
    )


class EnableDisableProviderResponse(BaseModel):
    """Response model for enable/disable provider"""
    message: str = Field(..., description="Success message")
    provider_id: str = Field(..., description="Provider ID")
    enabled: bool = Field(..., description="Current enabled status")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Provider disabled successfully",
                "provider_id": "507f1f77bcf86cd799439011",
                "enabled": False
            }
        }
    )


class DeleteProviderResponse(BaseModel):
    """Response model for provider deletion"""
    message: str = Field(..., description="Success message")
    provider_id: str = Field(..., description="Deleted provider ID")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Provider deleted successfully",
                "provider_id": "507f1f77bcf86cd799439011"
            }
        }
    )


class ProviderServeTableResponse(BaseModel):
    """Response model for provider serve table"""
    provider_id: str = Field(..., description="Provider ID")
    serve_table: str = Field(..., description="Serve table name")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "provider_id": "507f1f77bcf86cd799439011",
                "serve_table": "demand_serve"
            }
        }
    )


class IngestionTypeResponse(BaseModel):
    """Response model for ingestion type"""
    value: str = Field(..., description="Ingestion type value")
    description: str = Field(..., description="Ingestion type description")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "value": "UPLOAD",
                "description": "CSV file upload"
            }
        }
    )


class DataTypeResponse(BaseModel):
    """Response model for data type"""
    value: str = Field(..., description="Data type value")
    description: str = Field(..., description="Data type description")
    serve_table: str = Field(..., description="Target serve table name")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "value": "DEMAND",
                "description": "Demand data",
                "serve_table": "demand_serve"
            }
        }
    )


class DataTypeServeTableMapping(BaseModel):
    """Mapping between data types and serve tables"""
    data_type: DataType = Field(..., description="Data type")
    serve_table: str = Field(..., description="Target serve table name")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "data_type": "DEMAND",
                "serve_table": "demand_serve"
            }
        }
    )
