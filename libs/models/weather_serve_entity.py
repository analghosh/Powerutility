"""
Weather Serve Entity
Represents a single weather data point for serving with timeseries support
"""

from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field, ConfigDict
from bson import ObjectId
from libs.enum.updated_flag import UpdatedFlag


class WeatherServeEntity(BaseModel):
    """
    Weather Serve Entity - Active time series data (4-day window)
    
    This entity represents the processed weather data that is actively served
    to the frontend applications. It contains a 4-day rolling window of data.
    """
    
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
    
    # Time-related fields (for timeseries and 4-day window logic)
    valid_time_local: Optional[str] = Field(None, description="Local valid time for weather data")
    valid_time_utc: Optional[int] = Field(None, description="UTC valid time for weather data")
    type: Optional[str] = Field(None, description="Weather variable type (TEMP, HUMIDITY, etc)")
    
    # Processing timestamps (for timeseries)
    inserted_at: datetime = Field(default_factory=datetime.now, description="Insertion timestamp")
    updated_at: datetime = Field(default_factory=datetime.now, description="Last update timestamp")
    created_at: datetime = Field(default_factory=datetime.now, description="Creation timestamp")
    timestamp: datetime = Field(default_factory=datetime.now, description="Time series indexing timestamp")
    
    # Metadata fields
    data_type: str = Field(default="weather", description="Data type identifier")
    execution_id: Optional[str] = Field(None, description="Job execution ID from audit_db")
    
    # Update flag (for change tracking)
    updated_flag: UpdatedFlag = Field(default=UpdatedFlag.UPDATED, description="Flag indicating if data was updated")
    
    # Revision tracking
    source_revision_number: Optional[int] = Field(None, description="Revision number from source data")
    system_revision_number: int = Field(default=1, description="System revision counter (increments on each update)")
    
    # Weather data fields (single values)
    cloud_cover: Optional[float] = None
    day_of_week: Optional[str] = None
    day_or_night: Optional[str] = None
    expiration_time_utc: Optional[int] = None
    icon_code: Optional[int] = None
    icon_code_extend: Optional[int] = None
    precip_chance: Optional[float] = None
    precip_type: Optional[str] = None
    pressure_mean_sea_level: Optional[float] = None
    qpf: Optional[float] = None
    qpf_snow: Optional[float] = None
    relative_humidity: Optional[float] = None
    temperature: Optional[float] = None
    temperature_dew_point: Optional[float] = None
    temperature_feels_like: Optional[float] = None
    temperature_heat_index: Optional[float] = None
    temperature_wind_chill: Optional[float] = None
    uv_description: Optional[str] = None
    uv_index: Optional[int] = None
    visibility: Optional[float] = None
    wind_direction: Optional[int] = None
    wind_direction_cardinal: Optional[str] = None
    wind_gust: Optional[float] = None
    wind_speed: Optional[float] = None
    wx_phrase_long: Optional[str] = None
    wx_phrase_short: Optional[str] = None
    wx_severity: Optional[int] = None
    wx_string: Optional[str] = None
    ceiling: Optional[float] = None
    scattered_cloud_base_height: Optional[float] = None
    pressure_altimeter: Optional[float] = None
    qpf_ice: Optional[float] = None
    qualifier_set: Optional[str] = None
    temperature_wet_bulb_globe: Optional[float] = None
    conditional_probability_thunder: Optional[float] = None
    conditional_probability_sleet: Optional[float] = None
    conditional_probability_snow: Optional[float] = None
    conditional_probability_rain: Optional[float] = None
    conditional_probability_freezing_rain: Optional[float] = None
    source: Optional[str] = Field(None, description="Data source identifier")
    city: Optional[str] = Field(None, description="Data source identifier")
    
    @classmethod
    def from_raw_data(cls, raw_data: dict, metadata: dict, provider_config: dict) -> "WeatherServeEntity":
        """
        Create WeatherServeEntity from a single raw weather data record
        
        Args:
            raw_data: Single weather record
            metadata: Processing metadata
            provider_config: Provider configuration
            
        Returns:
            WeatherServeEntity: Populated weather entity
        """
        provider_metadata = provider_config.get("metadata", {})
        current_time = datetime.now()
        
        # Create entity with metadata and raw data
        entity_data = {
            "inserted_at": current_time,
            "created_at": current_time,
            "updated_at": current_time,
            "timestamp": current_time,
            "data_type": "weather",
            "execution_id": metadata.get("execution_id"),
            "updated_flag": UpdatedFlag.UPDATED,  # Default to UPDATED for new records
            
            # Map all weather fields from raw data
            "cloud_cover": raw_data.get("cloud_cover"),
            "day_of_week": raw_data.get("day_of_week"),
            "day_or_night": raw_data.get("day_or_night"),
            "expiration_time_utc": raw_data.get("expiration_time_utc"),
            "icon_code": raw_data.get("icon_code"),
            "icon_code_extend": raw_data.get("icon_code_extend"),
            "precip_chance": raw_data.get("precip_chance"),
            "precip_type": raw_data.get("precip_type"),
            "pressure_mean_sea_level": raw_data.get("pressure_mean_sea_level"),
            "qpf": raw_data.get("qpf"),
            "qpf_snow": raw_data.get("qpf_snow"),
            "relative_humidity": raw_data.get("relative_humidity"),
            "temperature": raw_data.get("temperature"),
            "temperature_dew_point": raw_data.get("temperature_dew_point"),
            "temperature_feels_like": raw_data.get("temperature_feels_like"),
            "temperature_heat_index": raw_data.get("temperature_heat_index"),
            "temperature_wind_chill": raw_data.get("temperature_wind_chill"),
            "uv_description": raw_data.get("uv_description"),
            "uv_index": raw_data.get("uv_index"),
            "valid_time_local": raw_data.get("valid_time_local"),
            "valid_time_utc": raw_data.get("valid_time_utc"),
            "visibility": raw_data.get("visibility"),
            "wind_direction": raw_data.get("wind_direction"),
            "wind_direction_cardinal": raw_data.get("wind_direction_cardinal"),
            "wind_gust": raw_data.get("wind_gust"),
            "wind_speed": raw_data.get("wind_speed"),
            "wx_phrase_long": raw_data.get("wx_phrase_long"),
            "wx_phrase_short": raw_data.get("wx_phrase_short"),
            "wx_severity": raw_data.get("wx_severity"),
            "wx_string": raw_data.get("wx_string"),
            "ceiling": raw_data.get("ceiling"),
            "scattered_cloud_base_height": raw_data.get("scattered_cloud_base_height"),
            "pressure_altimeter": raw_data.get("pressure_altimeter"),
            "qpf_ice": raw_data.get("qpf_ice"),
            "qualifier_set": raw_data.get("qualifier_set"),
            "temperature_wet_bulb_globe": raw_data.get("temperature_wet_bulb_globe"),
            "conditional_probability_thunder": raw_data.get("conditional_probability_thunder"),
            "conditional_probability_sleet": raw_data.get("conditional_probability_sleet"),
            "conditional_probability_snow": raw_data.get("conditional_probability_snow"),
            "conditional_probability_rain": raw_data.get("conditional_probability_rain"),
            "conditional_probability_freezing_rain": raw_data.get("conditional_probability_freezing_rain"),
            "source": provider_metadata.get("source", "IBM"),
            "city":  provider_metadata.get("city", "puducherry")
        }
        
        return cls(**entity_data)


