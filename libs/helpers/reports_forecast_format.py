"""
Response format options for reports-forecast API.
"""

from typing import Literal, Set

ReportsForecastResponseFormat = Literal["json", "csv", "both"]

REPORTS_FORECAST_FORMAT_JSON: ReportsForecastResponseFormat = "json"
REPORTS_FORECAST_FORMAT_CSV: ReportsForecastResponseFormat = "csv"
REPORTS_FORECAST_FORMAT_BOTH: ReportsForecastResponseFormat = "both"

VALID_REPORTS_FORECAST_FORMATS: Set[str] = {
    REPORTS_FORECAST_FORMAT_JSON,
    REPORTS_FORECAST_FORMAT_CSV,
    REPORTS_FORECAST_FORMAT_BOTH,
}


def normalize_reports_forecast_format(value: str) -> ReportsForecastResponseFormat:
    """Validate and normalize the response format query parameter."""
    normalized = (value or REPORTS_FORECAST_FORMAT_JSON).strip().lower()
    if normalized not in VALID_REPORTS_FORECAST_FORMATS:
        raise ValueError(
            f"format must be one of: {', '.join(sorted(VALID_REPORTS_FORECAST_FORMATS))}"
        )
    return normalized  # type: ignore[return-value]


def reports_forecast_includes_json_sections(response_format: str) -> bool:
    return response_format in (REPORTS_FORECAST_FORMAT_JSON, REPORTS_FORECAST_FORMAT_BOTH)


def reports_forecast_includes_flat_records(
    response_format: str,
    *,
    include_flat_records: bool = True,
) -> bool:
    if response_format == REPORTS_FORECAST_FORMAT_CSV:
        return True
    if response_format == REPORTS_FORECAST_FORMAT_BOTH:
        return True
    return include_flat_records


def reports_forecast_includes_csv_content(response_format: str) -> bool:
    return response_format == REPORTS_FORECAST_FORMAT_BOTH
