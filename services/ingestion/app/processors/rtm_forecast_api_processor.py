"""
RTM Forecast API Processor
Handles fetching RTM forecast data from external API and storing:
- Demand forecast (IDF) and forecasted_frequency in demand_serve
- RTM forecasted price in limit_values (RTM_forecasted_price)
"""

import json
import asyncio
import aiohttp
from datetime import date, datetime, timedelta
from typing import Dict, Any, List, Optional
from zoneinfo import ZoneInfo

from libs.database.mongo_client_factory import get_mongo_client
from libs.models.integration_apilog_entity import IntegrationApiLogEntity
from services.ingestion.app.core.config import settings
from services.ingestion.app.core.logger import get_ingestion_logger
from services.ingestion.app.processors.base_raw_data_processor import BaseRawDataProcessor
from services.ingestion.app.repositories.integration_apilog_repository import IntegrationApiLogRepository

logger = get_ingestion_logger("rtm_forecast_api_processor")

RTM_MARKET = "RTM"
RTM_FORECASTED_PRICE_ID = "RTM_forecasted_price"
RTM_SOURCE_FORECAST = "rtm_forecast_api"

# After this local time, API window shifts to tomorrow; intraday block filter is disabled
# so inserts run for blocks 1–96 (today's wall-clock "current block" refers to the wrong day).
EVENING_CUTOVER_HM = (22, 30)


