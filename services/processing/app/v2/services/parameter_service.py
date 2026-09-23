"""
Parameter Service
Service layer for parameter business logic
"""

import logging
import re
import uuid
from typing import List, Dict, Any, Optional

from libs.constants.idf_forecast import IDF_FORECAST_SWITCH_KEY
from ..repository.parameter_repository import parameter_repository

logger = logging.getLogger(__name__)


# Default bid configuration used when no active record exists in DB.
DEFAULT_BID_CONFIGURATION = {
    "RTM": {
        "block_bid_buy": True,
        "block_bid_sell": True,
        "single_bid_buy": True,
        "single_bid_sell": True,
    },
    "GDAM": {
        "block_bid_buy": True,
        "block_bid_sell": True,
        "single_bid_buy": True,
        "single_bid_sell": True,
        "ocf": True,
        "premium": True,
        "discount": True,
    },
    "DAM": {
        "block_bid_buy": True,
        "block_bid_sell": True,
        "single_bid_buy": True,
        "single_bid_sell": True,
        "ocf": True,
        "premium": True,
        "discount": True,
    },
    "HPDAM": {
        "block_bid_buy": True,
        "block_bid_sell": True,
        "single_bid_buy": True,
        "single_bid_sell": True,
    },
}

BID_CONFIGURATION_KEY = "bid_configuration"
SHIFTS_KEY = "shifts"
# Hours only: "1-8", "22-6", "1-24". Minutes: "9:30-18:15", "1:00-8:30".
SHIFT_TIMING_HOURS_PATTERN = re.compile(r"^(\d{1,2})-(\d{1,2})$")
SHIFT_TIMING_MINUTES_PATTERN = re.compile(
    r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$"
)
MINUTES_PER_DAY = 24 * 60
SHIFT_TIMING_ERROR = (
    "shift_timing must be a start-end window on the 1-24 clock, "
    "hours only or with minutes. "
    "Valid: '1-8', '1-24', '22-6', '9:30-18:15'. "
    "Invalid: '0-8', '1-25', '24:30-1:00', '9:30-18', 'abc'. "
    "Hours are 1-24; minutes are 00-59; hour 24 is only allowed as 24:00."
)
RESERVED_PARAMETER_KEYS = {SHIFTS_KEY}
SHIFTS_RESERVED_MESSAGE = (
    "Shifts must be managed via /parameters/shifts, not generic parameter CRUD"
)


def _clock_to_minutes(hour: int, minute: int) -> int:
    """Minutes past midnight. Hour 1 is 01:00; hour 24 is 24:00 (end of day)."""
    if not 1 <= hour <= 24 or not 0 <= minute <= 59 or (hour == 24 and minute != 0):
        raise ValueError(SHIFT_TIMING_ERROR)
    return hour * 60 + minute


def parse_shift_timing(shift_timing: Any) -> tuple[int, int, str]:
    """
    Parse a shift window into minutes past midnight.

    Returns ``(start_minute, end_minute, stored)``. ``stored`` is what to
    persist: hour-only input is canonicalized without leading zeros ("1-8"),
    and minute input is kept as submitted ("9:30-18:15"). Hour-only "9-18"
    is 09:00–18:00 (540, 1080), not a different clock.

    The window is half-open ``[start, end)`` on the IST 24h clock. A start
    later than the end wraps past midnight ("22-6", "22:30-06:15"). Equal
    start and end is a full day. Adjacent windows such as "1-8" and "8-12"
    share the boundary instant with the later window only: 08:00 belongs to
    "8-12".
    """
    timing = str(shift_timing or "").strip()
    hours = SHIFT_TIMING_HOURS_PATTERN.fullmatch(timing)
    if hours:
        start_hour, end_hour = int(hours.group(1)), int(hours.group(2))
        return (
            _clock_to_minutes(start_hour, 0),
            _clock_to_minutes(end_hour, 0),
            f"{start_hour}-{end_hour}",
        )
    minutes = SHIFT_TIMING_MINUTES_PATTERN.fullmatch(timing)
    if minutes:
        start_hour, start_minute, end_hour, end_minute = (
            int(minutes.group(i)) for i in range(1, 5)
        )
        return (
            _clock_to_minutes(start_hour, start_minute),
            _clock_to_minutes(end_hour, end_minute),
            timing,
        )
    raise ValueError(SHIFT_TIMING_ERROR)