class WeatherServeHistoricalEntity(BaseModel):
    """
    Weather Serve Historical Entity - Archived time series data
    
    This entity represents the historical weather data that has been archived
    from the active weather_serve collection. It preserves the original _id
    for tracking purposes.
    """
    
    model_config = ConfigDict(
        validate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={
            ObjectId: str,
            datetime: lambda v: v.isoformat()
        }
    )
    
    # MongoDB ID (preserved from original)
    id: Optional[Any] = Field(default_factory=ObjectId, alias="_id")
    
    # All fields from WeatherServeEntity
    valid_time_local: Optional[str] = Field(None, description="Local valid time for weather data")
    valid_time_utc: Optional[int] = Field(None, description="UTC valid time for weather data")
    type: Optional[str] = Field(None, description="Weather variable type (TEMP, HUMIDITY, etc)")
    
    # Metadata fields
    data_type: str = Field(default="weather", description="Data type identifier")
    execution_id: Optional[str] = Field(None, description="Job execution ID from audit_db")
    
    # Update flag
    updated_flag: UpdatedFlag = Field(default=UpdatedFlag.UPDATED, description="Flag indicating if data was updated")
    
    # Revision tracking
    source_revision_number: Optional[int] = Field(None, description="Revision number from source data")
    system_revision_number: int = Field(default=1, description="System revision counter (increments on each update)")
    
    # Weather data fields (all from original entity)
    cloud_cover: Optional[float] = None
    day_of_week: Optional[str] = None
    day_or_night: Optional[str] = None
    expiration_time_utc: Optional[int] = None
    icon_code: Optional[int] = None
    icon_code_extend: Optional[int] = None
    precip_chance: Optional[float] = None
    precip_type: Optional[str] = None
    pressure_mean_sea_level: Optional[float] = None
    qpf: Optional[float] = None
    qpf_snow: Optional[float] = None
    relative_humidity: Optional[float] = None
    temperature: Optional[float] = None
    temperature_dew_point: Optional[float] = None
    temperature_feels_like: Optional[float] = None
    temperature_heat_index: Optional[float] = None
    temperature_wind_chill: Optional[float] = None
    uv_description: Optional[str] = None
    uv_index: Optional[int] = None
    visibility: Optional[float] = None
    wind_direction: Optional[int] = None
    wind_direction_cardinal: Optional[str] = None
    wind_gust: Optional[float] = None
    wind_speed: Optional[float] = None
    wx_phrase_long: Optional[str] = None
    wx_phrase_short: Optional[str] = None
    wx_severity: Optional[int] = None
    wx_string: Optional[str] = None
    ceiling: Optional[float] = None
    scattered_cloud_base_height: Optional[float] = None
    pressure_altimeter: Optional[float] = None
    qpf_ice: Optional[float] = None
    qualifier_set: Optional[str] = None
    temperature_wet_bulb_globe: Optional[float] = None
    conditional_probability_thunder: Optional[float] = None
    conditional_probability_sleet: Optional[float] = None
    conditional_probability_snow: Optional[float] = None
    conditional_probability_rain: Optional[float] = None
    conditional_probability_freezing_rain: Optional[float] = None
    
    # Original processing timestamps (preserved)
    inserted_at: datetime = Field(..., description="Original insertion timestamp")
    updated_at: datetime = Field(..., description="Original last update timestamp")
    created_at: datetime = Field(..., description="Original creation timestamp")
    timestamp: datetime = Field(..., description="Original time series indexing timestamp")
    
    # Archive-specific timestamps
    archived_at: datetime = Field(default_factory=datetime.now, description="Archive timestamp")
    archived_timestamp: datetime = Field(default_factory=datetime.now, description="Archive timestamp for time series indexing")# Database Index Management
