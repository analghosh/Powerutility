"""
Parameter Service
Service layer for parameter business logic
"""

import logging
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


# Daily/Weekly markets report charge rates.
#
# These are rates, not measured data: EPMS holds no fee or charge dataset, and
# mplive carried them as literals in ReportsController@dailyReport. They live in
# `parameters` so PTC can correct them without a deploy, seeded here from the
# same values the report has always used so nothing moves on first run.
#
# GST is deliberately NOT editable yet. It is applied to the "Fees (incl. GST)"
# row only - Buy Cost and Sell Proceeds carry the bare fee - and whether that is
# right depends on a question PTC has not answered: is the per-MWh charge a
# transaction fee or a transmission charge, and does it already include GST?
# Until that is settled, changing the percentage would move two rows and leave
# two others behind, so the field is served read-only.
REPORT_CHARGE_RATE_KEYS = {
    "reports_fee_rate_per_mwh": {
        "default": "20",
        "description": "Per-MWh charge applied to cleared volume (assumed GST-exclusive)",
        "editable": True,
        "minimum": 0.0,
    },
    "reports_gst_percent": {
        "default": "18",
        "description": "GST on the per-MWh charge, percent. Applied to the Fees row only",
        "editable": False,
        "minimum": 0.0,
        "maximum": 100.0,
    },
    "reports_sldc_flat_charge": {
        "default": "3000",
        "description": "Flat SLDC scheduling and operating charge per day when any bid exists",
        "editable": True,
        "minimum": 0.0,
    },
    "reports_fcr_rate_per_mu": {
        "default": "200000",
        "description": "Fixed cost recovery rate per MU of cleared sell volume",
        "editable": True,
        "minimum": 0.0,
    },
}

REPORT_FEE_RATE_BASIS = (
    "GST-exclusive. Applied to the Fees row only - Buy Cost and Sell Proceeds "
    "carry the bare per-MWh charge."
)

