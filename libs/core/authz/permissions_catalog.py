# libs/core/authz/permissions_catalog.py
"""
Permissions catalog management for RBAC system
"""

from typing import Set

# Standard permissions catalog - includes all EPMS menu permissions
DEFAULT_PERMISSIONS_CATALOG = [
    # Admin permissions
    "admin:*",
    
    # User management
    "users:read",
    "users:write", 
    "users:create",
    "users:delete",
    
    # Role management
    "roles:read",
    "roles:write",
    "roles:create", 
    "roles:delete",
    
    # Menu management
    "menu:read",
    "menu:write",
    "menu:create",
    "menu:delete",
    
    # Dashboard
    "dashboard:read",
    "dashboard:write",
    
    # Overview
    "overview:read",
    "overview:write",
    "management_snapshot:read",
    "management_snapshot:write",
    "dsm_overview:read",
    "dsm_overview:write",
    "market_integrator:read",
    "market_integrator:write",
    
    # Forecast
    "forecast:read",
    "forecast:write",
    "forecast_analysis:read",
    "forecast_analysis:write",
    "demand_forecast:read",
    "demand_forecast:write",
    
    # Supply
    "supply:read",
    "supply:write",
    "non-renewable_supply:read",
    "non-renewable_supply:write",
    "renewable_supply:read",
    "renewable_supply:write",
    "urs:read",
    "urs:write",
    
    # Day Ahead Marketing
    "day_ahead_marketing:read",
    "day_ahead_marketing:write",
    "dam_scheduling:read",
    "dam_scheduling:write",
    "dam_block_bid:read",
    "dam_block_bid:write",
    "dam_single_bid:read",
    "dam_single_bid:write",
    "dam_bid_status:read",
    "dam_bid_status:write",
    
    # Real Time Market
    "real_time_market:read",
    "real_time_market:write",
    "rtm_scheduling:read",
    "rtm_scheduling:write",
    "rtm_block_bid:read",
    "rtm_block_bid:write",
    "rtm_single_bid:read",
    "rtm_single_bid:write",
    "rtm_bid_status:read",
    "rtm_bid_status:write",
    
    # Weather
    "weather:read",
    "weather:write",
    
    # Masters
    "masters:read",
    "masters:write",
    "plant_detail:read",
    "plant_detail:write",
    "dam_limit_values:read",
    "dam_limit_values:write",
    "rtm_limit_values:read",
    "rtm_limit_values:write",
    
    # Administration (all admin menus map to admin:* for consistency)
    "admin:read",
    "admin:write",
    "admin:create", 
    "admin:delete",
    "admin:*",
    
    # Reports
    "reports:read",
    "reports:write",
    "standard_reports:read",
    "standard_reports:write",
    
    # Authentication & MFA
    "auth:login",
    "auth:logout", 
    "mfa:setup",
    "mfa:disable",
    
    # System
    "system:health",
    "system:status",
]

async def ensure_permissions_catalog(db) -> bool:
    """
    Ensure permissions catalog exists in database
    """
    try:
        existing = await db.permissions_catalog.find_one({})
        if not existing:
            catalog_doc = {
                "permissions": DEFAULT_PERMISSIONS_CATALOG,
                "version": 1,
                "description": "Default RBAC permissions catalog"
            }
            await db.permissions_catalog.insert_one(catalog_doc)
            return True
        return True
    except Exception as e:  # noqa: BLE001
        print(f"Error ensuring permissions catalog: {e}")
        return False

def get_expanded_permissions() -> Set[str]:
    """
    Get all permissions when wildcard '*' is present
    """
    return set(DEFAULT_PERMISSIONS_CATALOG)
