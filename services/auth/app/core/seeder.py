"""
Database seeder for Auth Service
"""

import logging
from typing import List, Dict, Any
from datetime import datetime
from ..repository.page_repository import PageRepository
from ..repository.role_repository import RoleRepository
from ..repository.page_role_repository import PageRoleRepository
from ..repository.menu_repository import MenuRepository
from ..repository.user_repository import UserRepository
from ..models.pages import PageCreate  
from ..models.roles import RoleInDB
from ..models.page_role import PageRoleCreate
from ..models.menu import MenuCreate
from ..core.config import settings
from ..repository.user_repository import UserRepository # Import UserRepository

logger = logging.getLogger(__name__)

# Core permission types
CORE_PERMISSIONS = ["read", "write", "process", "create"]

# Default pages that will be seeded into the database - simplified structure
DEFAULT_PAGES = [
    {"name": "Home", "module_id": "6073ee4dfca6b62a5b47c842"},
    {"name": "User Management", "module_id": "614c6835f89e93154e781072"},
    {"name": "Role Management", "module_id": "614c6835f89e93154e781072"},
    {"name": "Data Processing", "module_id": "610d1ea5f22e9e73aa537b92"},
    {"name": "Data Ingestion", "module_id": "610d1eb5afb37c7fd50f12a2"},
    {"name": "Reports", "module_id": "614c681dc4aef12a7d244495"},
    {"name": "Settings", "module_id": "614c6835f89e93154e781072"}
]

# Default roles - simplified structure
DEFAULT_ROLES = [
    {"name": "admin", "description": "System administrator"},
    {"name": "operator", "description": "System operator"},  
    {"name": "user", "description": "Regular user"},
    {"name": "viewer", "description": "Read-only user"}
]

