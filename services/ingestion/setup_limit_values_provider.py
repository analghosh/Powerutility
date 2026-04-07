#!/usr/bin/env python3
"""
Setup script to create limit_values provider configuration in the database.
This script creates the necessary provider configuration for limit_values file uploads.

Usage:
    python setup_limit_values_provider.py

The script will create a provider with the following configuration:
- Code: limit_values_upload_provider
- Data Type: LIMIT_VALUES
- Ingestion Type: UPLOAD
- All required field mappings for limit values
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Any

from app.core.config import settings
from app.services.provider_service import ProviderManagementService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def create_limit_values_provider():
    """Create limit_values provider configuration"""
    
    provider_service = ProviderManagementService()
    
    # Provider configuration for limit_values
    provider_config = {
        "code": "limit_values_upload_provider",
        "name": "Limit Values Upload Data Provider",
        "description": "Provider for uploading limit values data through file upload with approval workflow",
        "data_type": "LIMIT_VALUES",
        "ingestion_type": "UPLOAD",
        "enabled": True,
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
        "fields": [
            {
                "field_name": "scheduled_at",
                "field_type": "string",
                "is_required": True,
                "description": "Date in YYYY-MM-DD format"
            },
            {
                "field_name": "block",
                "field_type": "integer",
                "is_required": True,
                "description": "Time block (1-96 for 15-minute intervals)"
            },
            {
                "field_name": "id_type",
                "field_type": "string",
                "is_required": True,
                "description": "Type of identifier (PLANT, ZONE, STATE, REGION)"
            },
            {
                "field_name": "values",
                "field_type": "float",
                "is_required": False,
                "description": "Limit value in MW (required for UPSERT action)"
            },
            {
                "field_name": "market",
                "field_type": "string",
                "is_required": False,
                "description": "Market type (default: RTM)"
            },
            {
                "field_name": "category",
                "field_type": "string",
                "is_required": False,
                "description": "Category (default: Limits)"
            },
            {
                "field_name": "area",
                "field_type": "string",
                "is_required": False,
                "description": "Area identifier"
            },
            {
                "field_name": "unit",
                "field_type": "string",
                "is_required": False,
                "description": "Unit (default: MW)"
            },
            {
                "field_name": "action",
                "field_type": "string",
                "is_required": False,
                "description": "Action type: UPSERT or DELETE (default: UPSERT)"
            },
            {
                "field_name": "comment",
                "field_type": "string",
                "is_required": False,
                "description": "Optional comment"
            }
        ],
        "mapping": [
            {
                "provider_field": "scheduled_at",
                "serve_field": "scheduled_at"
            },
            {
                "provider_field": "block",
                "serve_field": "block"
            },
            {
                "provider_field": "id_type",
                "serve_field": "id_type"
            },
            {
                "provider_field": "values",
                "serve_field": "values"
            },
            {
                "provider_field": "market",
                "serve_field": "market"
            },
            {
                "provider_field": "category",
                "serve_field": "category"
            },
            {
                "provider_field": "area",
                "serve_field": "area"
            },
            {
                "provider_field": "unit",
                "serve_field": "unit"
            },
            {
                "provider_field": "action",
                "serve_field": "action"
            },
            {
                "provider_field": "comment",
                "serve_field": "comment"
            }
        ]
    }
    
    try:
        # Check if provider already exists
        existing_provider = await provider_service.get_provider_by_code_and_data_type(
            provider_config["code"], provider_config["data_type"]
        )
        
        if existing_provider:
            logger.info(f"Provider {provider_config['code']} already exists. Updating...")
            # Update existing provider
            updated_provider = await provider_service.update_provider(
                existing_provider["_id"], provider_config
            )
            logger.info(f"Successfully updated provider: {updated_provider['code']}")
        else:
            # Create new provider
            created_provider = await provider_service.create_provider(provider_config)
            logger.info(f"Successfully created provider: {created_provider['code']}")
        
        logger.info("Limit values provider configuration completed successfully!")
        
    except Exception as e:
        logger.error(f"Error creating/updating limit_values provider: {e}")
        raise


async def main():
    """Main function"""
    try:
        logger.info("Starting limit_values provider setup...")
        await create_limit_values_provider()
        logger.info("Limit values provider setup completed successfully!")
    except Exception as e:
        logger.error(f"Setup failed: {e}")
        return 1
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