# Shown when a write to a locked rate is refused. Kept separate from the basis
# text so shortening one does not leave the other without a reason.
REPORT_RATE_LOCKED_REASON = "It applies to the Fees row only."


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
        # A locked report charge rate has to stay locked whichever route reaches
        # it. Guarding only the dedicated endpoint left this generic path able to
        # change the same row, which made the lock decorative.
        await self._reject_locked_report_rate_update(parameter_id, update_data)
        try:
            existing = await parameter_repository.get_parameter_by_id(parameter_id)
            if not existing:
                return None

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
            return await parameter_repository.delete_parameter(parameter_id)
            
        except Exception as e:
            logger.error(f"Error in parameter service delete: {e}")
            raise

    # ------------------------------------------------------------------
    # Bid price defaults (parameter master)
    # ------------------------------------------------------------------

    async def _reject_locked_report_rate_update(
        self,
        parameter_id: str,
        update_data: Dict[str, Any],
    ) -> None:
        """Raise if a write would change a report charge rate marked non-editable."""
        if "para_value" not in update_data:
            return
        try:
            existing = await parameter_repository.get_parameter_by_id(parameter_id)
        except Exception:
            return
        if not existing:
            return
        spec = REPORT_CHARGE_RATE_KEYS.get(existing.get("para_key"))
        if spec and not spec["editable"]:
            raise ValueError(
                f"'{existing['para_key']}' is not editable. {REPORT_RATE_LOCKED_REASON}"
            )

    async def ensure_report_charge_rate_defaults(self) -> Dict[str, Any]:
        """
        Ensure the four daily/weekly report charge rates exist in `parameters`.
        Creates missing keys with their defaults; never overwrites a value PTC
        has already set.
        """
        created: Dict[str, Any] = {}
        for para_key, spec in REPORT_CHARGE_RATE_KEYS.items():
            existing = await parameter_repository.get_parameter_by_key(para_key)
            if existing:
                continue
            await parameter_repository.create_parameter(
                {
                    "para_key": para_key,
                    "para_value": spec["default"],
                    "description": spec["description"],
                }
            )
            created[para_key] = spec["default"]
            logger.info(
                "Auto-created report charge rate '%s' with default %s",
                para_key,
                spec["default"],
            )
        return created

    async def get_report_charge_rates(self) -> Dict[str, Any]:
        """
        Return the four report charge rates, plus which source each came from.

        `parameters` stores values as strings with no schema, so a hand-edited
        row can hold anything. Any value that will not parse, or is out of
        range, falls back to the seeded default rather than propagating a zero
        into every money row on the report. `source` reports which applied so a
        bad edit surfaces instead of silently changing every figure.
        """
        await self.ensure_report_charge_rate_defaults()

        values: Dict[str, float] = {}
        sources: Dict[str, str] = {}
        for para_key, spec in REPORT_CHARGE_RATE_KEYS.items():
            default = float(spec["default"])
            record = await parameter_repository.get_parameter_by_key(para_key)
            raw = record.get("para_value") if record else None
            resolved, source = default, "default"
            if raw is not None:
                try:
                    candidate = float(str(raw).strip())
                except (TypeError, ValueError):
                    logger.warning(
                        "Report charge rate '%s' is not numeric (%r); using default %s",
                        para_key, raw, default,
                    )
                else:
                    low = spec.get("minimum")
                    high = spec.get("maximum")
                    if (low is not None and candidate < low) or (
                        high is not None and candidate > high
                    ):
                        logger.warning(
                            "Report charge rate '%s' out of range (%s); using default %s",
                            para_key, candidate, default,
                        )
                    else:
                        resolved, source = candidate, "parameters"
            values[para_key] = resolved
            sources[para_key] = source

        gst_percent = values["reports_gst_percent"]
        return {
            "fee_rate_per_mwh": values["reports_fee_rate_per_mwh"],
            # Stored as a percent so that 0 means "no GST" rather than "no fee";
            # a stored multiplier would let 0 silently delete the charge.
            "gst_percent": gst_percent,
            "gst_multiplier": 1 + gst_percent / 100,
            "sldc_flat_charge": values["reports_sldc_flat_charge"],
            "fcr_rate_per_mu": values["reports_fcr_rate_per_mu"],
            "sources": sources,
            "fee_rate_basis": REPORT_FEE_RATE_BASIS,
        }

    async def save_report_charge_rates(
        self, updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Update editable report charge rates. Rejects unknown keys, non-numeric
        values, out-of-range values, and any attempt to change a rate marked
        non-editable.
        """
        if not updates:
            raise ValueError("No charge rates supplied")

        parsed: Dict[str, float] = {}
        for para_key, raw in updates.items():
            spec = REPORT_CHARGE_RATE_KEYS.get(para_key)
            if spec is None:
                raise ValueError(f"Unknown charge rate '{para_key}'")
            if not spec["editable"]:
                raise ValueError(
                    f"'{para_key}' is not editable. {REPORT_RATE_LOCKED_REASON}"
                )
            try:
                value = float(str(raw).strip())
            except (TypeError, ValueError):
                raise ValueError(f"'{para_key}' must be a number, got {raw!r}")
            low, high = spec.get("minimum"), spec.get("maximum")
            if low is not None and value < low:
                raise ValueError(f"'{para_key}' must be at least {low}")
            if high is not None and value > high:
                raise ValueError(f"'{para_key}' must be at most {high}")
            parsed[para_key] = value

        await self.ensure_report_charge_rate_defaults()
        previous: Dict[str, Any] = {}
        for para_key, value in parsed.items():
            record = await parameter_repository.get_parameter_by_key(para_key)
            previous[para_key] = record.get("para_value") if record else None
            await parameter_repository.update_parameter_by_key(
                para_key, {"para_value": str(value)}
            )

        return {"updated": parsed, "previous": previous}

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


# Create singleton instance
parameter_service = ParameterService()