# Comprehensive menu structure for EPMS
DEFAULT_MENUS = [
    # 1. Dashboard (Root)
    {"name": "Dashboard", "icon": "📊", "parent_id": None, "path": "/dashboard", "order": 1},
    
    # 2. Overview (Root)
    {"name": "Overview", "icon": "👁️", "parent_id": None, "path": "/overview", "order": 2},
    {"name": "Management Snapshot", "icon": "📈", "parent_id": "Overview", "path": "/overview/snapshot", "order": 1},
    {"name": "DSM Overview", "icon": "⚡", "parent_id": "Overview", "path": "/overview/dsm", "order": 2},
    {"name": "Market Integrator", "icon": "🔗", "parent_id": "Overview", "path": "/overview/market", "order": 3},
    
    # 3. Forecast (Root)
    {"name": "Forecast", "icon": "🔮", "parent_id": None, "path": "/forecast", "order": 3},
    {"name": "Forecast Analysis", "icon": "📊", "parent_id": "Forecast", "path": "/forecast/analysis", "order": 1},
    {"name": "Demand Forecast", "icon": "📈", "parent_id": "Forecast", "path": "/forecast/demand", "order": 2},
    
    # 4. Supply (Root)
    {"name": "Supply", "icon": "⚡", "parent_id": None, "path": "/supply", "order": 4},
    {"name": "Non-Renewable Supply", "icon": "🛢️", "parent_id": "Supply", "path": "/supply/non-renewable", "order": 1},
    {"name": "Renewable Supply", "icon": "🌱", "parent_id": "Supply", "path": "/supply/renewable", "order": 2},
    {"name": "URS", "icon": "🔋", "parent_id": "Supply", "path": "/supply/urs", "order": 3},
    
    # 5. Day Ahead Marketing (Root)
    {"name": "Day Ahead Marketing", "icon": "📅", "parent_id": None, "path": "/dam", "order": 5},
    {"name": "DAM Scheduling", "icon": "⏰", "parent_id": "Day Ahead Marketing", "path": "/dam/scheduling", "order": 1},
    {"name": "DAM Block Bid", "icon": "📦", "parent_id": "Day Ahead Marketing", "path": "/dam/block-bid", "order": 2},
    {"name": "DAM Single Bid", "icon": "🎯", "parent_id": "Day Ahead Marketing", "path": "/dam/single-bid", "order": 3},
    {"name": "DAM Bid Status", "icon": "📋", "parent_id": "Day Ahead Marketing", "path": "/dam/bid-status", "order": 4},
    
    # 6. Real Time Market (Root)
    {"name": "Real Time Market", "icon": "⚡", "parent_id": None, "path": "/rtm", "order": 6},
    {"name": "RTM Scheduling", "icon": "⏰", "parent_id": "Real Time Market", "path": "/rtm/scheduling", "order": 1},
    {"name": "RTM Block Bid", "icon": "📦", "parent_id": "Real Time Market", "path": "/rtm/block-bid", "order": 2},
    {"name": "RTM Single Bid", "icon": "🎯", "parent_id": "Real Time Market", "path": "/rtm/single-bid", "order": 3},
    {"name": "RTM Bid Status", "icon": "📋", "parent_id": "Real Time Market", "path": "/rtm/bid-status", "order": 4},
    
    # 7. Weather (Root)
    {"name": "Weather", "icon": "🌤️", "parent_id": None, "path": "/weather", "order": 7},
    
    # 8. Masters (Root)
    {"name": "Masters", "icon": "⚙️", "parent_id": None, "path": "/masters", "order": 8},
    {"name": "Plant Detail", "icon": "🏭", "parent_id": "Masters", "path": "/masters/plant", "order": 1},
    {"name": "DAM Limit Values", "icon": "📊", "parent_id": "Masters", "path": "/masters/dam-limits", "order": 2},
    {"name": "RTM Limit Values", "icon": "📊", "parent_id": "Masters", "path": "/masters/rtm-limits", "order": 3},
    
    # 9. Administration (Root)
    {"name": "Administration", "icon": "🔧", "parent_id": None, "path": "/admin", "order": 9},
    {"name": "Users", "icon": "👥", "parent_id": "Administration", "path": "/admin/users", "order": 1},
    {"name": "Permissions", "icon": "🔐", "parent_id": "Administration", "path": "/admin/permissions", "order": 2},
    {"name": "Audit Logs", "icon": "📝", "parent_id": "Administration", "path": "/admin/audit", "order": 3},
    {"name": "Setup", "icon": "⚙️", "parent_id": "Administration", "path": "/admin/setup", "order": 4},
    # Seeded for fresh installs only: seed_menus skips when any menu exists, so
    # existing deployments get this row from tools/add_system_health_menu.py.
    {"name": "System Health", "icon": "🩺", "parent_id": "Administration", "path": "/admin/system-health", "order": 5},
    
    # 10. Reports (Root)
    {"name": "Reports", "icon": "📋", "parent_id": None, "path": "/reports", "order": 10},
    {"name": "Standard Reports", "icon": "📊", "parent_id": "Reports", "path": "/reports/standard", "order": 1},
    # The Power System Data Analytics Dashboard lives under Reports (user decision
    # 2026-09-17), renamed "Reports Dashboard" 2026-09-18. Without this row
    # RouteProtection has no menu to resolve the path against and sends the user back
    # to /login, however correct the frontend route.
    # 🔴 The NAME mints the permission: `_get_resource_name_from_menu` falls back to
    # `name.lower().replace(" ", "_")`, so this row mints `reports_dashboard:read`
    # where it used to mint `analytics_dashboard:read`. `AnalyticsService.PAGE_GRANTS`
    # accepts BOTH, so an environment seeded under either name keeps working — do not
    # drop the old string until every deployment has been reseeded.
    # The PATH is deliberately unchanged: it is in RouteProtection's bypass list and
    # in every bookmark.
    {"name": "Reports Dashboard", "icon": "📉", "parent_id": "Reports",
     "path": "/reports/analytics-dashboard", "order": 2}
]