def shift_contains_minute(
    start_minute: int,
    end_minute: int,
    minute_of_day: int,
) -> bool:
    """
    Whether ``minute_of_day`` (0–1439) falls in the half-open shift window.

    Equal bounds cover the whole day. When start is after end, the window
    is ``[start, 1440) ∪ [0, end)``.
    """
    if not 0 <= minute_of_day < MINUTES_PER_DAY:
        return False
    if start_minute == end_minute:
        return True
    if start_minute < end_minute:
        return start_minute <= minute_of_day < end_minute
    return minute_of_day >= start_minute or minute_of_day < end_minute


# Default NOC / ceiling bid prices per market (auto-created if missing).
DEFAULT_BID_PRICES = {
    "rtm_bid_price": 10000,
    "dam_bid_price": 10000,
    "gdam_bid_price": 10000,
    "hpdam_bid_price": 20000,
}

# "Maximum Buy Bid Price" toggles per market (auto-created if missing).
# Disabled by default: enabling lets users bid at the Maximum Exchange Bid Price.
DEFAULT_MAX_BUY_BID_PRICE_FLAGS = {
    "rtm_max_buy_bid_price": False,
    "gdam_max_buy_bid_price": False,
    "dam_max_buy_bid_price": False,
    "hpdam_max_buy_bid_price": False,
}


