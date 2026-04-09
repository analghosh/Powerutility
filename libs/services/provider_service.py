"""
Provider Service
Business logic for provider management
"""

import logging
from typing import Any, Dict, List, Optional

from libs.models.provider_models import (
    ProviderEntity, CreateProviderRequest, UpdateProviderRequest,
    ProviderResponse, ProviderListResponse, ProviderSummary, IngestionType, DataType
)
from libs.repository.provider_repository import ProviderRepository


logger = logging.getLogger(__name__)


class ProviderService:
    """Service for managing providers"""

    def __init__(self, mongodb_uri: str, database_name: str):
        """Initialize the provider service"""
        self.repository = ProviderRepository(mongodb_uri, database_name)

        # Data type to serve table mapping
        self.data_type_serve_mapping = {
            DataType.DEMAND: "demand_serve",
            DataType.SUPPLY: "supply_serve",
            DataType.EXCHANGE: "exchange_serve",
            DataType.BID: "bid_serve",
            DataType.WEATHER: "weather_serve",
            DataType.PLANTS: "plant_master",
            DataType.RENEWABLE: "renewable_serve",
            DataType.EXCHANGE_TRADE: "exchange_trade_serve",
            DataType.EXCHANGE_DSM: "exchange_dsm_serve"
        }

        # Serve collection columns for each data type
        self.serve_collection_columns = {
            DataType.DEMAND: {
                "scheduled_at", "time_block", "type", "forecast", "actual", "schedule", "drawal",
                "frequency", "rostering", "remarks", "source_actual",
                "source_forecast", "source_rostering", "zone"
            },
            DataType.SUPPLY: {
                "scheduled_at", "time_block", "plant_id", "dc_sg", "volume", "fc_price",
                "px_price", "vc_price", "price_group",
                "tm", "multiplier", "ramping"
            },
            DataType.EXCHANGE: {
                "market_type", "px_name", "market", "time_block", "buy_bid", "sell_bid",
                "mcv", "mcp", "acp", "acv", "scheduled_at"
            },
            DataType.BID: {
                "scheduled_at", "time_block", "type", "bidded", "traded"
            },
            DataType.WEATHER: {
                "scheduled_at", "time", "rain", "relative_humidity_2m", "uv_index", "wind_speed", "temperature_2m","precipitation_probability"
            },
            DataType.PLANTS: {
                "plant_name", "c_s", "category", "technology", "price_group", "ramping",
                "tm", "fc_price", "px_price", "vc_price", "multiplier", "volume", "ppa", "plant_id"
            },
            DataType.RENEWABLE: {
                "scheduled_at", "time_block", "type", "plant_id", "category", "schedule_volume",
                "technology", "c_s"
            },
            DataType.EXCHANGE_TRADE: {
                "scheduled_at", "time_block", "market_type", "px_name", "total_buy_cleared_vol", "total_sell_cleared_vol",
                "price", "time_period"
            },
            DataType.EXCHANGE_DSM: {
                "scheduled_at", "time_block", "market_type", "px_name", "total_buy_cleared_vol", "total_sell_cleared_vol",
                "price"
            }
        }

    def get_serve_table_for_data_type(self, data_type: DataType) -> str:
        """Get the serve table name for a data type"""
        return self.data_type_serve_mapping.get(data_type, "unknown_serve")

    def get_serve_columns_for_data_type(self, data_type: DataType) -> List[str]:
        """Get the serve collection columns for a data type"""
        return sorted(list(self.serve_collection_columns.get(data_type, set())))

    async def get_provider_by_id(self, provider_id: str) -> Optional[ProviderResponse]:
        """Get provider by ID"""
        try:
            provider = await self.repository.get_provider_by_id(provider_id)
            if not provider:
                return None
            return self._entity_to_response(provider)
        except Exception as e:
            logger.error("Error getting provider by ID: %s", e)
            raise

    async def get_provider_by_code(self, code: str) -> Optional[ProviderEntity]:
        """Get provider by code (returns entity for internal use)"""
        try:
            return await self.repository.get_provider_by_code(code)
        except Exception as e:
            logger.error("Error getting provider by code: %s", e)
            raise

    async def get_provider_by_code_and_data_type(self, code: str, data_type: str) -> Optional[ProviderEntity]:
        """Get provider by code and data type (returns entity for internal use)"""
        try:
            return await self.repository.get_provider_by_code_and_data_type(code, data_type)
        except Exception as e:
            logger.error("Error getting provider by code and data type: %s", e)
            raise

    async def get_all_providers(
        self,
        ingestion_type: Optional[IngestionType] = None,
        data_type: Optional[DataType] = None,
        enabled: Optional[bool] = None,
        skip: int = 0,
        limit: int = 100
    ) -> ProviderListResponse:
        """Get all providers with optional filters"""
        try:
            providers = await self.repository.get_all_providers(
                ingestion_type=ingestion_type,
                data_type=data_type,
                enabled=enabled,
                skip=skip,
                limit=limit
            )

            total = await self.repository.count_providers(
                ingestion_type=ingestion_type,
                data_type=data_type,
                enabled=enabled
            )

            provider_summaries = [self._entity_to_summary(p) for p in providers]

            return ProviderListResponse(
                providers=provider_summaries,
                total=total
            )
        except Exception as e:
            logger.error("Error getting all providers: %s", e)
            raise

    async def get_enabled_providers(self) -> List[ProviderEntity]:
        """Get all enabled providers (returns entities for internal use)"""
        try:
            return await self.repository.get_enabled_providers()
        except Exception as e:
            logger.error("Error getting enabled providers: %s", e)
            raise

    def _entity_to_response(self, entity: ProviderEntity) -> ProviderResponse:
        """Convert ProviderEntity to ProviderResponse"""
        return ProviderResponse(
            id=str(entity.id),
            name=entity.name,
            code=entity.code,
            enabled=entity.enabled,
            ingestion_type=entity.ingestion_type,
            data_type=entity.data_type,
            fields=entity.fields,
            unique_columns=entity.unique_columns,
            mapping=entity.mapping,
            description=entity.description,
            metadata=entity.metadata,
            version=entity.version,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            created_by=entity.created_by
        )

    def _entity_to_summary(self, entity: ProviderEntity) -> ProviderSummary:
        """Convert ProviderEntity to ProviderSummary"""
        return ProviderSummary(
            id=str(entity.id),
            name=entity.name,
            code=entity.code,
            data_type=entity.data_type,
            ingestion_type=entity.ingestion_type,
            enabled=entity.enabled
        )