def _get_rtm_timezone():
    tz_name = getattr(settings, "CRON_TIMEZONE", None) or "Asia/Kolkata"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def _current_time_block_1_to_96(now_local: datetime) -> int:
    minutes_from_midnight = now_local.hour * 60 + now_local.minute
    time_block = (minutes_from_midnight // 15) + 1
    if time_block > 96:
        time_block = 96
    if time_block < 1:
        time_block = 1
    return time_block


def _should_write_forecast_block(
    scheduled_date: date,
    time_block: int,
    today_ist: date,
    current_block: int,
    after_evening_cutover: bool = False,
) -> bool:
    if scheduled_date < today_ist:
        return False
    if after_evening_cutover:
        return True
    if scheduled_date == today_ist:
        return time_block >= current_block
    return True


class RTMForecastAPIProcessor(BaseRawDataProcessor):
    """
    Processor for fetching RTM forecast data from external API and storing demand + frequency
    into demand_serve and forecasted RTM price into limit_values.
    """

    def __init__(self, audit_logger=None):
        """Initialize RTM Forecast API processor"""
        super().__init__()
        self.audit_logger = audit_logger
        self.api_timeout = getattr(settings, 'API_TIMEOUT', 30)
        self.retry_attempts = getattr(settings, 'API_RETRY_ATTEMPTS', 3)
        self.retry_delay = getattr(settings, 'API_RETRY_DELAY', 5)
        self.mongo_uri = settings.MONGODB_URI
        self.database_name = settings.MONGODB_DATABASE
        self.integration_apilog_repository = IntegrationApiLogRepository(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE
        )

    async def execute_cron_job(self) -> Dict[str, Any]:
        """Execute RTM Forecast API processing as a cron job."""
        job_type = "rtm_forecast_api_processor"
        job_name = "RTM Forecast API Processor"
        schedule = "*/30 * * * *"  # Every 30 minutes
        start_time = datetime.now()
        execution_id = self.record_job_start(job_type, job_name, schedule, start_time)

        logger.info("Starting RTM Forecast API processing cron job at %s", start_time.strftime('%Y-%m-%d %H:%M:%S'))

        try:
            api_config = await self._get_api_config_from_db()
            if not api_config:
                logger.warning("No RTM Forecast API configuration found in database")
                result = {'status': 'error', 'error': 'No API configuration found', 'execution_time': (datetime.now() - start_time).total_seconds()}
                if execution_id:
                    self.record_job_completion(job_type, job_name, schedule, start_time, datetime.now(), execution_id, "error", False, execution_id, result, "No API configuration found")
                return result

            api_response = await self._fetch_rtm_forecast_data(api_config)
            if not api_response:
                logger.error("Failed to fetch RTM forecast data from API")
                result = {'status': 'error', 'error': 'Failed to fetch API data', 'execution_time': (datetime.now() - start_time).total_seconds()}
                if execution_id:
                    self.record_job_completion(job_type, job_name, schedule, start_time, datetime.now(), execution_id, "error", False, execution_id, result, "Failed to fetch API data")
                return result

            demand_serve_stored = 0
            limit_values_stored = 0

            data_items = api_response.get("data", [])
            if not isinstance(data_items, list):
                logger.warning("RTM Forecast API 'data' must be an array; got %s", type(data_items).__name__)
                data_items = []

            logger.info("Processing %d date entries from RTM forecast API response", len(data_items))

            tz = _get_rtm_timezone()
            now_local = datetime.now(tz)
            today_ist = now_local.date()
            current_block = _current_time_block_1_to_96(now_local)
            after_evening_cutover = (now_local.hour, now_local.minute) >= EVENING_CUTOVER_HM

            for date_entry in data_items:
                date_str = date_entry.get("date")
                if not date_str:
                    continue
                try:
                    scheduled_at = datetime.strptime(date_str, "%Y-%m-%d")
                except ValueError:
                    logger.warning("Invalid date format: %s", date_str)
                    continue

                # Frequency forecast lookup by time_block
                freq_by_block: Dict[int, float] = {}
                for fr in date_entry.get("frequency_forecast", []) or []:
                    try:
                        tb = fr.get("time_block")
                        if tb is None:
                            continue
                        freq_by_block[int(tb)] = float(fr.get("forecast_frequency", 0.0) or 0.0)
                    except Exception:
                        continue

                # Demand -> demand_serve (IDF)
                demand_data = date_entry.get("demand") or {}
                market_type = (demand_data.get("market") or "IDF").strip() or "IDF"
                demand_records = demand_data.get("data") or []
                if isinstance(demand_records, list) and demand_records:
                    stored = await self._store_demand_serve(
                        scheduled_at=scheduled_at,
                        market_type=market_type,
                        data=demand_records,
                        freq_by_block=freq_by_block,
                        today_ist=today_ist,
                        current_block=current_block,
                        after_evening_cutover=after_evening_cutover,
                    )
                    demand_serve_stored += stored

                # RTM forecasted price -> limit_values
                price_records = date_entry.get("rtm_forecasted_price") or []
                if isinstance(price_records, list) and price_records:
                    stored = await self._store_rtm_forecasted_price_limit_values(
                        scheduled_at=scheduled_at,
                        data=price_records,
                        today_ist=today_ist,
                        current_block=current_block,
                        after_evening_cutover=after_evening_cutover,
                    )
                    limit_values_stored += stored

            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            result = {
                'status': 'success',
                'demand_serve_stored': demand_serve_stored,
                'limit_values_stored': limit_values_stored,
                'execution_time': execution_time,
                'execution_id': execution_id,
                'today_ist': today_ist.isoformat(),
                'current_block': current_block,
                'after_evening_cutover': after_evening_cutover,
            }
            if execution_id:
                self.record_job_completion(job_type, job_name, schedule, start_time, end_time, execution_id, "success", True, execution_id, result)
            logger.info(
                "RTM Forecast API processing completed: demand_serve=%d, limit_values=%d in %.2fs",
                demand_serve_stored,
                limit_values_stored,
                execution_time
            )
            return result

        except Exception as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            logger.error("Error in RTM Forecast API processing cron job: %s", str(e))
            result = {'status': 'error', 'error': str(e), 'execution_time': execution_time}
            if execution_id:
                self.record_job_completion(job_type, job_name, schedule, start_time, end_time, execution_id, "error", False, execution_id, result, str(e))
            return result

    async def _get_api_config_from_db(self) -> Optional[Dict[str, Any]]:
        """Get RTM Forecast API configuration from database (providers collection)."""
        try:
            client = get_mongo_client(self.mongo_uri, "rtm_forecast_api_config")
            db = client[self.database_name]
            provider = db["providers"].find_one({"code": "rtm_forecast_api_provider", "enabled": True})
            if not provider:
                provider = db["providers"].find_one({"provider_type": "rtm_forecast_api", "enabled": True})
            if not provider:
                provider = db["providers"].find_one({"code": "rtm_api_provider", "enabled": True})
            if not provider or not provider.get("metadata"):
                return None
            metadata = provider.get("metadata", {})
            api_config = {
                'url': metadata.get('api_url'),
                'api_key': metadata.get('api_key'),
                'timeout': metadata.get('timeout', self.api_timeout),
                'auth_header': metadata.get('auth_header', 'X-API-Key'),
                'date_range_days': metadata.get('date_range_days', 1),
            }
            if not api_config['url']:
                logger.error("RTM Forecast API URL not configured in provider metadata")
                return None
            if not api_config['api_key']:
                logger.error("RTM Forecast API key not configured in provider metadata")
                return None
            logger.info("Loaded RTM Forecast API config: url=%s", api_config['url'])
            return api_config
        except Exception as e:
            logger.error("Error getting RTM Forecast API config from database: %s", str(e))
            return None

    async def _fetch_rtm_forecast_data(self, api_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Fetch RTM forecast data from the external API."""
        try:
            url = api_config['url']
            api_key = api_config['api_key']
            timeout = api_config.get('timeout', self.api_timeout)
            auth_header = api_config.get('auth_header', 'X-API-Key')
            date_range_days = api_config.get('date_range_days', 1)
            tz = _get_rtm_timezone()
            today = datetime.now(tz).date()
            now_local = datetime.now(tz)
            if (now_local.hour, now_local.minute) >= EVENING_CUTOVER_HM:
                base_date = today + timedelta(days=1)
                tomorrow_shift = True
            else:
                base_date = today
                tomorrow_shift = False
            start_date = base_date.strftime('%Y-%m-%d')
            end_date = (base_date + timedelta(days=date_range_days - 1)).strftime('%Y-%m-%d')

            headers = {auth_header: api_key, 'User-Agent': 'EPMS-Backend/1.0'}
            params = {'start_date': start_date, 'end_date': end_date}
            logger.info(
                "Fetching RTM forecast data from %s for dates %s to %s (base_date=%s, tomorrow_shift=%s)",
                url,
                start_date,
                end_date,
                base_date.isoformat(),
                tomorrow_shift,
            )

            for attempt in range(self.retry_attempts):
                try:
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                        async with session.get(url, params=params, headers=headers) as response:
                            if response.status != 200:
                                error_msg = f"API returned status {response.status}: {response.reason}"
                                logger.error("RTM Forecast API request failed: %s", error_msg)
                                if attempt < self.retry_attempts - 1:
                                    logger.info("Retrying RTM Forecast API request (attempt %d/%d)", attempt + 2, self.retry_attempts)
                                    await asyncio.sleep(self.retry_delay)
                                    continue
                                return None
                            response_text = await response.text()
                            try:
                                response_data = json.loads(response_text)
                            except json.JSONDecodeError as e:
                                logger.error("RTM Forecast API returned invalid JSON: %s", e)
                                return None
                            try:
                                log_entity = IntegrationApiLogEntity(
                                    provider_id="rtm_forecast_api_provider",
                                    raw_data=response,
                                    timestamp=datetime.now(),
                                    metadata={"url": url, "method": "GET", "status_code": response.status, "params": params, "attempt": attempt + 1}
                                )
                                await self.integration_apilog_repository.save_log("rtm_forecast", log_entity)
                            except Exception as log_error:
                                logger.warning("Failed to log RTM Forecast API response: %s", log_error)
                            logger.info("Successfully fetched RTM forecast data from API")
                            return response_data
                except aiohttp.ClientError as e:
                    logger.error("HTTP client error (attempt %d/%d): %s", attempt + 1, self.retry_attempts, str(e))
                    if attempt < self.retry_attempts - 1:
                        await asyncio.sleep(self.retry_delay)
                    else:
                        return None
            return None
        except Exception as e:
            logger.error("Error fetching RTM forecast data: %s", str(e))
            return None

    async def _store_demand_serve(
        self,
        scheduled_at: datetime,
        market_type: str,
        data: List[Dict[str, Any]],
        freq_by_block: Dict[int, float],
        today_ist: date,
        current_block: int,
        after_evening_cutover: bool = False,
    ) -> int:
        """
        Store RTM demand forecasts in demand_serve collection.

        Uses market_type from API (expected: IDF) and overwrites forecasted_frequency each run.
        When after_evening_cutover, writes blocks 1–96 for non-past dates (current_block ignored).
        """
        try:
            if not data:
                return 0

            client = get_mongo_client(self.mongo_uri, "rtm_forecast_demand_serve")
            db = client[self.database_name]
            collection = db["demand_serve"]

            current_time = datetime.now()
            stored_count = 0
            scheduled_date = scheduled_at.date()

            # Deduplicate by time_block (keep first)
            time_block_data: Dict[int, Dict[str, Any]] = {}
            for record in data:
                tb = record.get("time_block")
                if tb is None:
                    continue
                tb_i = int(tb)
                if tb_i not in time_block_data:
                    time_block_data[tb_i] = record

            rostering_by_block: Dict[int, float] = {}
            try:
                for doc in collection.find(
                    {"scheduled_at": scheduled_at, "type": "IDF"},
                    {"time_block": 1, "rostering": 1},
                ):
                    tb = doc.get("time_block")
                    if tb is not None:
                        rostering_by_block[int(tb)] = float(doc.get("rostering") or 0.0)
            except Exception as prefetch_err:
                logger.warning(
                    "Failed to prefetch rostering from demand_serve (IDF): %s",
                    prefetch_err,
                )

            for time_block, record in time_block_data.items():
                try:
                    if not _should_write_forecast_block(
                        scheduled_date,
                        int(time_block),
                        today_ist,
                        current_block,
                        after_evening_cutover=after_evening_cutover,
                    ):
                        continue
                    forecast_mw = float(record.get("demand_forecast_mw", 0.0) or 0.0)
                    forecasted_frequency = float(freq_by_block.get(int(time_block), 0.0) or 0.0)
                    rostering = float(rostering_by_block.get(int(time_block), 0.0) or 0.0)
                    comp_act_demand = forecast_mw + rostering

                    filter_doc = {
                        "scheduled_at": scheduled_at,
                        "time_block": int(time_block),
                        "type": market_type,
                    }

                    update_doc = {
                        "$set": {
                            "scheduled_at": scheduled_at,
                            "time_block": int(time_block),
                            "type": market_type,
                            "forecast": forecast_mw,
                            "forecasted_frequency": forecasted_frequency,
                            "updated_at": current_time,
                            "timestamp": current_time,
                            "source_forecast": RTM_SOURCE_FORECAST,
                            "comp_act_demand": comp_act_demand,
                            "comp_udm": forecast_mw,
                        },
                        "$setOnInsert": {
                            "inserted_at": current_time,
                            "created_at": current_time,
                            "source": RTM_SOURCE_FORECAST,
                            "system_revision_number": 1,
                        },
                    }

                    result = collection.update_one(filter_doc, update_doc, upsert=True)
                    if result.upserted_id or result.modified_count > 0:
                        stored_count += 1
                except Exception as record_error:
                    logger.warning(
                        "Failed to store RTM demand_serve record: time_block=%s, error=%s",
                        time_block,
                        str(record_error),
                    )

            logger.info(
                "Stored %d demand_serve records for date %s, type %s",
                stored_count,
                scheduled_at.strftime('%Y-%m-%d'),
                market_type,
            )
            return stored_count
        except Exception as e:
            logger.error("Error storing RTM demand_serve: %s", str(e))
            return 0

    async def _store_rtm_forecasted_price_limit_values(
        self,
        scheduled_at: datetime,
        data: List[Dict[str, Any]],
        today_ist: date,
        current_block: int,
        after_evening_cutover: bool = False,
    ) -> int:
        """Store RTM forecasted prices in limit_values collection as RTM_forecasted_price."""
        try:
            if not data:
                return 0

            client = get_mongo_client(self.mongo_uri, "rtm_forecast_limit_values")
            db = client[self.database_name]
            collection = db["limit_values"]

            current_time = datetime.now()
            stored_count = 0
            scheduled_date = scheduled_at.date()

            for record in data:
                try:
                    time_block = record.get("time_block")
                    price = record.get("price_inr_mwh", 0.0)
                    if time_block is None:
                        continue
                    tb_i = int(time_block)
                    if not _should_write_forecast_block(
                        scheduled_date,
                        tb_i,
                        today_ist,
                        current_block,
                        after_evening_cutover=after_evening_cutover,
                    ):
                        continue

                    filter_doc = {
                        "scheduled_at": scheduled_at,
                        "block": tb_i,
                        "id": RTM_FORECASTED_PRICE_ID,
                        "market": RTM_MARKET,
                        "category": "Limits",
                        "area": "Puducherry",
                    }

                    update_doc = {
                        "$set": {
                            "scheduled_at": scheduled_at,
                            "block": tb_i,
                            "id": RTM_FORECASTED_PRICE_ID,
                            "market": RTM_MARKET,
                            "category": "Limits",
                            "area": "Puducherry",
                            "values": float(price or 0.0),
                            "unit": "Rs/MWh",
                            "is_active": True,
                            "source": RTM_SOURCE_FORECAST,
                            "updated_at": current_time,
                            "updated_by": "rtm_forecast_api_processor",
                        },
                        "$setOnInsert": {
                            "version": 1,
                            "created_at": current_time,
                            "created_by": "rtm_forecast_api_processor",
                        },
                    }

                    result = collection.update_one(filter_doc, update_doc, upsert=True)
                    if result.upserted_id or result.modified_count > 0:
                        stored_count += 1
                except Exception as record_error:
                    logger.warning(
                        "Failed to store RTM limit_values record: time_block=%s, error=%s",
                        record.get("time_block"),
                        str(record_error),
                    )

            logger.info(
                "Stored %d RTM_forecasted_price limit_values records for date %s",
                stored_count,
                scheduled_at.strftime('%Y-%m-%d'),
            )
            return stored_count
        except Exception as e:
            logger.error("Error storing RTM limit_values: %s", str(e))
            return 0
