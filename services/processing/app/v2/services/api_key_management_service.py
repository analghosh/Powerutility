"""
API Key Management Service
Business logic layer for API key management operations
"""
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from fastapi import HTTPException, status
import secrets
import string

from ..repository.api_key_repository import APIKeyRepository

logger = logging.getLogger(__name__)


class APIKeyManagementService:
    def __init__(self):
        self.repository = APIKeyRepository()

    def generate_api_key(self, prefix: str = "client", length: int = 16) -> str:
        """
        Generate a random API key with prefix
        
        Args:
            prefix: Prefix for the API key
            length: Length of random part
            
        Returns:
            Generated API key string
        """
        random_part = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(length))
        return f"{prefix}_{random_part}"

    def generate_secret(self, length: int = 32) -> str:
        """
        Generate a secure random secret key
        
        Args:
            length: Length of the secret (default: 32 bytes)
            
        Returns:
            URL-safe base64 encoded secret
        """
        return secrets.token_urlsafe(length)

    def create_api_key(self, name: str, description: Optional[str], api_key_prefix: Optional[str], created_by: str) -> Dict[str, Any]:
        """
        Create a new API key
        
        Args:
            name: Client/Company name
            description: Optional description
            api_key_prefix: Optional prefix for API key
            created_by: Username of admin creating the key
            
        Returns:
            Dictionary containing api_key, secret, and metadata
            
        Raises:
            HTTPException: If creation fails
        """
        try:
            # Generate API key and secret
            prefix = api_key_prefix or "client"
            api_key = self.generate_api_key(prefix, 16)
            secret = self.generate_secret(32)
            
            # Create in repository
            result = self.repository.create_api_key(
                api_key=api_key,
                secret=secret,
                name=name,
                description=description or f"API key for {name}",
                created_by=created_by
            )
            
            if not result:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to create API key (may already exist, try different prefix)"
                )
            
            # Return created key info (secret shown only once)
            return {
                "api_key": api_key,
                "secret": secret,
                "name": name,
                "description": description or f"API key for {name}",
                "is_active": True,
                "created_at": datetime.now(),
                "created_by": created_by
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error creating API key: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create API key: {str(e)}"
            )

    def list_api_keys(self, is_active: Optional[bool] = None) -> List[Dict[str, Any]]:
        """
        List all API keys (without secrets)
        
        Args:
            is_active: Optional filter by active status
            
        Returns:
            List of API key dictionaries (secrets excluded)
        """
        try:
            # Build filter
            filter_dict = {}
            if is_active is not None:
                filter_dict["is_active"] = is_active
            
            # Get keys from repository
            keys = self.repository.list_api_keys(filter=filter_dict)
            
            # Convert to response format (exclude secrets)
            result = []
            for key in keys:
                created_at = key.get("created_at")
                updated_at = key.get("updated_at")
                last_used_at = key.get("last_used_at")
                
                result.append({
                    "api_key": key.get("api_key"),
                    "name": key.get("name", ""),
                    "description": key.get("description", ""),
                    "is_active": key.get("is_active", False),
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "last_used_at": last_used_at,
                    "created_by": key.get("created_by")
                })
            
            return result
        except Exception as e:
            logger.error(f"Error listing API keys: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to list API keys: {str(e)}"
            )

    def get_api_key(self, api_key: str) -> Dict[str, Any]:
        """
        Get API key information (without secret)
        
        Args:
            api_key: The API key identifier
            
        Returns:
            API key dictionary (secret excluded)
            
        Raises:
            HTTPException: If key not found
        """
        try:
            key_doc = self.repository.find_by_api_key(api_key)
            
            if not key_doc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"API key not found: {api_key}"
                )
            
            created_at = key_doc.get("created_at")
            updated_at = key_doc.get("updated_at")
            last_used_at = key_doc.get("last_used_at")
            
            return {
                "api_key": key_doc.get("api_key"),
                "name": key_doc.get("name", ""),
                "description": key_doc.get("description", ""),
                "is_active": key_doc.get("is_active", False),
                "created_at": created_at,
                "updated_at": updated_at,
                "last_used_at": last_used_at,
                "created_by": key_doc.get("created_by")
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting API key: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get API key: {str(e)}"
            )

    def activate_api_key(self, api_key: str) -> Dict[str, Any]:
        """
        Activate an API key
        
        Args:
            api_key: The API key identifier
            
        Returns:
            Dictionary with api_key, is_active, and message
            
        Raises:
            HTTPException: If key not found or activation fails
        """
        try:
            # Check if key exists (include inactive keys since we're activating)
            key_doc = self.repository.find_by_api_key(api_key, include_inactive=True)
            if not key_doc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"API key not found: {api_key}"
                )
            
            # Activate the key
            success = self.repository.update_api_key(api_key, {"is_active": True})
            
            if not success:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to activate API key"
                )
            
            return {
                "api_key": api_key,
                "is_active": True,
                "message": "API key activated successfully"
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error activating API key: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to activate API key: {str(e)}"
            )

    def deactivate_api_key(self, api_key: str) -> Dict[str, Any]:
        """
        Deactivate an API key (revoke access)
        
        Args:
            api_key: The API key identifier
            
        Returns:
            Dictionary with api_key, is_active, and message
            
        Raises:
            HTTPException: If key not found or deactivation fails
        """
        try:
            # Check if key exists (include inactive keys for consistency)
            key_doc = self.repository.find_by_api_key(api_key, include_inactive=True)
            if not key_doc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"API key not found: {api_key}"
                )
            
            # Deactivate the key (revoke)
            success = self.repository.revoke_api_key(api_key)
            
            if not success:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to deactivate API key"
                )
            
            return {
                "api_key": api_key,
                "is_active": False,
                "message": "API key deactivated successfully"
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error deactivating API key: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to deactivate API key: {str(e)}"
            )


# Singleton instance
api_key_management_service = APIKeyManagementService()