def ensure_weather_serve_indexes(collection):
    """
    Ensure database indexes for weather_serve collection
    
    Args:
        collection: MongoDB collection instance
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        # Primary time-based index (most common query)
        collection.create_index([("valid_time_utc", 1)], background=True)
        collection.create_index([("valid_time_local", 1)], background=True)
        
        # Weather type index
        collection.create_index("type", background=True)
        
        # Location-based indexes
        collection.create_index("latitude", background=True)
        collection.create_index("longitude", background=True)
        
        # Compound index for location queries
        collection.create_index([
            ("latitude", 1),
            ("longitude", 1)
        ], background=True)
        
        # Compound index for time + type queries (very common)
        collection.create_index([
            ("valid_time_utc", 1),
            ("type", 1)
        ], background=True)
        
        # Compound index for time + location
        collection.create_index([
            ("valid_time_utc", 1),
            ("latitude", 1),
            ("longitude", 1)
        ], background=True)
        
        # Source index for data source tracking
        collection.create_index("source", background=True)
        
        # Index for processing timestamps
        collection.create_index("inserted_at", background=True)
        collection.create_index("updated_at", background=True)
        
        logger.debug("✅ Weather serve collection indexes ensured")
        
    except Exception as e:
        logger.warning(f"⚠️ Failed to create weather serve indexes: {e}")

def ensure_weather_serve_historical_indexes(collection):
    """
    Ensure database indexes for weather_serve_historical collection
    
    Args:
        collection: MongoDB collection instance
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        # Primary time-based index for historical queries
        collection.create_index([("valid_time_utc", 1)], background=True)
        collection.create_index([("valid_time_local", 1)], background=True)
        
        # Archive timestamp index
        collection.create_index([("archived_at", -1)], background=True)
        collection.create_index([("archived_timestamp", -1)], background=True)
        
        # Weather type and location indexes for historical analysis
        collection.create_index("type", background=True)
        collection.create_index([
            ("latitude", 1),
            ("longitude", 1)
        ], background=True)
        
        # Compound index for historical weather queries
        collection.create_index([
            ("valid_time_utc", 1),
            ("type", 1)
        ], background=True)
        
        # TTL index for automatic cleanup (optional - 1 year retention)
        # collection.create_index("archived_at", expireAfterSeconds=31536000, background=True)
        
        logger.debug("✅ Weather serve historical collection indexes ensured")
        
    except Exception as e:
        logger.warning(f"⚠️ Failed to create weather serve historical indexes: {e}")
