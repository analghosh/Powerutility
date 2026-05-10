"""
Exchange entities
Defines data models for exchange serve and historical data with timeseries support
"""

from datetime import datetime, date
from typing import Dict, Any, List, Optional, Union
from bson import ObjectId
from pydantic import BaseModel, Field, ConfigDict
from libs.enum.updated_flag import UpdatedFlag


class ExchangeServeEntity(BaseModel):
    """
    Exchange Serve Entity - Active time series data (4-day window)
    
    This entity represents the processed exchange data that is actively served
    to the frontend applications. It contains a 4-day rolling window of data.
    """
    
    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={
            ObjectId: str,
            datetime: lambda v: v.isoformat(),
            date: lambda v: v.isoformat()
        }
    )
    
    # MongoDB ID
    id: Optional[Any] = Field(default_factory=ObjectId, alias="_id")
    
    # Time-related fields (for timeseries and 4-day window logic)
    scheduled_at: date = Field(..., description="Scheduled date for exchange data (stored as date)")
    time_block: int = Field(..., description="Time block identifier (1-96)")
    
    # Processing timestamps (for timeseries)
    inserted_at: datetime = Field(default_factory=datetime.now, description="Insertion timestamp")
    updated_at: datetime = Field(default_factory=datetime.now, description="Last update timestamp")
    created_at: datetime = Field(default_factory=datetime.now, description="Creation timestamp")
    timestamp: datetime = Field(default_factory=datetime.now, description="Time series indexing timestamp")
    
    # Market identification fields
    market_type: str = Field(..., description="Type of market data (IDF/DAF)")
    px_name: str = Field(default="", description="Power exchange name (RTM/DAM)")
    data_type: str = Field(default="exchange", description="Data type identifier")
    
    # Data tracking fields
    raw_data_id: Optional[str] = Field(None, description="Raw data document ID that created this record")
    
    # Update flag (for change tracking)
    updated_flag: UpdatedFlag = Field(default=UpdatedFlag.UPDATED, description="Flag indicating if data was updated")
    
    # Revision tracking
    source_revision_number: Optional[int] = Field(None, description="Revision number from source data")
    system_revision_number: int = Field(default=1, description="System revision counter (increments on each update)")
    
    # Exchange data fields
    buy_bid: Union[float, Dict[str, Any]] = Field(default=0.0, description="Purchase Bid (MW)")
    sell_bid: Union[float, Dict[str, Any]] = Field(default=0.0, description="Sell Bid (MW)")
    mcv: Union[float, Dict[str, Any]] = Field(default=0.0, description="MCV (MW)")
    mcp: Union[float, Dict[str, Any]] = Field(default=0.0, description="MCP (Rs/MWh)")
    acp: Union[float, Dict[str, Any]] = Field(default=0.0, description="ACP (Rs/MWh)")
    acv: Union[float, Dict[str, Any]] = Field(default=0.0, description="ACV (MW)")
    final_scheduled_volume: Union[float, Dict[str, Any]] = Field(default=0.0, description="Final Scheduled Volume (MW)")
    
    @classmethod
    def from_raw_data(cls, raw_data: dict, metadata: dict, provider_config: dict, 
                     market_type: Optional[str] = None, px_name: Optional[str] = None) -> "ExchangeServeEntity":
        """
        Create ExchangeServeEntity from a single raw exchange data record using provider mapping
        
        Args:
            raw_data: Single exchange record (should already be mapped by processor)
            metadata: Processing metadata
            provider_config: Provider configuration with mapping attribute
            market_type: Explicit market type (overrides provider config)
            px_name: Explicit PX name (overrides provider config)
            
        Returns:
            ExchangeServeEntity: Populated exchange entity
        """
        current_time = datetime.now()
        
        rawDataDetail = metadata.get("raw_data", {})
        created_at = rawDataDetail.get("created_at", current_time)
        
        # Get market type and px_name if not provided
        if not market_type:
            meta_data = provider_config.get("metadata", {})
            config = meta_data.get("config", {})
            market_type = config.get("market_type", "unknown").upper()
            
        if not px_name:
            meta_data = provider_config.get("metadata", {})
            px_name = meta_data.get("source", "unknown").upper()   
        
        # Parse time block (will be handled by processor using timeblock_master collection)
        time_block = raw_data.get("time_block", 0)  # Expect processed time_block number
        
        # Get scheduled date and convert to date object
        scheduled_at_raw = raw_data.get("scheduled_at") or raw_data.get("Date") or raw_data.get("date")
        if isinstance(scheduled_at_raw, datetime):
            scheduled_at = scheduled_at_raw.date()
        elif isinstance(scheduled_at_raw, date):
            scheduled_at = scheduled_at_raw
        else:
            scheduled_at = current_time.date()
        
        # Initialize entity data with default values
        entity_data = {
            "inserted_at": current_time,
            "created_at": created_at,
            "updated_at": current_time,
            "timestamp": current_time,
            "data_type": "exchange",
            "execution_id": metadata.get("execution_id"),
            "raw_data_id": rawDataDetail.get("raw_data_id"),
            "updated_flag": UpdatedFlag.UPDATED,  # Default to UPDATED for new records
            
            # Market identification
            "market_type": market_type,
            "px_name": px_name,
            "scheduled_at": scheduled_at,
            "time_block": time_block
        }
        
        # Map exchange data fields from raw_data (should already be mapped by processor)
        # This assumes the processor has already applied provider mapping
        exchange_fields = {
            "buy_bid": "buy_bid",
            "sell_bid": "sell_bid", 
            "mcv": "mcv",
            "mcp": "mcp",
            "acp": "acp",
            "acv": "acv",
            "final_scheduled_volume": "final_scheduled_volume"
        }
        
        # Initialize all exchange fields with default values first
        for entity_field in exchange_fields.keys():
            entity_data[entity_field] = 0.0
        
        # Then populate with actual values from raw_data
        for entity_field, raw_field in exchange_fields.items():
            if raw_field in raw_data:
                val = raw_data[raw_field]
                # If value is a dict, pass it through directly (assuming flexible schema)
                if isinstance(val, dict):
                    entity_data[entity_field] = val
                else:
                    entity_data[entity_field] = cls._safe_float(val)

        # Apply fallback logic for missing values
        if entity_data.get("acp", 0.0) == 0.0:
            entity_data["acp"] = entity_data.get("mcp", 0.0)

        if entity_data.get("acv", 0.0) == 0.0:
            entity_data["acv"] = entity_data.get("mcv", 0.0)

        
        
        return cls(**entity_data)

    @staticmethod
    def _safe_float(value: Any) -> float:
        """Safely convert value to float"""
        try:
            if value is None or value == "":
                return 0.0
            return float(str(value).replace(",", ""))
        except (ValueError, TypeError):
            return 0.0
    
    @staticmethod
    def _apply_transformation(value: Any, transformation: str) -> Any:
        """Apply transformation to field value"""
        if not transformation:
            return value
        
        transformation = transformation.lower()
        
        if transformation == "uppercase":
            return str(value).upper()
        elif transformation == "lowercase":
            return str(value).lower()
        elif transformation == "strip":
            return str(value).strip()
        elif transformation == "float":
            return ExchangeServeEntity._safe_float(value)
        elif transformation == "int":
            try:
                return int(float(str(value).replace(",", "")))
            except (ValueError, TypeError):
                return 0
        elif transformation == "timeblock_lookup":
            # For timeblock_lookup, we need to process the timeblock string
            # This should be handled by the processor's timeblock lookup logic
            # Return the value as-is for now, processor will handle the lookup
            return value
        else:
            return value
    
    def model_dump_for_db(self, provider_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Convert entity to dictionary for database storage with proper field mapping
        
        Args:
            provider_config: Provider configuration with field mapping
            
        Returns:
            Dictionary ready for database insertion
        """
        # Get the standard model dump
        data = self.model_dump(by_alias=True)
        
        # Apply reverse mapping if provider config has database field mapping
        if provider_config and "db_field_mapping" in provider_config:
            db_mapping = provider_config["db_field_mapping"]
            mapped_data = {}
            
            for entity_field, db_field in db_mapping.items():
                if entity_field in data:
                    mapped_data[db_field] = data[entity_field]
                else:
                    # Keep unmapped fields as-is
                    if entity_field not in db_mapping.values():
                        mapped_data[entity_field] = data.get(entity_field)
            
            # Ensure required fields are present
            for field in data:
                if field not in mapped_data and field not in db_mapping:
                    mapped_data[field] = data[field]
            
            return mapped_data
        
        return data
    
    @classmethod
    def validate_required_fields(cls, raw_data: Dict[str, Any], provider_config: Dict[str, Any]) -> List[str]:
        """
        Validate required fields before creating entity
        
        Args:
            raw_data: Raw data to validate
            provider_config: Provider configuration with field definitions
            
        Returns:
            List of validation error messages
        """
        errors = []
        
        # Get field definitions from provider config
        fields = provider_config.get("fields", [])
        
        for field_def in fields:
            field_name = field_def.get("field_name")
            is_required = field_def.get("is_required", False)
            validation_rules = field_def.get("validation", [])
            
            if is_required and (field_name not in raw_data or raw_data[field_name] is None):
                errors.append(f"Required field '{field_name}' is missing or null")
                continue
            
            # Apply validation rules
            if field_name in raw_data:
                field_value = raw_data[field_name]
                
                for rule in validation_rules:
                    rule_type = rule.get("rule")
                    rule_value = rule.get("value")
                    custom_message = rule.get("message")
                    
                    if rule_type == "required" and rule_value and field_value is None:
                        errors.append(custom_message or f"Field '{field_name}' is required")
                    elif rule_type == "min_value" and isinstance(field_value, (int, float)) and field_value < rule_value:
                        errors.append(custom_message or f"Field '{field_name}' must be at least {rule_value}")
                    elif rule_type == "max_value" and isinstance(field_value, (int, float)) and field_value > rule_value:
                        errors.append(custom_message or f"Field '{field_name}' must not exceed {rule_value}")
                    elif rule_type == "min_length" and isinstance(field_value, str) and len(field_value) < rule_value:
                        errors.append(custom_message or f"Field '{field_name}' must be at least {rule_value} characters")
                    elif rule_type == "max_length" and isinstance(field_value, str) and len(field_value) > rule_value:
                        errors.append(custom_message or f"Field '{field_name}' must not exceed {rule_value} characters")
        
        return errors


class ExchangeHistoricalEntity(BaseModel):
    """
    Exchange Historical Entity - Archived time series data
    
    This entity represents the historical exchange data that has been archived
    from the active exchange_serve collection. It preserves the original _id
    for tracking purposes.
    """
    
    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={
            ObjectId: str,
            datetime: lambda v: v.isoformat(),
            date: lambda v: v.isoforma
        }
    )

    # MongoDB ID (preserved from original)
    id: Optional[Any] = Field(default_factory=ObjectId, alias="_id")
    
    # All fields from ExchangeServeEntity
    scheduled_at: date = Field(..., description="Scheduled date for exchange data (stored as date)")
    time_block: int = Field(..., description="Time block identifier (1-96)")
    
    # Market identification fields
    market_type: str = Field(..., description="Type of market data (IDF/DAF)")
    px_name: str = Field(default="", description="Power exchange name (RTM/DAM)")
    market: str = Field(default="", description="Market identifier")
    data_type: str = Field(default="exchange", description="Data type identifier")
    execution_id: Optional[str] = Field(None, description="Job execution ID from audit_db")
    raw_data_id: Optional[str] = Field(None, description="Raw data document ID that created this record")
    
    # Update flag
    updated_flag: UpdatedFlag = Field(default=UpdatedFlag.UPDATED, description="Flag indicating if data was updated")
    
    # Revision tracking
    source_revision_number: Optional[int] = Field(None, description="Revision number from source data")
    system_revision_number: int = Field(default=1, description="System revision counter (increments on each update)")
    
    # Exchange data fields
    buy_bid: Union[float, Dict[str, Any]] = Field(default=0.0, description="Purchase Bid (MW)")
    sell_bid: Union[float, Dict[str, Any]] = Field(default=0.0, description="Sell Bid (MW)")
    mcv: Union[float, Dict[str, Any]] = Field(default=0.0, description="MCV (MW)")
    mcp: Union[float, Dict[str, Any]] = Field(default=0.0, description="MCP (Rs/MWh)")
    acp: Union[float, Dict[str, Any]] = Field(default=0.0, description="ACP (Rs/MWh)")
    acv: Union[float, Dict[str, Any]] = Field(default=0.0, description="ACV (MW)")
    final_scheduled_volume: Union[float, Dict[str, Any]] = Field(default=0.0, description="Final Scheduled Volume (MW)")
    
    # Original processing timestamps (preserved)
    inserted_at: datetime = Field(..., description="Original insertion timestamp")
    updated_at: datetime = Field(..., description="Original last update timestamp")
    created_at: datetime = Field(..., description="Original creation timestamp")
    timestamp: datetime = Field(..., description="Original time series indexing timestamp")
    
    # Archive-specific timestamps
    archived_at: datetime = Field(default_factory=datetime.now, description="Archive timestamp")
    archived_timestamp: datetime = Field(default_factory=datetime.now, description="Archive timestamp for time series indexing")

# Database Index Management
def ensure_exchange_serve_indexes(collection):
    """
    Ensure database indexes for exchange_serve collection
    
    Args:
        collection: MongoDB collection instance
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        # Primary time-based index (most common query)
        collection.create_index([("scheduled_at", 1)], background=True)
        
        # Time block index for time-based queries
        collection.create_index("time_block", background=True)
        
        # Exchange type index
        collection.create_index("exchange_type", background=True)
        
        # Market type index (DAM/RTM)
        collection.create_index("market_type", background=True)
        
        # Compound index for time + market type queries (very common)
        collection.create_index([
            ("scheduled_at", 1),
            ("market_type", 1)
        ], background=True)
        
        # Compound index for time + exchange type
        collection.create_index([
            ("scheduled_at", 1),
            ("exchange_type", 1)
        ], background=True)
        
        # Compound index for time block + market type
        collection.create_index([
            ("time_block", 1),
            ("market_type", 1)
        ], background=True)
        
        # Index for updated flag filtering
        collection.create_index("updated_flag", background=True)
        
        # Index for processing timestamps
        collection.create_index("inserted_at", background=True)
        collection.create_index("updated_at", background=True)
        
        logger.debug("✅ Exchange serve collection indexes ensured")
        
    except Exception as e:
        logger.warning(f"⚠️ Failed to create exchange serve indexes: {e}")

def ensure_exchange_serve_historical_indexes(collection):
    """
    Ensure database indexes for exchange_serve_historical collection
    
    Args:
        collection: MongoDB collection instance
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        # Primary time-based index for historical queries
        collection.create_index([("scheduled_at", 1)], background=True)
        
        # Archive timestamp index
        collection.create_index([("archived_at", -1)], background=True)
        collection.create_index([("archived_timestamp", -1)], background=True)
        
        # Market and exchange type indexes for historical analysis
        collection.create_index("market_type", background=True)
        collection.create_index("exchange_type", background=True)
        
        # Compound index for historical market-based queries
        collection.create_index([
            ("scheduled_at", 1),
            ("market_type", 1)
        ], background=True)
        
        # Time block index for historical data
        collection.create_index("time_block", background=True)
        
        # TTL index for automatic cleanup (optional - 1 year retention)
        # collection.create_index("archived_at", expireAfterSeconds=31536000, background=True)
        
        logger.debug("✅ Exchange serve historical collection indexes ensured")
        
    except Exception as e:
        logger.warning(f"⚠️ Failed to create exchange serve historical indexes: {e}")