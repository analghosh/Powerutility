"""
Centralized Test Data Schema Definitions
Single source of truth for all test data provider schemas
"""

from typing import Dict, List, Any
from enum import Enum


class DataType(str, Enum):
    """Data type enumeration"""
    DEMAND = "demand"
    SUPPLY = "supply"


class TestDataSchemas:
    """Centralized test data schema definitions"""
    
    # Demand Data Schema (26 fields)
    DEMAND_SCHEMA: Dict[str, str] = {
        "time_block": "integer",
        "schedule_date": "datetime",
        "type": "string",
        "zone": "string",
        "Actual": "float",
        "Drawal": "float",
        "MAPE": "float",
        "MPE": "float",
        "Raw_Frequency": "float",
        "Schedule": "float",
        "comp_act_demand": "float",
        "created_at": "datetime",
        "dsm_rate": "float",
        "forecast": "float",
        "forecasted_frequency": "float",
        "inserted_at": "datetime",
        "net_dsm_amount": "float",
        "remarks": "string",
        "rostering": "float",
        "source_actual": "string",
        "source_forecast": "string",
        "source_rostering": "string",
        "udm": "float",
        "updated_at": "datetime",
        "updated_flag": "string",
        "comp_udm": "float"
    }
    
    # Supply Data Schema (12 fields)
    SUPPLY_SCHEMA: Dict[str, str] = {
        "scheduled_at": "datetime",
        "time_block": "integer",
        "plant_name": "string",
        "dc_sg": "string",
        "volume": "float",
        "fc_price": "float",
        "px_price": "float",
        "vc_price": "float",
        "category": "string",
        "technology": "string",
        "price_group": "string",
        "c_s": "string"
    }
    
    # Required fields for demand data
    DEMAND_REQUIRED_FIELDS: List[str] = [
        "time_block",
        "schedule_date", 
        "type",
        "zone",
        "Actual"
    ]
    
    # Required fields for supply data
    SUPPLY_REQUIRED_FIELDS: List[str] = [
        "scheduled_at",
        "time_block",
        "plant_name",
        "volume"
    ]
    
    # Validation rules for demand data
    DEMAND_VALIDATION_RULES: Dict[str, List[Dict[str, Any]]] = {
        "time_block": [
            {"rule": "required", "value": True},
            {"rule": "min_value", "value": 1},
            {"rule": "max_value", "value": 48}
        ],
        "Actual": [
            {"rule": "required", "value": True},
            {"rule": "min_value", "value": 0}
        ],
        "Drawal": [
            {"rule": "min_value", "value": 0}
        ],
        "MAPE": [
            {"rule": "min_value", "value": 0},
            {"rule": "max_value", "value": 100}
        ],
        "MPE": [
            {"rule": "min_value", "value": -100},
            {"rule": "max_value", "value": 100}
        ],
        "Raw_Frequency": [
            {"rule": "min_value", "value": 45.0},
            {"rule": "max_value", "value": 55.0}
        ],
        "dsm_rate": [
            {"rule": "min_value", "value": 0},
            {"rule": "max_value", "value": 1}
        ]
    }
    
    # Validation rules for supply data
    SUPPLY_VALIDATION_RULES: Dict[str, List[Dict[str, Any]]] = {
        "time_block": [
            {"rule": "required", "value": True},
            {"rule": "min_value", "value": 1},
            {"rule": "max_value", "value": 48}
        ],
        "volume": [
            {"rule": "required", "value": True},
            {"rule": "min_value", "value": 0}
        ],
        "fc_price": [
            {"rule": "min_value", "value": 0}
        ],
        "px_price": [
            {"rule": "min_value", "value": 0}
        ],
        "vc_price": [
            {"rule": "min_value", "value": 0}
        ]
    }
    
    @classmethod
    def get_schema(cls, data_type: DataType) -> Dict[str, str]:
        """
        Get schema definition for the specified data type
        
        Args:
            data_type: Type of data (demand or supply)
            
        Returns:
            Dict[str, str]: Schema definition mapping field names to types
        """
        if data_type == DataType.DEMAND:
            return cls.DEMAND_SCHEMA.copy()
        elif data_type == DataType.SUPPLY:
            return cls.SUPPLY_SCHEMA.copy()
        else:
            raise ValueError(f"Unsupported data type: {data_type}")
    
    @classmethod
    def get_required_fields(cls, data_type: DataType) -> List[str]:
        """
        Get required fields for the specified data type
        
        Args:
            data_type: Type of data (demand or supply)
            
        Returns:
            List[str]: List of required field names
        """
        if data_type == DataType.DEMAND:
            return cls.DEMAND_REQUIRED_FIELDS.copy()
        elif data_type == DataType.SUPPLY:
            return cls.SUPPLY_REQUIRED_FIELDS.copy()
        else:
            raise ValueError(f"Unsupported data type: {data_type}")
    
    @classmethod
    def get_validation_rules(cls, data_type: DataType) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get validation rules for the specified data type
        
        Args:
            data_type: Type of data (demand or supply)
            
        Returns:
            Dict[str, List[Dict[str, Any]]]: Validation rules for each field
        """
        if data_type == DataType.DEMAND:
            return cls.DEMAND_VALIDATION_RULES.copy()
        elif data_type == DataType.SUPPLY:
            return cls.SUPPLY_VALIDATION_RULES.copy()
        else:
            raise ValueError(f"Unsupported data type: {data_type}")
    
    @classmethod
    def get_example_data(cls, data_type: DataType) -> Dict[str, Any]:
        """
        Get example data for the specified data type
        
        Args:
            data_type: Type of data (demand or supply)
            
        Returns:
            Dict[str, Any]: Example data record
        """
        if data_type == DataType.DEMAND:
            return {
                "time_block": 1,
                "schedule_date": "2025-01-15T10:00:00Z",
                "type": "IDF",
                "zone": "North Zone",
                "Actual": 150.5,
                "Drawal": 150.0,
                "MAPE": 2.5,
                "MPE": 1.2,
                "Raw_Frequency": 50.0,
                "Schedule": 148.0,
                "comp_act_demand": 150.5,
                "created_at": "2025-01-15T10:00:00Z",
                "dsm_rate": 0.05,
                "forecast": 148.0,
                "forecasted_frequency": 148.0,
                "inserted_at": "2025-01-15T10:00:00Z",
                "net_dsm_amount": 7.5,
                "remarks": "Test data upload",
                "rostering": 0.0,
                "source_actual": "CSV Upload",
                "source_forecast": "CSV Upload",
                "source_rostering": "CSV Upload",
                "udm": 150.5,
                "updated_at": "2025-01-15T10:00:00Z",
                "updated_flag": "UPDATED",
                "comp_udm": 0.0
            }
        elif data_type == DataType.SUPPLY:
            return {
                "scheduled_at": "2025-01-15T10:00:00Z",
                "time_block": 1,
                "plant_name": "Thermal A",
                "dc_sg": "DC",
                "volume": 500.0,
                "fc_price": 2.5,
                "px_price": 2.6,
                "vc_price": 2.4,
                "category": "Thermal",
                "technology": "Coal",
                "price_group": "A",
                "c_s": "Central"
            }
        else:
            raise ValueError(f"Unsupported data type: {data_type}")