class DatabaseSeeder:
    """Database seeder for populating initial data"""
    
    def __init__(self):
        self.page_repository = PageRepository()
        self.role_repository = RoleRepository()
        self.page_role_repository = PageRoleRepository()
        self.menu_repository = MenuRepository()
        self.user_repository = UserRepository()
    
    async def seed_pages(self, force_reload: bool = False) -> bool:
        """Seed default pages into the database"""
        try:
            logger.info("Seeding default pages...")
            
            for page_data in DEFAULT_PAGES:
                existing_page = await self.page_repository.get_page_by_name(page_data["name"])
                if existing_page and not force_reload:
                    logger.info(f"Page {page_data['name']} already exists, skipping...")
                    continue
                
                if existing_page and force_reload:
                    logger.info(f"Force reloading page: {page_data['name']}")
                    await self.page_repository.delete_page(existing_page.id)
                
                # Create page without role_ids (separate relationship table)
                page_create = PageCreate(
                    name=page_data["name"],
                    module_id=page_data["module_id"]
                )

                created_page = await self.page_repository.create_page(page_create)
                logger.info(f"Created page: {created_page.name}")
            
            logger.info("Page seeding completed successfully!")
            return True
            
        except Exception as e:
            logger.error(f"Error seeding pages: {e}")
            return False

    async def seed_page_roles(self, force_reload: bool = False) -> bool:
        """Seed page-role relationships"""
        try:
            logger.info("Seeding page-role relationships...")
            
            # Get admin role
            admin_role = await self.role_repository.get_role_by_name("admin")
            if not admin_role:
                logger.error("Admin role not found - cannot create page-role relationships")
                return False
            
            # Get all pages
            all_pages = await self.page_repository.get_all_active_pages()
            
            for page in all_pages:
                # Grant admin access to all pages with full permissions
                success = self.page_role_repository.grant_role_page_access(
                    role_id=admin_role.id,
                    page_id=page.id,
                    permissions=["read", "write", "create", "delete"]
                )
                
                if success:
                    logger.info(f"Granted admin access to page: {page.name}")
                else:
                    logger.warning(f"Failed to grant admin access to page: {page.name}")
            
            logger.info("Page-role relationship seeding completed successfully!")
            return True
            
        except Exception as e:
            logger.error(f"Error seeding page-role relationships: {e}")
            return False
    
    async def seed_menus(self, force_reload: bool = False) -> bool:
        """Seed default menus into the database asynchronously"""
        try:
            logger.info("Seeding default menus...")
            
            # Check if menus already exist
            existing_menus = await self.menu_repository.get_all_active_menus()
            if existing_menus and not force_reload:
                logger.info(f"Found {len(existing_menus)} existing menus, skipping menu seeding...")
                return True
            
            if existing_menus and force_reload:
                logger.info("Force reloading menus - deleting all existing menus...")
                for menu in existing_menus:
                    await self.menu_repository.delete_menu(menu.id)
            
            # First pass: Create all root menus and store their IDs
            menu_name_to_id = {}
            
            for menu_data in DEFAULT_MENUS:
                parent_id = None
                if menu_data["parent_id"]:
                    parent_id = menu_name_to_id.get(menu_data["parent_id"])
                    if not parent_id:
                        logger.warning(f"Parent menu '{menu_data['parent_id']}' not found for '{menu_data['name']}'")
                
                menu_create = MenuCreate(
                    name=menu_data["name"],
                    icon=menu_data["icon"],
                    parent_id=parent_id,
                    path=menu_data["path"],
                    order=menu_data["order"]
                )
                
                created_menu = await self.menu_repository.create_menu(menu_create)
                menu_name_to_id[menu_data["name"]] = created_menu.id
                logger.info(f"Created menu: {created_menu.name}")
            
            logger.info("Menu seeding completed successfully!")
            return True
            
        except Exception as e:
            logger.error(f"Error seeding menus: {e}")
            return False

    async def seed_roles(self, force_reload: bool = False) -> bool:
        """Seed default roles into the database"""
        try:
            logger.info("Seeding default roles...")
            
            for role_data in DEFAULT_ROLES:
                existing_role = await self.role_repository.get_role_by_name(role_data["name"])
                if existing_role and not force_reload:
                    logger.info(f"Role {role_data['name']} already exists, skipping...")
                    continue
                
                if existing_role and force_reload:
                    logger.info(f"Force reloading role: {role_data['name']}")
                    await self.role_repository.delete_role(existing_role.id)
                
                created_role = await self.role_repository.create_role(
                    name=role_data["name"],
                    description=role_data["description"]
                )
                logger.info(f"Created role: {created_role.name}")
            
            logger.info("Role seeding completed successfully!")
            return True
            
        except Exception as e:
            logger.error(f"Error seeding roles: {e}")
            return False

    async def seed_all(self, force_reload: bool = False) -> bool:
        """
        Seed all default data

        Args:
            force_reload: If True, will reload all data even if it exists (admin only)
        """
        try:
            logger.info("Starting database seeding...")
            
            # Seed roles first (needed for page access assignment and admin user)
            if not await self.seed_roles(force_reload=force_reload):
                logger.error("Failed to seed roles")
                return False
            
            # Ensure admin user exists (only admin user, no other users)
            if not await self.ensure_admin_user():
                logger.error("Failed to ensure admin user")
                return False
            
            # Seed pages (separate from roles)
            if not await self.seed_pages(force_reload=force_reload):
                logger.error("Failed to seed pages")
                return False
            
            # Seed page-role relationships (now async)
            if not await self.seed_page_roles(force_reload=force_reload):
                logger.error("Failed to seed page-role relationships")
                return False
            
            # Seed menus (static, seeded once)
            if not await self.seed_menus(force_reload=force_reload):
                logger.error("Failed to seed menus")
                return False
            
            logger.info("Database seeding completed successfully!")
            return True

        except Exception as e:
            logger.error(f"Error during database seeding: {e}")
            return False

    async def get_seed_status(self) -> Dict[str, Any]:
        """Get the current seeding status"""
        try:
            all_pages = await self.page_repository.get_all_active_pages()
            all_roles = await self.role_repository.get_all_active_roles()
            
            # Check which default pages are seeded
            seeded_pages = [page.name for page in all_pages]
            missing_pages = [page["name"] for page in DEFAULT_PAGES if page["name"] not in seeded_pages]
            
            # Check which default roles are seeded
            seeded_roles = [role.name for role in all_roles]
            missing_roles = [role["name"] for role in DEFAULT_ROLES if role["name"] not in seeded_roles]
            
            return {
                "pages": {
                    "total_default": len(DEFAULT_PAGES),
                    "seeded": len(seeded_pages),
                    "missing": len(missing_pages),
                    "missing_pages": missing_pages,
                    "all_pages": seeded_pages
                },
                "roles": {
                    "total_default": len(DEFAULT_ROLES),
                    "seeded": len(seeded_roles),
                    "missing": len(missing_roles),
                    "missing_roles": missing_roles,
                    "all_roles": seeded_roles
                },
                "core_permissions": CORE_PERMISSIONS,
                "is_complete": len(missing_pages) == 0 and len(missing_roles) == 0
            }
            
        except Exception as e:
            logger.error(f"Error getting seed status: {e}")
            return {
                "error": str(e),
                "is_complete": False # type: ignore
            }

    async def reset_database(self) -> bool:
        """Reset database by removing all pages and roles (admin only)"""
        try:
            logger.warning("Resetting database - this will remove all pages and roles!")
            
            # Get all pages and roles
            all_pages = await self.page_repository.get_all_active_pages()
            all_roles = await self.role_repository.get_all_active_roles()
            
            # Delete all pages
            for page in all_pages:
                await self.page_repository.delete_page(page.id)
                logger.info(f"Deleted page: {page.name}")
            
            # Delete all roles
            for role in all_roles:
                await self.role_repository.delete_role(role.id)
                logger.info(f"Deleted role: {role.name}")
            
            logger.info("Database reset completed!")
            return True
            
        except Exception as e:
            logger.error(f"Error resetting database: {e}")
            return False

    async def reload_pages(self) -> bool:
        """Reload pages (admin only - will overwrite existing pages)"""
        try:
            logger.warning("Reloading pages - this will overwrite existing pages!")
            return await self.seed_pages(force_reload=True)
        except Exception as e:
            logger.error(f"Error reloading pages: {e}")
            return False

    async def reload_roles(self) -> bool:
        """Reload roles (admin only - will overwrite existing roles)"""
        try:
            logger.warning("Reloading roles - this will overwrite existing roles!")
            return await self.seed_roles(force_reload=True)
        except Exception as e:
            logger.error(f"Error reloading roles: {e}")
            return False

    async def grant_admin_page_access(self) -> bool:
        """Grant admin role access to all existing pages"""
        try:
            logger.info("Granting admin access to all pages...")
            
            # Get admin role
            admin_role = await self.role_repository.get_role_by_name("admin")
            if not admin_role:
                logger.error("Admin role not found - cannot grant page access")
                return False
            
            # Get all pages
            all_pages = await self.page_repository.get_all_active_pages()
            
            for page in all_pages:
                # Check if admin already has access
                existing_relationship = await self.page_role_repository.get_page_role(page.id, admin_role.id)
                
                if not existing_relationship:
                    # Grant admin access to page with full permissions
                    success = self.page_role_repository.grant_role_page_access(
                        role_id=admin_role.id,
                        page_id=page.id,
                        permissions=["read", "write", "create", "delete"]
                    ) # This method is async, so it needs await
                    
                    if success:
                        logger.info(f"Granted admin access to page: {page.name}")
                    else:
                        logger.warning(f"Failed to grant admin access to page: {page.name}")
                else:
                    logger.info(f"Admin already has access to page: {page.name}")
            
            logger.info("Admin page access granted successfully!")
            return True
            
        except Exception as e:
            logger.error(f"Error granting admin page access: {e}")
            return False

    async def ensure_admin_user(self) -> bool:
        """
        Ensures the admin user exists and updates it if necessary.
        Only creates/updates the admin user, no other users.
        """
        try:
            logger.info("Ensuring admin user exists...")
            admin_user = await self.user_repository.get_user_by_username(settings.ADMIN_USERNAME)

            if admin_user:
                logger.info(f"Admin user '{settings.ADMIN_USERNAME}' already exists.")
                return True
            else:
                logger.info(f"Admin user '{settings.ADMIN_USERNAME}' not found. Creating...")
                
                # Get admin role (now async)
                admin_role = await self.role_repository.get_role_by_name("admin")
                if not admin_role:
                    logger.error("Admin role not found - cannot create admin user")
                    return False
                
                # Create admin user
                created_user = await self.user_repository.create_user(
                    username=settings.ADMIN_USERNAME,
                    email=settings.ADMIN_EMAIL,
                    password=settings.ADMIN_INITIAL_PASSWORD,
                    roles=["admin"],
                    permissions=[],
                    profile={},
                    is_active=True,
                    is_verified=True
                )
                logger.info(f"Created admin user: {created_user.username}")
                return True
        except Exception as e:
            logger.error(f"Error ensuring admin user: {e}")
            return False

