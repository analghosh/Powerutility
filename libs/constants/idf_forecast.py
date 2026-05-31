"""Constants for IDF forecast switch and merge."""

from typing import Optional

IDF_FORECAST_SWITCH_KEY = "idf_forecast_switch"
IDF_FORECAST_SWITCH_LAST_RESET_KEY = "idf_forecast_switch_last_reset_date"
IDF_FORECAST_SWITCH_AUTO_FALLBACK_MESSAGE_KEY = "idf_forecast_switch_auto_fallback_message"

MANUAL_AUTO_FLIP_ACTIVE_SESSION_CSV_MESSAGE = (
    "Active session has zero / empty data for forecast in CSV; "
    "switched automatically to Auto mode."
)

TRUE_VALUES = frozenset({"true", "1", "yes"})
FALSE_VALUES = frozenset({"false", "0", "no"})


def parse_switch_use_rtm_api(para_value: Optional[str], default: bool = True) -> bool:
    if para_value is None:
        return default
    normalized = str(para_value).strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return default


def switch_para_value(use_rtm_api: bool) -> str:
    return "true" if use_rtm_api else "false"
