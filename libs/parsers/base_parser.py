from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from libs.models.provider_models import ProviderEntity


class BaseParser(ABC):
    """
    Base parser interface for different data formats
    """
    
    @abstractmethod
    def parse(self, content: str, provider: Optional[ProviderEntity] = None, **kwargs) -> List[Dict[str, Any]]:
        """
        Parse content into structured data
        
        Args:
            content: Raw content to parse
            provider: Provider entity for context
            **kwargs: Additional parsing parameters
            
        Returns:
            List[Dict[str, Any]]: Parsed data records
        """
        pass
    
    @abstractmethod
    def get_parser_type(self) -> str:
        """
        Get the parser type identifier
        
        Returns:
            str: Parser type identifier
        """
        pass
    
    def validate_content(self, content: str) -> bool:
        """
        Validate if content can be parsed by this parser
        
        Args:
            content: Raw content to validate
            
        Returns:
            bool: True if content can be parsed
        """
        return True  # Default implementation accepts all content
