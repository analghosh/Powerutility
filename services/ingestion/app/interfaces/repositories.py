"""
Repository Interfaces for Clean Architecture
Defines contracts for data access without implementation details
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from datetime import datetime

from libs.enum.raw_data_status import RawDataStatus


class IUploadTrackingRepository(ABC):
    """Interface for upload tracking repository"""
    
    @abstractmethod
    async def create_upload_record(
        self,
        upload_id: str,
        file_name: str,
        file_size: int,
        data_type: str,
        provider_type: str,
        status: RawDataStatus = RawDataStatus.PENDING
    ) -> str:
        """Create a new upload tracking record"""
        pass
    
    @abstractmethod
    async def get_upload_by_id(self, upload_id: str) -> Optional[Dict[str, Any]]:
        """Get upload record by ID"""
        pass
    
    @abstractmethod
    async def update_upload_status(
        self,
        upload_id: str,
        status: RawDataStatus,
        status_message: str = "",
        completed_at: Optional[datetime] = None,
        failed_at: Optional[datetime] = None
    ) -> bool:
        """Update upload status"""
        pass
    
    @abstractmethod
    async def get_pending_uploads(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get pending uploads for processing"""
        pass
    
    @abstractmethod
    async def get_uploads_by_status(
        self,
        status: RawDataStatus,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get uploads by status"""
        pass
    
    @abstractmethod
    async def get_uploads_paginated(
        self,
        skip: int = 0,
        limit: int = 20,
        status_filter: Optional[str] = None,
        sort_by: str = "created_at",
        sort_order: int = -1
    ) -> Dict[str, Any]:
        """Get paginated uploads with optional filtering and sorting"""
        pass


class IProviderRepository(ABC):
    """Interface for provider repository"""
    
    @abstractmethod
    async def get_provider_by_code_and_type(
        self,
        provider_code: str,
        data_type: str
    ) -> Optional[Dict[str, Any]]:
        """Get provider by code and data type"""
        pass
    
    @abstractmethod
    async def create_provider(self, provider_data: Dict[str, Any]) -> str:
        """Create a new provider"""
        pass
    
    @abstractmethod
    async def update_provider(
        self,
        provider_id: str,
        update_data: Dict[str, Any]
    ) -> bool:
        """Update provider"""
        pass
    
    @abstractmethod
    async def delete_provider(self, provider_id: str) -> bool:
        """Delete provider"""
        pass
    
    @abstractmethod
    async def get_providers_list(
        self,
        skip: int = 0,
        limit: int = 100,
        filters: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Get paginated providers list"""
        pass


class IFileStorageRepository(ABC):
    """Interface for file storage operations"""
    
    @abstractmethod
    async def save_file(
        self,
        file_content: bytes,
        file_name: str,
        upload_id: str
    ) -> str:
        """Save file and return file path"""
        pass
    
    @abstractmethod
    async def get_file_path(self, upload_id: str) -> Optional[str]:
        """Get file path by upload ID"""
        pass
    
    @abstractmethod
    async def delete_file(self, file_path: str) -> bool:
        """Delete file"""
        pass
    
    @abstractmethod
    async def file_exists(self, file_path: str) -> bool:
        """Check if file exists"""
        pass