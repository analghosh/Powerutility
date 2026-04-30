"""
Provider Seeder for Ingestion Service
Seeds default provider configurations including forecast API provider
"""

import logging
from typing import List, Dict, Any
from datetime import datetime
from libs.database.mongo_client_factory import get_mongo_client
from services.ingestion.app.core.config import settings

logger = logging.getLogger(__name__)

# Default providers to seed
DEFAULT_PROVIDERS = [
    {
        "code": "forecast_api_provider",
        "name": "Forecast API Provider",
        "provider_type": "forecast_api",
        "data_type": "FORECAST",
        "ingestion_type": "CRON",  # Using CRON instead of API to avoid being picked up by api_raw_data_processor
        "enabled": True,
        "description": "Fetch forecast data from external API and store in limit_values and demand_serve",
        "metadata": {
            "api_url": "url",
            "api_key": "key",  # API key to be set manually by user
            "auth_header": "X-API-Key",
            "date_range_days": 2,
            "area": "Puducherry",
            "timeout": 30
        }
    },
    {
        "code": "schedule_api_provider",
        "name": "Schedule API Provider",
        "provider_type": "schedule_api",
        "data_type": "SCHEDULE",
        "ingestion_type": "CRON",  # Separate cron job, runs at 08:15 and 14:15
        "enabled": True,
        "description": "Fetch schedule data from external API and store in supply_serve",
        "metadata": {
            "api_url": "http://192.168.8.249:8004/api/v1/schedule",
            "api_key": "key",  # API key to be set manually by user
            "auth_header": "X-API-Key",
            "timeout": 30
        }
    },
    {
        "code": "rtm_forecast_api_provider",
        "name": "RTM Forecast API Provider",
        "provider_type": "rtm_forecast_api",
        "data_type": "SCHEDULE",
        "ingestion_type": "CRON",
        "enabled": True,
        "description": "Fetch RTM forecast/schedule from external API into serve_dam_scheduling (market_type=RTM)",
        "metadata": {
            "api_url": "url",
            "api_key": "key",
            "auth_header": "X-API-Key",
            "date_range_days": 1,
            "timeout": 30
        }
    },
    {
        "code": "hpx_exchange_provider",
        "name": "HPX Exchange Data Provider",
        "provider_type": "hpx_exchange", # Logic will use metadata.parser_type or logic
        "data_type": "EXCHANGE",
        "ingestion_type": "API",
        "enabled": True,
        "description": "Fetch HPX exchange data from HPX India API",
        "metadata": {
            "api_url": "https://api.hpxindia.com/api/getDataMarkSnapDAMMS/w",
            "method": "GET",
            "params": {
                "deliveryperiod": "Today",
                "interval": "15-Minute Block"
            },
            "headers": {
                "accept": "application/json, text/plain, */*",
                "accept-language": "en-US,en;q=0.9",
                "origin": "https://www.hpxindia.com",
                "priority": "u=1, i",
                "referer": "https://www.hpxindia.com/",
                "sec-ch-ua": '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-site",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
                "Cookie": "AlteonP=AQJAIHq1qMD9jMVOQG7UPg$$"
            },
            "response_format": "json",
            "parser_type": "hpx_exchange",
            "source": "HPX",
            "config": {
                "market_type": "DAM"
            }
        },
        "fields": [
            {"field_name": "scheduled_at", "field_type": "datetime", "is_required": True},
            {"field_name": "time_block", "field_type": "integer", "is_required": True},
            {"field_name": "buy_bid", "field_type": "float"},
            {"field_name": "sell_bid", "field_type": "float"},
            {"field_name": "mcv", "field_type": "float"},
            {"field_name": "final_scheduled_volume", "field_type": "float"},
            {"field_name": "mcp", "field_type": "float"},
            {"field_name": "acv", "field_type": "float"},
            {"field_name": "acp", "field_type": "float"},
            {"field_name": "px_name", "field_type": "string"},
            {"field_name": "market_type", "field_type": "string"},
        ]
    },
    {
        "code": "hpx_exchange_rtm_provider",
        "name": "HPX RTM Exchange Data Provider",
        "provider_type": "hpx_exchange",
        "data_type": "EXCHANGE",
        "ingestion_type": "API",
        "enabled": True,
        "description": "Fetch HPX RTM exchange data from HPX India API",
        "metadata": {
            "api_url": "https://api.hpxindia.com/api/getRTMMSDataMarkSnap1/w",
            "method": "GET",
            "params": {
                "deliveryperiod": "Today",
                "flag": "web",
                "interval": "15-Minute Block"
            },
            "headers": {
                "accept": "application/json, text/plain, */*",
                "accept-language": "en-US,en;q=0.9",
                "origin": "https://www.hpxindia.com",
                "priority": "u=1, i",
                "referer": "https://www.hpxindia.com/",
                "sec-ch-ua": '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-site",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
                "Cookie": "AlteonP=AQJAIHq1qMD9jMVOQG7UPg$$"
            },
            "response_format": "json",
            "parser_type": "hpx_rtm_exchange",
            "source": "HPX",
            "config": {
                "market_type": "RTM"
            }
        },
        "fields": [
            {"field_name": "scheduled_at", "field_type": "datetime", "is_required": True},
            {"field_name": "time_block", "field_type": "integer", "is_required": True},
            {"field_name": "buy_bid", "field_type": "float"},
            {"field_name": "sell_bid", "field_type": "float"},
            {"field_name": "mcv", "field_type": "float"},
            {"field_name": "final_scheduled_volume", "field_type": "float"},
            {"field_name": "mcp", "field_type": "float"},
            {"field_name": "acv", "field_type": "float"},
            {"field_name": "acp", "field_type": "float"},
            {"field_name": "px_name", "field_type": "string"},
            {"field_name": "market_type", "field_type": "string"},
        ]
    },
    {
        "code": "hpx_exchange_hpdam_provider",
        "name": "HPX HPDAM Exchange Data Provider",
        "provider_type": "hpx_exchange",
        "data_type": "EXCHANGE",
        "ingestion_type": "API",
        "enabled": True,
        "description": "Fetch HPX HPDAM exchange data from HPX India API",
        "metadata": {
            "api_url": "https://api.hpxindia.com/api/getDataMarkSnapHPDAMMarket/w",
            "method": "GET",
            "params": {
                "deliveryperiod": "Today",
                "flag": "web",
                "interval": "15-Minute Block"
            },
            "headers": {
                "accept": "application/json, text/plain, */*",
                "accept-language": "en-US,en;q=0.9",
                "origin": "https://www.hpxindia.com",
                "priority": "u=1, i",
                "referer": "https://www.hpxindia.com/",
                "sec-ch-ua": '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-site",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
                "Cookie": "AlteonP=AQJAIHq1qMD9jMVOQG7UPg$$"
            },
            "response_format": "json",
            "parser_type": "hpx_exchange",  # Reusing DAM parser
            "source": "HPX",
            "config": {
                "market_type": "HPDAM"
            }
        },
        "fields": [
            {"field_name": "scheduled_at", "field_type": "datetime", "is_required": True},
            {"field_name": "time_block", "field_type": "integer", "is_required": True},
            {"field_name": "buy_bid", "field_type": "float"},
            {"field_name": "sell_bid", "field_type": "float"},
            {"field_name": "mcv", "field_type": "float"},
            {"field_name": "final_scheduled_volume", "field_type": "float"},
            {"field_name": "mcp", "field_type": "float"},
            {"field_name": "acv", "field_type": "float"},
            {"field_name": "acp", "field_type": "float"},
            {"field_name": "px_name", "field_type": "string"},
            {"field_name": "market_type", "field_type": "string"},
        ]
    },
    {
        "code": "hpx_gdam_provider",
        "name": "HPX Green Day Ahead Market Provider",
        "provider_type": "hpx_green_day_ahead",
        "data_type": "GREEN_DAY_AHEAD",
        "ingestion_type": "API",
        "enabled": True,
        "description": "Fetch HPX Green Day Ahead Market data",
        "metadata": {
            "api_url": "https://api.hpxindia.com/api/getGDAMDataMarkSnap1/w",
            "method": "GET",
            "params": {
                "deliveryperiod": "Tomorrow",
                "flag": "web",
                "fromdate": "",
                "frommonth": "",
                "fromweek": "",
                "interval": "15-Minute Block",
                "todate": "",
                "tomonth": "",
                "toweek": "",
                "years": ""
            },
            "headers": {
                "accept": "application/json, text/plain, */*",
                "accept-language": "en-US,en;q=0.9",
                "origin": "https://www.hpxindia.com",
                "priority": "u=1, i",
                "referer": "https://www.hpxindia.com/",
                "sec-ch-ua": '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-site",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
                "Cookie": "AlteonP=AQJAIHq1qMD9jMVOQG7UPg$$"
            },
            "response_format": "json",
            "parser_type": "hpx_green_day_ahead",
            "source": "HPX",
            "config": {
                "market_type": "GDAM"
            }
        },
        "fields": [
            {"field_name": "date", "field_type": "string", "is_required": True},
            {"field_name": "time_block", "field_type": "string", "is_required": True},
            {"field_name": "buy_bid", "field_type": "float"},
            {"field_name": "sell_bid", "field_type": "object"},
            {"field_name": "mcv", "field_type": "object"},
            {"field_name": "final_scheduled_volume", "field_type": "object"},
            {"field_name": "mcp", "field_type": "float"},
            {"field_name": "data_type", "field_type": "string"},
            {"field_name": "source", "field_type": "string"},
        ]
    },
    {
        "code": "pxil_rtm_provider",
        "name": "PXIL RTM Provider",
        "provider_type": "pxil_rtm",
        "data_type": "EXCHANGE",
        "ingestion_type": "API",
        "enabled": True,
        "description": "Fetch PXIL RTM data",
        "metadata": {
            "api_url": "https://powerexindia.in/get_rtm_daily_data/",
            "method": "POST",
            "params": {},
            "headers": {
                "accept": "application/json, text/javascript, */*; q=0.01",
                "accept-language": "en-US,en;q=0.9",
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "origin": "https://powerexindia.in",
                "priority": "u=1, i",
                "referer": "https://powerexindia.in/Pages/Market.html",
                "sec-ch-ua": '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
                "x-requested-with": "XMLHttpRequest"
            },
            "body_type": "form_urlencoded",
            "body": {
                "from_date": "{today}",
                "to_date": "{today}",
                "interval": "15-min_block"
            },
            "response_format": "json",
            "parser_type": "pxil_rtm",
            "source": "PXIL",
            "config": {
                "market_type": "RTM"
            }
        },
        "fields": [
            {"field_name": "delivery", "field_type": "string", "is_required": True},
            {"field_name": "time_block", "field_type": "string", "is_required": True},
            {"field_name": "purchase_bid", "field_type": "mixed"},
            {"field_name": "sell_bid", "field_type": "mixed"},
            {"field_name": "mcv", "field_type": "mixed"},
            {"field_name": "final_mcv", "field_type": "mixed"},
            {"field_name": "mcp", "field_type": "mixed"}
        ]
    },
    {
        "code": "pxil_dam_provider",
        "name": "PXIL DAM Provider",
        "provider_type": "pxil_dam",
        "data_type": "EXCHANGE",
        "ingestion_type": "API",
        "enabled": True,
        "description": "Fetch PXIL DAM data",
        "metadata": {
            "api_url": "https://powerexindia.in/get_idas_daily_data/",
            "method": "POST",
            "params": {},
            "headers": {
                "accept": "application/json, text/javascript, */*; q=0.01",
                "accept-language": "en-US,en;q=0.9",
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "origin": "https://powerexindia.in",
                "priority": "u=1, i",
                "referer": "https://powerexindia.in/Pages/Market.html",
                "sec-ch-ua": '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
                "x-requested-with": "XMLHttpRequest"
            },
            "body_type": "form_urlencoded",
            "body": {
                "from_date": "{today}",
                "to_date": "{today}",
                "source_type": "DAM",
                "interval": "15-min_block"
            },
            "response_format": "json",
            "parser_type": "pxil_dam",
            "source": "PXIL",
            "config": {
                "market_type": "DAM"
            }
        },
        "fields": [
            {"field_name": "delivery", "field_type": "string", "is_required": True},
            {"field_name": "time_block", "field_type": "string", "is_required": True},
            {"field_name": "purchase_bid", "field_type": "mixed"},
            {"field_name": "sell_bid", "field_type": "mixed"},
            {"field_name": "mcv", "field_type": "mixed"},
            {"field_name": "final_mcv", "field_type": "mixed"},
            {"field_name": "mcp", "field_type": "mixed"}
        ]
    },
    {
        "code": "pxil_gdam_provider",
        "name": "PXIL GDAM Provider",
        "provider_type": "pxil_gdam",
        "data_type": "EXCHANGE",
        "ingestion_type": "API",
        "enabled": True,
        "description": "Fetch PXIL GDAM data",
        "metadata": {
            "api_url": "https://powerexindia.in/get_idas_daily_data/",
            "method": "POST",
            "params": {},
            "headers": {
                "accept": "application/json, text/javascript, */*; q=0.01",
                "accept-language": "en-US,en;q=0.9",
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "origin": "https://powerexindia.in",
                "priority": "u=1, i",
                "referer": "https://powerexindia.in/Pages/Market.html",
                "sec-ch-ua": '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
                "x-requested-with": "XMLHttpRequest"
            },
            "body_type": "form_urlencoded",
            "body": {
                "from_date": "{today}",
                "to_date": "{today}",
                "source_type": "GDAM",
                "interval": "15-min_block"
            },
            "response_format": "json",
            "parser_type": "pxil_gdam",
            "source": "PXIL",
            "config": {
                "market_type": "GDAM"
            }
        },
        "fields": [
            {"field_name": "delivery", "field_type": "string", "is_required": True},
            {"field_name": "time_block", "field_type": "string", "is_required": True},
            {"field_name": "purchase_bid", "field_type": "mixed"},
            {"field_name": "sell_bid", "field_type": "mixed"},
            {"field_name": "mcv", "field_type": "mixed"},
            {"field_name": "final_mcv", "field_type": "mixed"},
            {"field_name": "mcp", "field_type": "mixed"}
        ]
    },
    {
        "code": "pxil_hpdam_provider",
        "name": "PXIL HPDAM Provider",
        "provider_type": "pxil_hpdam",
        "data_type": "EXCHANGE",
        "ingestion_type": "API",
        "enabled": True,
        "description": "Fetch PXIL HPDAM data",
        "metadata": {
            "api_url": "https://powerexindia.in/get_idas_daily_data/",
            "method": "POST",
            "params": {},
            "headers": {
                "accept": "application/json, text/javascript, */*; q=0.01",
                "accept-language": "en-US,en;q=0.9",
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "origin": "https://powerexindia.in",
                "priority": "u=1, i",
                "referer": "https://powerexindia.in/Pages/Market.html",
                "sec-ch-ua": '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
                "x-requested-with": "XMLHttpRequest"
            },
            "body_type": "form_urlencoded",
            "body": {
                "from_date": "{today}",
                "to_date": "{today}",
                "source_type": "HPDAM",
                "interval": "15-min_block"
            },
            "response_format": "json",
            "parser_type": "pxil_hpdam",
            "source": "PXIL",
            "config": {
                "market_type": "HPDAM"
            }
        },
        "fields": [
            {"field_name": "delivery", "field_type": "string", "is_required": True},
            {"field_name": "time_block", "field_type": "string", "is_required": True},
            {"field_name": "purchase_bid", "field_type": "mixed"},
            {"field_name": "sell_bid", "field_type": "mixed"},
            {"field_name": "mcv", "field_type": "mixed"},
            {"field_name": "final_mcv", "field_type": "mixed"},
            {"field_name": "mcp", "field_type": "mixed"}
        ]
    },
    {
        "code": "hpx_exchange_trade_dam_provider",
        "name": "HPX Exchange Trade DAM Provider",
        "provider_type": "hpx_exchange_trade",
        "data_type": "exchange_trade",
        "ingestion_type": "api",
        "enabled": True,
        "description": "Fetch HPX Exchange Trade DAM data",
        "metadata": {
            "api_url": "https://api.hpxindia.com/api/GetStateViewGetData/w",
            "method": "GET",
            "params": {
                "fromdate": "{tomorrow:%Y-%m-%d}",
                "todate": "{tomorrow:%Y-%m-%d}",
                "segment": "DAM",
                "state": "PONDICHERRY (PUDUCHERRY)"
            },
            "headers": {
                "accept": "application/json, text/plain, */*",
                "accept-language": "en-US,en;q=0.9",
                "origin": "https://www.hpxindia.com",
                "referer": "https://www.hpxindia.com/"
            },
            "response_format": "json",
            "source": "HPX",
            "config": {
                "market_type": "DAM"
            }
        },
        "fields": []
    },
    {
        "code": "hpx_exchange_trade_gdam_provider",
        "name": "HPX Exchange Trade GDAM Provider",
        "provider_type": "hpx_exchange_trade",
        "data_type": "exchange_trade",
        "ingestion_type": "api",
        "enabled": True,
        "description": "Fetch HPX Exchange Trade GDAM data",
        "metadata": {
            "api_url": "https://api.hpxindia.com/api/GetStateViewGetData/w",
            "method": "GET",
            "params": {
                "fromdate": "{tomorrow:%Y-%m-%d}",
                "todate": "{tomorrow:%Y-%m-%d}",
                "segment": "GDAM",
                "state": "PONDICHERRY (PUDUCHERRY)"
            },
            "headers": {
                "accept": "application/json, text/plain, */*",
                "accept-language": "en-US,en;q=0.9",
                "origin": "https://www.hpxindia.com",
                "referer": "https://www.hpxindia.com/"
            },
            "response_format": "json",
            "source": "HPX",
            "config": {
                "market_type": "GDAM"
            }
        },
        "fields": []
    },
    {
        "code": "hpx_exchange_trade_hpdam_provider",
        "name": "HPX Exchange Trade HPDAM Provider",
        "provider_type": "hpx_exchange_trade",
        "data_type": "exchange_trade",
        "ingestion_type": "api",
        "enabled": True,
        "description": "Fetch HPX Exchange Trade HPDAM data",
        "metadata": {
            "api_url": "https://api.hpxindia.com/api/GetStateViewGetData/w",
            "method": "GET",
            "params": {
                "fromdate": "{tomorrow:%Y-%m-%d}",
                "todate": "{tomorrow:%Y-%m-%d}",
                "segment": "HPDAM",
                "state": "PONDICHERRY (PUDUCHERRY)"
            },
            "headers": {
                "accept": "application/json, text/plain, */*",
                "accept-language": "en-US,en;q=0.9",
                "origin": "https://www.hpxindia.com",
                "referer": "https://www.hpxindia.com/"
            },
            "response_format": "json",
            "source": "HPX",
            "config": {
                "market_type": "HPDAM"
            }
        },
        "fields": []
    },
    {
        "code": "hpx_exchange_trade_rtm_provider",
        "name": "HPX Exchange Trade RTM Provider",
        "provider_type": "hpx_exchange_trade",
        "data_type": "exchange_trade",
        "ingestion_type": "api",
        "enabled": True,
        "description": "Fetch HPX Exchange Trade RTM data",
        "metadata": {
            "api_url": "https://api.hpxindia.com/api/GetStateViewGetData/w",
            "method": "GET",
            "params": {
                "fromdate": "{today:%Y-%m-%d}",
                "todate": "{today:%Y-%m-%d}",
                "segment": "RTM",
                "state": "PONDICHERRY (PUDUCHERRY)"
            },
            "headers": {
                "accept": "application/json, text/plain, */*",
                "accept-language": "en-US,en;q=0.9",
                "origin": "https://www.hpxindia.com",
                "referer": "https://www.hpxindia.com/"
            },
            "response_format": "json",
            "source": "HPX",
            "config": {
                "market_type": "RTM"
            }
        },
        "fields": []
    },
    {
        "code": "pxil_exchange_dsm_dam_provider",
        "name": "PXIL Exchange DSM DAM Provider",
        "provider_type": "pxil_exchange_dsm",
        "data_type": "exchange_dsm",
        "ingestion_type": "api",
        "enabled": True,
        "description": "Fetch PXIL Exchange DSM DAM data",
        "metadata": {
            "api_url": "https://powerexindia.in/get_dsm_data/",
            "method": "POST",
            "body_type": "form_urlencoded",
            "parser_type": "pxil_dsm",
            "headers": {
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8"
            },
            "body": {
                "from_date": "{tomorrow:%Y-%m-%d}",
                "to_date": "{tomorrow:%Y-%m-%d}",
                "segment": "DAM",
                "state": "S2"
            },
            "response_format": "json",
            "source": "PXIL",
            "config": {
                "market_type": "DAM",
                "px_name": "PXIL"
            }
        },
        "fields": []
    },
    {
        "code": "pxil_exchange_dsm_gdam_provider",
        "name": "PXIL Exchange DSM GDAM Provider",
        "provider_type": "pxil_exchange_dsm",
        "data_type": "exchange_dsm",
        "ingestion_type": "api",
        "enabled": True,
        "description": "Fetch PXIL Exchange DSM GDAM data",
        "metadata": {
            "api_url": "https://powerexindia.in/get_dsm_data/",
            "method": "POST",
            "body_type": "form_urlencoded",
            "parser_type": "pxil_dsm",
            "headers": {
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8"
            },
            "body": {
                "from_date": "{tomorrow:%Y-%m-%d}",
                "to_date": "{tomorrow:%Y-%m-%d}",
                "segment": "GDAM",
                "state": "S2"
            },
            "response_format": "json",
            "source": "PXIL",
            "config": {
                "market_type": "GDAM",
                "px_name": "PXIL"
            }
        },
        "fields": []
    },
    {
        "code": "pxil_exchange_dsm_hpdam_provider",
        "name": "PXIL Exchange DSM HPDAM Provider",
        "provider_type": "pxil_exchange_dsm",
        "data_type": "exchange_dsm",
        "ingestion_type": "api",
        "enabled": True,
        "description": "Fetch PXIL Exchange DSM HPDAM data",
        "metadata": {
            "api_url": "https://powerexindia.in/get_dsm_data/",
            "method": "POST",
            "body_type": "form_urlencoded",
            "parser_type": "pxil_dsm",
            "headers": {
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8"
            },
            "body": {
                "from_date": "{tomorrow:%Y-%m-%d}",
                "to_date": "{tomorrow:%Y-%m-%d}",
                "segment": "HPDAM",
                "state": "S2"
            },
            "response_format": "json",
            "source": "PXIL",
            "config": {
                "market_type": "HPDAM",
                "px_name": "PXIL"
            }
        },
        "fields": []
    },
    {
        "code": "pxil_exchange_dsm_rtm_provider",
        "name": "PXIL Exchange DSM RTM Provider",
        "provider_type": "pxil_exchange_dsm",
        "data_type": "exchange_dsm",
        "ingestion_type": "api",
        "enabled": True,
        "description": "Fetch PXIL Exchange DSM RTM data",
        "metadata": {
            "api_url": "https://powerexindia.in/get_dsm_data/",
            "method": "POST",
            "body_type": "form_urlencoded",
            "parser_type": "pxil_dsm",
            "headers": {
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8"
            },
            "body": {
                "from_date": "{today:%Y-%m-%d}",
                "to_date": "{today:%Y-%m-%d}",
                "segment": "RTM",
                "state": "S2"
            },
            "response_format": "json",
            "source": "PXIL",
            "config": {
                "market_type": "RTM",
                "px_name": "PXIL"
            }
        },
        "fields": []
    }
]


