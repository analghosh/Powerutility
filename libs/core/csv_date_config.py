"""
CSV upload date format flags (manual upload only).

Environment variables (all optional; defaults = DD-MM-YYYY only):
  CSV_DATE_DD_MM_YYYY_ENABLED=true
  CSV_DATE_YYYY_MM_DD_ENABLED=false
  CSV_DATE_DD_MM_SLASH_ENABLED=false
  CSV_DATE_ABBREV_MONTH_ENABLED=false

"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class CsvDateSettings(BaseSettings):
    CSV_DATE_DD_MM_YYYY_ENABLED: bool = True
    CSV_DATE_YYYY_MM_DD_ENABLED: bool = False
    CSV_DATE_DD_MM_SLASH_ENABLED: bool = False
    CSV_DATE_ABBREV_MONTH_ENABLED: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_csv_date_settings() -> CsvDateSettings:
    return CsvDateSettings()