class ParameterService:
    """Service for parameter operations"""

    @staticmethod
    def _is_idf_forecast_switch_update(
        para_key: Optional[str], update_data: Dict[str, Any]
    ) -> bool:
        return para_key == IDF_FORECAST_SWITCH_KEY and "para_value" in update_data

    async def create_parameter(self, parameter_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a single parameter
        
        Args:
            parameter_data: Dictionary containing parameter data
            
        Returns:
            Created parameter
        """
        try:
            # Validate required fields
            if "para_key" not in parameter_data:
                raise ValueError("para_key is required")
            
            if "para_value" not in parameter_data:
                raise ValueError("para_value is required")

            self._reject_reserved_parameter_key(parameter_data.get("para_key"))
            
            # Check if parameter with same key already exists
            existing = await parameter_repository.get_parameter_by_key(parameter_data["para_key"])
            if existing:
                raise ValueError(f"Parameter with key '{parameter_data['para_key']}' already exists")
            
            return await parameter_repository.create_parameter(parameter_data)
            
        except Exception as e:
            logger.error(f"Error in parameter service create: {e}")
            raise

    async def create_parameters_bulk(self, parameters: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Create multiple parameters in bulk
        
        Args:
            parameters: List of parameter dictionaries
            
        Returns:
            Dictionary with created count and IDs
        """
        try:
            # Validate all parameters
            for param in parameters:
                if "para_key" not in param:
                    raise ValueError("All parameters must have para_key")
                if "para_value" not in param:
                    raise ValueError("All parameters must have para_value")
                self._reject_reserved_parameter_key(param.get("para_key"))
            
            # Check for duplicate keys in the input
            keys = [param["para_key"] for param in parameters]
            if len(keys) != len(set(keys)):
                raise ValueError("Duplicate para_key found in input parameters")
            
            # Check if any keys already exist in database
            for key in keys:
                existing = await parameter_repository.get_parameter_by_key(key)
                if existing:
                    raise ValueError(f"Parameter with key '{key}' already exists")
            
            return await parameter_repository.create_parameters_bulk(parameters)
            
        except Exception as e:
            logger.error(f"Error in parameter service bulk create: {e}")
            raise

    async def get_parameter_by_id(self, parameter_id: str) -> Optional[Dict[str, Any]]:
        """
        Get parameter by ID
        
        Args:
            parameter_id: Parameter ID
            
        Returns:
            Parameter document or None
        """
        try:
            return await parameter_repository.get_parameter_by_id(parameter_id)
            
        except Exception as e:
            logger.error(f"Error in parameter service get by ID: {e}")
            raise

    async def get_parameter_by_key(self, para_key: str) -> Optional[Dict[str, Any]]:
        """
        Get parameter by key
        
        Args:
            para_key: Parameter key
            
        Returns:
            Parameter document or None
        """
        try:
            return await parameter_repository.get_parameter_by_key(para_key)
            
        except Exception as e:
            logger.error(f"Error in parameter service get by key: {e}")
            raise

    async def get_all_parameters(
        self,
        skip: int = 0,
        limit: int = 100,
        sort_by: str = "created_at",
        sort_order: int = -1
    ) -> Dict[str, Any]:
        """
        Get all parameters with pagination
        
        Args:
            skip: Number of records to skip
            limit: Maximum number of records to return
            sort_by: Field to sort by
            sort_order: Sort order (1 for ascending, -1 for descending)
            
        Returns:
            Dictionary with parameters and metadata
        """
        try:
            await self.ensure_bid_price_defaults()
            await self.ensure_max_buy_bid_price_defaults()
            return await parameter_repository.get_all_parameters(
                skip=skip,
                limit=limit,
                sort_by=sort_by,
                sort_order=sort_order
            )
            
        except Exception as e:
            logger.error(f"Error in parameter service get all: {e}")
            raise

    async def update_parameter(
        self,
        parameter_id: str,
        update_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Update parameter by ID
        
        Args:
            parameter_id: Parameter ID
            update_data: Dictionary containing fields to update
            
        Returns:
            Updated parameter or None
        """
        try:
            existing = await parameter_repository.get_parameter_by_id(parameter_id)
            if not existing:
                return None

            self._reject_reserved_parameter_key(existing.get("para_key"))
            if "para_key" in update_data:
                self._reject_reserved_parameter_key(update_data["para_key"])

            # Don't allow updating para_key to avoid conflicts
            if "para_key" in update_data:
                # Check if new key already exists
                key_existing = await parameter_repository.get_parameter_by_key(
                    update_data["para_key"]
                )
                if key_existing and str(key_existing.get("_id")) != parameter_id:
                    raise ValueError(
                        f"Parameter with key '{update_data['para_key']}' already exists"
                    )

            effective_key = update_data.get("para_key") or existing.get("para_key")
            if self._is_idf_forecast_switch_update(effective_key, update_data):
                from .idf_forecast_switch_service import idf_forecast_switch_service

                await idf_forecast_switch_service.validate_parameter_switch(
                    existing.get("para_value"),
                    update_data["para_value"],
                )

            result = await parameter_repository.update_parameter(parameter_id, update_data)
            if (
                result
                and self._is_idf_forecast_switch_update(effective_key, update_data)
            ):
                from .idf_forecast_switch_service import idf_forecast_switch_service

                result["merge_result"] = (
                    await idf_forecast_switch_service.merge_after_parameter_switch(
                        update_data["para_value"],
                        updated_by=update_data.get("updated_by"),
                    )
                )
            return result

        except Exception as e:
            logger.error(f"Error in parameter service update: {e}")
            raise

    async def delete_parameter(self, parameter_id: str) -> bool:
        """
        Delete parameter by ID
        
        Args:
            parameter_id: Parameter ID
            
        Returns:
            True if deleted, False otherwise
        """
        try:
            existing = await parameter_repository.get_parameter_by_id(parameter_id)
            if existing:
                self._reject_reserved_parameter_key(existing.get("para_key"))
            return await parameter_repository.delete_parameter(parameter_id)
            
        except Exception as e:
            logger.error(f"Error in parameter service delete: {e}")
            raise

    # ------------------------------------------------------------------
    # Bid price defaults (parameter master)
    # ------------------------------------------------------------------

    async def ensure_bid_price_defaults(self) -> Dict[str, Any]:
        """
        Ensure rtm_bid_price / dam_bid_price / gdam_bid_price / hpdam_bid_price
        exist in the parameters collection. Creates missing keys with defaults
        only; does not overwrite existing values.
        """
        created: Dict[str, Any] = {}
        for para_key, para_value in DEFAULT_BID_PRICES.items():
            existing = await parameter_repository.get_parameter_by_key(para_key)
            if existing:
                continue
            await parameter_repository.create_parameter(
                {
                    "para_key": para_key,
                    "para_value": para_value,
                    "description": (
                        f"Default NOC/ceiling bid price for "
                        f"{para_key.replace('_bid_price', '').upper()}"
                    ),
                }
            )
            created[para_key] = para_value
            logger.info(
                "Auto-created parameter '%s' with default value %s",
                para_key,
                para_value,
            )
        return created

    async def ensure_max_buy_bid_price_defaults(self) -> Dict[str, Any]:
        """
        Ensure the per-market "Maximum Buy Bid Price" toggles exist in the
        parameters collection. Creates missing keys disabled; never overwrites
        an existing value.
        """
        created: Dict[str, Any] = {}
        for para_key, para_value in DEFAULT_MAX_BUY_BID_PRICE_FLAGS.items():
            existing = await parameter_repository.get_parameter_by_key(para_key)
            if existing:
                continue
            market = para_key.replace("_max_buy_bid_price", "").upper()
            await parameter_repository.create_parameter(
                {
                    "para_key": para_key,
                    "para_value": para_value,
                    "description": (
                        f"Allow {market} Buy Bids at the Maximum Exchange Bid Price "
                        f"when the Ceiling Price equals it"
                    ),
                }
            )
            created[para_key] = para_value
            logger.info(
                "Auto-created parameter '%s' with default value %s",
                para_key,
                para_value,
            )
        return created

    async def get_bid_price(self, market: str) -> int:
        """
        Return the configured bid price for a market (RTM / DAM / GDAM / HPDAM).
        Ensures defaults exist, then reads from parameters.
        """
        await self.ensure_bid_price_defaults()
        key_map = {
            "RTM": "rtm_bid_price",
            "DAM": "dam_bid_price",
            "GDAM": "gdam_bid_price",
            "HPDAM": "hpdam_bid_price",
        }
        market_key = str(market).strip().upper()
        para_key = key_map.get(market_key)
        if not para_key:
            raise ValueError(
                f"Invalid market '{market}'. Allowed: {sorted(key_map)}"
            )
        record = await parameter_repository.get_parameter_by_key(para_key)
        default = DEFAULT_BID_PRICES[para_key]
        if not record or record.get("para_value") is None:
            return int(default)
        try:
            return int(record["para_value"])
        except (TypeError, ValueError):
            logger.warning(
                "Invalid para_value for %s: %r; falling back to %s",
                para_key,
                record.get("para_value"),
                default,
            )
            return int(default)

    # ------------------------------------------------------------------
    # Bid configuration (parameter master)
    # ------------------------------------------------------------------

    def _normalize_bid_configuration(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize incoming bid configuration to only allowed markets and flags.
        Missing markets/flags are filled from the default.
        """
        normalized: Dict[str, Any] = {}
        for market, default_flags in DEFAULT_BID_CONFIGURATION.items():
            incoming_market = config.get(market, {}) if isinstance(config, dict) else {}
            normalized[market] = {}
            for flag in default_flags:
                value = incoming_market.get(flag)
                normalized[market][flag] = bool(value) if value is not None else default_flags[flag]
        return normalized

    async def get_bid_configuration(self) -> Dict[str, Any]:
        """
        Return the currently active bid configuration from parameters collection.
        Falls back to DEFAULT_BID_CONFIGURATION if not found.
        """
        try:
            record = await parameter_repository.get_parameter_by_key(BID_CONFIGURATION_KEY)
            if record and record.get("para_value"):
                return record["para_value"]
            return DEFAULT_BID_CONFIGURATION
        except Exception as e:
            logger.error(f"Error fetching bid configuration: {e}")
            raise

    async def save_bid_configuration(
        self,
        config: Dict[str, Any],
        user_ctx: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Save the bid configuration directly to the active parameters collection.
        It becomes active immediately application-wide.
        """
        try:
            normalized = self._normalize_bid_configuration(config)
            actor_id = user_ctx.get("user_id", "anonymous")

            record = await parameter_repository.update_parameter_by_key(
                BID_CONFIGURATION_KEY,
                {
                    "para_key": BID_CONFIGURATION_KEY,
                    "para_value": normalized,
                    "updated_by": actor_id,
                }
            )

            return {
                "para_key": BID_CONFIGURATION_KEY,
                "para_value": record.get("para_value") if record else normalized,
                "message": "Bid configuration saved successfully"
            }
        except Exception as e:
            logger.error(f"Error saving bid configuration: {e}")
            raise

    async def is_bid_enabled(self, market: str, flag: str) -> bool:
        """
        Helper for bidding services to check if a specific bid flag is enabled.
        Markets: RTM, GDAM, DAM, HPDAM.
        Flags: block_bid_buy, block_bid_sell, single_bid_buy, single_bid_sell,
               ocf, premium, discount (last three only for GDAM/DAM).
        """
        try:
            config = await self.get_bid_configuration()
            market_config = config.get(market, {})
            return bool(market_config.get(flag, False))
        except Exception as e:
            logger.error(f"Error checking bid flag {market}/{flag}: {e}")
            # Fail-safe: disable on error.
            return False

    def _normalize_markets(self, markets: Any) -> List[str]:
        """
        Normalize active markets input to a list of unique, uppercase values.
        Accepts a list, a single string, or a comma-separated string.
        """
        allowed = {"DAM", "GDAM", "HPDAM"}
        if markets is None:
            return []
        if isinstance(markets, (list, tuple, set)):
            raw = [str(m).strip() for m in markets if m is not None]
        else:
            raw = [m.strip() for m in str(markets).split(",") if m.strip()]
        normalized = []
        for m in raw:
            upper = m.upper()
            if upper not in allowed:
                raise ValueError(f"Invalid active market '{m}'. Allowed: {sorted(allowed)}")
            if upper not in normalized:
                normalized.append(upper)
        return normalized

    async def get_active_markets(self) -> List[str]:
        """
        Return the list of currently configured active markets for single-bid-table.
        Defaults to ['DAM'] when not set or on error.
        """
        try:
            record = await parameter_repository.get_parameter_by_key("active_markets")
            if record and record.get("para_value"):
                value = record["para_value"]
                if isinstance(value, str):
                    return self._normalize_markets(value)
                if isinstance(value, (list, tuple, set)):
                    return self._normalize_markets(value)
        except Exception as e:
            logger.error(f"Error reading active_markets parameter: {e}")
        return ["DAM"]

    async def set_active_markets(self, markets: Any) -> Dict[str, Any]:
        """
        Persist the list of active markets.
        Accepts a list or a comma-separated string.
        """
        normalized = self._normalize_markets(markets)
        if not normalized:
            raise ValueError("At least one active market is required.")
        record = await parameter_repository.update_parameter_by_key(
            "active_markets",
            {"para_value": normalized}
        )
        return {
            "para_key": "active_markets",
            "para_value": record.get("para_value") if record else normalized,
            "message": "Active markets saved successfully"
        }

    async def get_active_market(self) -> str:
        """
        Convenience helper that returns the first configured active market.
        Defaults to 'DAM'.
        """
        markets = await self.get_active_markets()
        return markets[0] if markets else "DAM"

    async def set_active_market(self, market: str) -> Dict[str, Any]:
        """
        Convenience helper to set a single active market.
        """
        return await self.set_active_markets([market])

    async def get_active_exchange(self) -> str:
        """
        Return the currently configured active exchange for single-bid-table.
        Defaults to 'IEX' when not set or on error.
        """
        try:
            record = await parameter_repository.get_parameter_by_key("active_exchange")
            if record and record.get("para_value"):
                return str(record["para_value"]).strip().upper()
        except Exception as e:
            logger.error(f"Error reading active_exchange parameter: {e}")
        return "IEX"

    async def set_active_exchange(self, exchange: str) -> Dict[str, Any]:
        """
        Persist the active exchange parameter.
        """
        normalized = str(exchange).strip().upper()
        allowed = {"IEX", "PXIL", "HPX"}
        if normalized not in allowed:
            raise ValueError(f"Invalid active exchange '{exchange}'. Allowed: {sorted(allowed)}")
        record = await parameter_repository.update_parameter_by_key(
            "active_exchange",
            {"para_value": normalized}
        )
        return {
            "para_key": "active_exchange",
            "para_value": record.get("para_value") if record else normalized,
            "message": "Active exchange saved successfully"
        }

    # ------------------------------------------------------------------
    # Shift master (parameter key: shifts)
    # ------------------------------------------------------------------

    @staticmethod
    def _reject_reserved_parameter_key(para_key: Optional[str]) -> None:
        if para_key in RESERVED_PARAMETER_KEYS:
            raise ValueError(SHIFTS_RESERVED_MESSAGE)

    @staticmethod
    def _normalize_shift_name(shift_name: Any) -> str:
        name = str(shift_name or "").strip()
        if not name:
            raise ValueError("shift_name is required")
        return name

    @staticmethod
    def _normalize_shift_timing(shift_timing: Any) -> str:
        """Validate and return the string to persist.

        Hour-only values are stored without leading zeros ("01-08" → "1-8"),
        which is how existing rows are already saved. Minute values are stored
        exactly as submitted ("9:30-18:15", "1:00-8:30") so a later read returns
        the same text. "9-18" means 09:00–18:00 and is not rewritten with minutes.
        """
        _start, _end, stored = parse_shift_timing(shift_timing)
        return stored

    @staticmethod
    def _normalize_shift_record(raw: Any) -> Optional[Dict[str, str]]:
        if not isinstance(raw, dict):
            return None
        shift_id = str(raw.get("id") or "").strip()
        if not shift_id:
            return None
        try:
            return {
                "id": shift_id,
                "shift_name": ParameterService._normalize_shift_name(raw.get("shift_name")),
                "shift_timing": ParameterService._normalize_shift_timing(
                    raw.get("shift_timing")
                ),
            }
        except ValueError:
            logger.warning("Skipping invalid stored shift record: %r", raw)
            return None

    async def _load_shifts(self) -> List[Dict[str, str]]:
        record = await parameter_repository.get_parameter_by_key(SHIFTS_KEY)
        if not record:
            return []
        value = record.get("para_value")
        if not isinstance(value, list):
            return []
        shifts: List[Dict[str, str]] = []
        for item in value:
            normalized = self._normalize_shift_record(item)
            if normalized:
                shifts.append(normalized)
        return shifts

    async def _save_shifts(
        self,
        shifts: List[Dict[str, str]],
        user_id: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        payload: Dict[str, Any] = {"para_value": shifts}
        if user_id:
            payload["updated_by"] = user_id
        record = await parameter_repository.update_parameter_by_key(SHIFTS_KEY, payload)
        saved = record.get("para_value") if record else shifts
        if not isinstance(saved, list):
            return shifts
        return saved

    @staticmethod
    def _ensure_unique_shift_name(
        shifts: List[Dict[str, str]],
        shift_name: str,
        exclude_id: Optional[str] = None,
    ) -> None:
        target = shift_name.casefold()
        for shift in shifts:
            if exclude_id and shift.get("id") == exclude_id:
                continue
            if str(shift.get("shift_name") or "").casefold() == target:
                raise ValueError(f"Shift name '{shift_name}' already exists")

    async def get_shifts(self) -> List[Dict[str, str]]:
        try:
            return await self._load_shifts()
        except Exception as e:
            logger.error("Error fetching shifts: %s", e)
            raise

    async def create_shift(
        self,
        shift_name: Any,
        shift_timing: Any,
        user_id: Optional[str] = None,
    ) -> Dict[str, str]:
        name = self._normalize_shift_name(shift_name)
        timing = self._normalize_shift_timing(shift_timing)
        shifts = await self._load_shifts()
        self._ensure_unique_shift_name(shifts, name)
        created = {
            "id": str(uuid.uuid4()),
            "shift_name": name,
            "shift_timing": timing,
        }
        shifts.append(created)
        await self._save_shifts(shifts, user_id)
        return created

    async def update_shift(
        self,
        shift_id: str,
        shift_name: Any = None,
        shift_timing: Any = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, str]]:
        if shift_name is None and shift_timing is None:
            raise ValueError("No fields to update")
        shifts = await self._load_shifts()
        index = next((i for i, s in enumerate(shifts) if s.get("id") == shift_id), None)
        if index is None:
            return None
        updated = dict(shifts[index])
        if shift_name is not None:
            name = self._normalize_shift_name(shift_name)
            self._ensure_unique_shift_name(shifts, name, exclude_id=shift_id)
            updated["shift_name"] = name
        if shift_timing is not None:
            updated["shift_timing"] = self._normalize_shift_timing(shift_timing)
        shifts[index] = updated
        await self._save_shifts(shifts, user_id)
        return updated

    async def delete_shift(self, shift_id: str) -> bool:
        shifts = await self._load_shifts()
        remaining = [s for s in shifts if s.get("id") != shift_id]
        if len(remaining) == len(shifts):
            return False
        await self._save_shifts(remaining)
        return True


# Create singleton instance
parameter_service = ParameterService()