class ProviderSeeder:
    """Seeder for provider configurations"""
    
    def __init__(self):
        self.client = get_mongo_client(settings.MONGODB_URI)
        self.db = self.client[settings.MONGODB_DATABASE]
        self.collection = self.db.providers

    async def seed(self):
        """Seed default providers if they don't exist"""
        logger.info("Checking for default provider configurations...")
        
        for provider_data in DEFAULT_PROVIDERS:
            code = provider_data["code"]
            
            # Upsert provider (Insert if new, Update if exists)
            logger.info(f"Seeding/Updating provider: {code}")
            
            # Update timestamps
            now = datetime.utcnow()
            provider_data["updated_at"] = now
            if "created_at" not in provider_data:
                 # existing records might have created_at, we don't want to overwrite it if we were reading from DB, 
                 # but here provider_data is from DEFAULT_PROVIDERS which is a dict. 
                 # We should probably use $setOnInsert for created_at if we want to be strict, 
                 # but for simplicity in this script, we can just set it.
                 # Actually, let's use pymongo's update_one with upsert.
                 pass

            # If provider already exists, preserve its metadata values.
            # This prevents overwriting user-configured values like api_url/api_key on service restarts,
            # while still allowing newly introduced default keys to be added.
            existing = await self.collection.find_one({"code": code}) or {}
            existing_metadata = existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}
            new_metadata = provider_data.get("metadata") if isinstance(provider_data.get("metadata"), dict) else {}
            merged_metadata = {**new_metadata, **existing_metadata}  # existing wins

            provider_to_set = dict(provider_data)
            if new_metadata or existing_metadata:
                provider_to_set["metadata"] = merged_metadata

            # Prepare update dict
            update_data = {
                "$set": provider_to_set,
                "$setOnInsert": {"created_at": now}
            }
            # Remove created_at from $set to avoid overwriting it on update
            if "created_at" in update_data["$set"]:
                del update_data["$set"]["created_at"]

            await self.collection.update_one(
                {"code": code},
                update_data,
                upsert=True
            )
            logger.info(f"Provider {code} seeded/updated successfully")

def seed_providers():
    """Function to run seeder"""
    seeder = ProviderSeeder()
    seeder.seed()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seed_providers()
