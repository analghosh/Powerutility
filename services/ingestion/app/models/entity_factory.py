"""
Entity Factory for Provider-Specific Raw Data Entities
Factory class to create appropriate entity instances based on provider and data type
"""

from typing import Type, Dict, Any
from .provider_raw_data_entities import (
    BaseRawDataEntity,
    TestDataDemandRawDataEntity,
    TestDataSupplyRawDataEntity,
    ScadaDataDemandRawDataEntity,
    ScadaDataSupplyRawDataEntity,
    ApiDataDemandRawDataEntity,
    ApiDataSupplyRawDataEntity,
    FtpDataDemandRawDataEntity,
    FtpDataSupplyRawDataEntity
)


class RawDataEntityFactory:
    """Factory class for creating provider-specific raw data entities"""
    
    # Mapping of provider_type + data_type to entity class
    ENTITY_MAPPING: Dict[str, Type[BaseRawDataEntity]] = {
        "test_data_demand": TestDataDemandRawDataEntity,
        "test_data_supply": TestDataSupplyRawDataEntity,
        "scada_data_demand": ScadaDataDemandRawDataEntity,
        "scada_data_supply": ScadaDataSupplyRawDataEntity,
        "api_data_demand": ApiDataDemandRawDataEntity,
        "api_data_supply": ApiDataSupplyRawDataEntity,
        "ftp_data_demand": FtpDataDemandRawDataEntity,
        "ftp_data_supply": FtpDataSupplyRawDataEntity,
    }
    
    @classmethod
    def get_entity_class(cls, provider_type: str, data_type: str) -> Type[BaseRawDataEntity]:
        """
        Get the appropriate entity class for the given provider and data type
        
        Args:
            provider_type: Type of data provider (test_data, scada_data, api_data, ftp_data)
            data_type: Type of data (demand, supply)
            
        Returns:
            Type[BaseRawDataEntity]: The appropriate entity class
            
        Raises:
            ValueError: If the combination is not supported
        """
        key = f"{provider_type}_{data_type}"
        
        if key not in cls.ENTITY_MAPPING:
            raise ValueError(f"Unsupported provider/data type combination: {provider_type}/{data_type}")
        
        return cls.ENTITY_MAPPING[key]
    
    @classmethod
    def create_entity(cls, provider_type: str, data_type: str, **kwargs) -> BaseRawDataEntity:
        """
        Create a new entity instance for the given provider and data type
        
        Args:
            provider_type: Type of data provider
            data_type: Type of data
            **kwargs: Additional arguments to pass to entity constructor
            
        Returns:
            BaseRawDataEntity: New entity instance
        """
        entity_class = cls.get_entity_class(provider_type, data_type)
        return entity_class(**kwargs)
    
    @classmethod
    def get_supported_combinations(cls) -> Dict[str, str]:
        """
        Get all supported provider/data type combinations
        
        Returns:
            Dict[str, str]: Mapping of combination key to description
        """
        return {
            "test_data_demand": "Test data demand entity",
            "test_data_supply": "Test data supply entity",
            "scada_data_demand": "SCADA data demand entity",
            "scada_data_supply": "SCADA data supply entity",
            "api_data_demand": "API data demand entity",
            "api_data_supply": "API data supply entity",
            "ftp_data_demand": "FTP data demand entity",
            "ftp_data_supply": "FTP data supply entity",
        }
    
    @classmethod
    def is_supported(cls, provider_type: str, data_type: str) -> bool:
        """
        Check if a provider/data type combination is supported
        
        Args:
            provider_type: Type of data provider
            data_type: Type of data
            
        Returns:
            bool: True if supported, False otherwise
        """
        key = f"{provider_type}_{data_type}"
        return key in cls.ENTITY_MAPPING