def seed_database(): # Deprecated synchronous wrapper - now raises NotImplementedError
    """Convenience function to seed the database (synchronous wrapper)"""
    raise NotImplementedError("Synchronous seed_database is deprecated. Use async_seed_database instead.")

def seed_database_force(): # Deprecated synchronous wrapper - now raises NotImplementedError
    """Convenience function to force seed the database (admin only) (synchronous wrapper)"""
    raise NotImplementedError("Synchronous seed_database_force is deprecated. Use async_seed_database_force instead.")

async def reset_database():
    """Convenience function to reset the database (admin only)"""
    seeder = DatabaseSeeder()
    return await seeder.reset_database()

async def get_seed_status():
    """Convenience function to get seeding status"""
    seeder = DatabaseSeeder()
    return await seeder.get_seed_status()

async def reload_pages():
    """Convenience function to reload pages (admin only)"""
    seeder = DatabaseSeeder()
    return await seeder.reload_pages()

async def reload_roles():
    """Convenience function to reload roles (admin only)"""
    seeder = DatabaseSeeder()
    return await seeder.reload_roles()

async def grant_admin_page_access():
    """Convenience function to grant admin access to all pages"""
    seeder = DatabaseSeeder()
    return await seeder.grant_admin_page_access()

async def ensure_admin_user():
    """Convenience function to ensure admin user exists"""
    seeder = DatabaseSeeder()
    return await seeder.ensure_admin_user()

async def async_seed_database():
    """Convenience function to seed the database asynchronously"""
    seeder = DatabaseSeeder()
    return await seeder.seed_all()

async def async_seed_database_force():
    """Convenience function to force seed the database (admin only) asynchronously"""
    seeder = DatabaseSeeder()
    return await seeder.seed_all(force_reload=True)

async def async_reload_menus():
    """Convenience function to reload menus (admin only) asynchronously"""
    seeder = DatabaseSeeder()
    return await seeder.reload_menus()

def get_menu_tree(): # Deprecated synchronous wrapper
    """Convenience function to get menu tree (synchronous wrapper)"""
    raise NotImplementedError("Synchronous get_menu_tree is deprecated. Use async_get_menu_tree instead.")

async def async_get_menu_tree():
    """Convenience function to get menu tree asynchronously"""
    seeder = DatabaseSeeder()
    return await seeder.menu_repository.get_menu_tree()