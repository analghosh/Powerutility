"""
Database indexes management for Auth Service

This module creates and manages database indexes for optimal performance
across all collections used by the auth service.
"""

import logging
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import OperationFailure
from .config import settings

logger = logging.getLogger(__name__)

def create_database_indexes():
    """
    Create database indexes for all collections in the auth service.
    This function is called during application startup to ensure optimal performance.
    """
    try:
        # Use centralized MongoDB client (singleton pattern)
        from libs.database.mongo_client_factory import get_auth_client
        
        client = get_auth_client(settings.MONGODB_URI)
        auth_db = client[settings.AUTH_DB_NAME]
        audit_db = client[settings.AUDIT_DB_NAME]
        
        logger.info("🔧 Creating database indexes...")
        
        # Create indexes for users collection
        _create_user_indexes(auth_db)
        
        # Create indexes for roles collection
        _create_role_indexes(auth_db)
        
        # Create indexes for menus collection
        _create_menu_indexes(auth_db)
        
        # Create indexes for menu_roles collection
        _create_menu_role_indexes(auth_db)
        
        # Create indexes for audit collection
        _create_audit_indexes(audit_db)
        
        # Create indexes for blacklist collection
        _create_blacklist_indexes(auth_db)
        
        # Note: Client is managed by MongoClientFactory, no need to close
        logger.info("✅ Database indexes created successfully")
        
    except Exception as e:
        logger.error(f"❌ Failed to create database indexes: {e}")
        raise

def _create_user_indexes(db):
    """
    Create indexes for users collection.

    Each index is created independently (its own try/except) rather than
    inside one big try/except for the whole function. In the live UAT data,
    813 of 816 users have `email: null` and two share `admin@epms.com`, so
    the unique index on `email` ALWAYS fails with OperationFailure. When
    every statement shared one try/except, that single failure silently
    aborted every index declared after it - `roles`, `login_attempts`,
    `locked_until`, `mfa_enabled`, `sessions.session_id`, and
    `password_expires_at` never existed on any deployment. Isolating each
    call means one bad index (still `email`, still expected to fail here)
    only costs that one index.
    """
    users_collection = db[settings.MONGODB_AUTH_COLLECTION]

    # Unique indexes for authentication
    _create_single_index(users_collection, "username", unique=True, background=True)
    _create_single_index(users_collection, "email", unique=True, background=True)

    # Compound index for active users queries
    _create_single_index(
        users_collection,
        [("is_deleted", ASCENDING), ("is_active", ASCENDING)],
        background=True,
    )

    # Index for login attempts and account lockout
    _create_single_index(users_collection, "login_attempts", background=True)
    _create_single_index(users_collection, "locked_until", background=True)

    # Index for role-based queries
    _create_single_index(users_collection, "roles", background=True)

    # Compound index serving notification audience resolution:
    #   users.find({"roles": {"$in": [...]}, "is_active": True,
    #               "is_deleted": {"$ne": True}}, {"_id": 1})
    # The single-field `roles` index above cannot also serve the
    # `is_active` predicate, so that query stayed a COLLSCAN over the
    # TOTAL user count (816) instead of narrowing by recipient count.
    _create_single_index(
        users_collection,
        [("roles", ASCENDING), ("is_active", ASCENDING)],
        background=True,
    )

    # Index for MFA users
    _create_single_index(users_collection, "mfa_enabled", background=True)

    # Index for session management
    _create_single_index(users_collection, "sessions.session_id", background=True)

    # Index for password expiry queries
    _create_single_index(users_collection, "password_expires_at", background=True)

    logger.debug("✅ User collection indexes created")


def _create_single_index(collection, keys, **kwargs):
    """
    Attempt to create one index, logging (and swallowing) a failure for
    just that index instead of letting it cancel every index that follows.
    """
    try:
        collection.create_index(keys, **kwargs)
    except OperationFailure as e:
        logger.warning(f"⚠️ Index {keys!r} may already exist or conflicts with data: {e}")
    except Exception as e:
        logger.error(f"❌ Failed to create index {keys!r}: {e}")

def _create_role_indexes(db):
    """Create indexes for roles collection"""
    try:
        roles_collection = db["roles"]
        
        # Unique index for role names
        roles_collection.create_index("name", unique=True, background=True)
        
        # Index for active roles
        roles_collection.create_index("is_active", background=True)
        
        # Index for permission queries
        roles_collection.create_index("permissions", background=True)
        
        logger.debug("✅ Roles collection indexes created")
        
    except OperationFailure as e:
        logger.warning(f"⚠️ Some role indexes may already exist: {e}")
    except Exception as e:
        logger.error(f"❌ Failed to create role indexes: {e}")

def _create_menu_indexes(db):
    """Create indexes for menus collection"""
    try:
        menus_collection = db["menus"]
        
        # Index for menu hierarchy queries
        menus_collection.create_index("parent_id", background=True)
        
        # Compound index for active menus with ordering
        menus_collection.create_index([
            ("is_active", ASCENDING),
            ("is_deleted", ASCENDING),
            ("order", ASCENDING)
        ], background=True)
        
        # Index for path-based queries
        menus_collection.create_index("path", background=True)
        
        # Index for menu name queries
        menus_collection.create_index("name", background=True)
        
        logger.debug("✅ Menus collection indexes created")
        
    except OperationFailure as e:
        logger.warning(f"⚠️ Some menu indexes may already exist: {e}")
    except Exception as e:
        logger.error(f"❌ Failed to create menu indexes: {e}")

def _create_menu_role_indexes(db):
    """Create indexes for menu_roles collection"""
    try:
        menu_roles_collection = db["menu_roles"]
        
        # Compound index for role-menu relationships
        menu_roles_collection.create_index([
            ("role_id", ASCENDING),
            ("menu_id", ASCENDING)
        ], unique=True, background=True)
        
        # Index for role-based queries
        menu_roles_collection.create_index("role_id", background=True)
        
        # Index for menu-based queries
        menu_roles_collection.create_index("menu_id", background=True)
        
        # Index for permission queries
        menu_roles_collection.create_index("permissions", background=True)
        
        logger.debug("✅ Menu-roles collection indexes created")
        
    except OperationFailure as e:
        logger.warning(f"⚠️ Some menu-role indexes may already exist: {e}")
    except Exception as e:
        logger.error(f"❌ Failed to create menu-role indexes: {e}")

def _create_audit_indexes(db):
    """Create indexes for audit collection"""
    try:
        audit_collection = db["audit_logs"]
        
        # Index for user-based audit queries
        audit_collection.create_index("user_id", background=True)
        
        # Index for action-based queries
        audit_collection.create_index("action", background=True)
        
        # Index for timestamp-based queries (with TTL for cleanup)
        audit_collection.create_index("timestamp", background=True)
        
        # Compound index for user action queries
        audit_collection.create_index([
            ("user_id", ASCENDING),
            ("timestamp", DESCENDING)
        ], background=True)
        
        # Index for IP-based queries
        audit_collection.create_index("ip_address", background=True)
        
        logger.debug("✅ Audit collection indexes created")
        
    except OperationFailure as e:
        logger.warning(f"⚠️ Some audit indexes may already exist: {e}")
    except Exception as e:
        logger.error(f"❌ Failed to create audit indexes: {e}")

def _create_blacklist_indexes(db):
    """Create indexes for token blacklist collection"""
    try:
        blacklist_collection = db["refresh_token_blacklist"]
        
        # Unique index for refresh tokens
        blacklist_collection.create_index("refresh_token", unique=True, background=True)
        
        # Index for user-based queries
        blacklist_collection.create_index("user_id", background=True)
        
        # TTL index for automatic cleanup of expired tokens
        blacklist_collection.create_index("expires_at", expireAfterSeconds=0, background=True)
        
        logger.debug("✅ Blacklist collection indexes created")
        
    except OperationFailure as e:
        logger.warning(f"⚠️ Some blacklist indexes may already exist: {e}")
    except Exception as e:
        logger.error(f"❌ Failed to create blacklist indexes: {e}")

def drop_all_indexes():
    """
    Drop all indexes (except _id) from all collections.
    WARNING: This should only be used for maintenance or testing.
    """
    try:
        # Use centralized MongoDB client (singleton pattern)
        from libs.database.mongo_client_factory import get_auth_client
        
        client = get_auth_client(settings.MONGODB_URI)
        auth_db = client[settings.AUTH_DB_NAME]
        audit_db = client[settings.AUDIT_DB_NAME]
        
        collections = [
            (auth_db, settings.MONGODB_AUTH_COLLECTION),
            (auth_db, "roles"),
            (auth_db, "menus"),
            (auth_db, "menu_roles"),
            (auth_db, "refresh_token_blacklist"),
            (audit_db, "audit_logs")
        ]
        
        for db, collection_name in collections:
            try:
                collection = db[collection_name]
                collection.drop_indexes()
                logger.info(f"🗑️ Dropped indexes for {collection_name}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to drop indexes for {collection_name}: {e}")
        
        # Note: Client is managed by MongoClientFactory, no need to close
        logger.info("✅ All indexes dropped successfully")
        
    except Exception as e:
        logger.error(f"❌ Failed to drop indexes: {e}")
        raise

def list_collection_indexes():
    """
    List all indexes for all collections.
    Useful for debugging and monitoring.
    """
    try:
        # Use centralized MongoDB client (singleton pattern)
        from libs.database.mongo_client_factory import get_auth_client
        
        client = get_auth_client(settings.MONGODB_URI)
        auth_db = client[settings.AUTH_DB_NAME]
        audit_db = client[settings.AUDIT_DB_NAME]
        
        collections = [
            (auth_db, settings.MONGODB_AUTH_COLLECTION, "Users"),
            (auth_db, "roles", "Roles"),
            (auth_db, "menus", "Menus"),
            (auth_db, "menu_roles", "Menu-Roles"),
            (auth_db, "refresh_token_blacklist", "Token Blacklist"),
            (audit_db, "audit_logs", "Audit Logs")
        ]
        
        index_info = {}
        
        for db, collection_name, display_name in collections:
            try:
                collection = db[collection_name]
                indexes = list(collection.list_indexes())
                index_info[display_name] = [
                    {
                        "name": idx.get("name"),
                        "keys": dict(idx.get("key", {})),
                        "unique": idx.get("unique", False),
                        "background": idx.get("background", False)
                    }
                    for idx in indexes
                ]
            except Exception as e:
                index_info[display_name] = f"Error: {e}"
        
        # Note: Client is managed by MongoClientFactory, no need to close
        return index_info
        
    except Exception as e:
        logger.error(f"❌ Failed to list indexes: {e}")
        return {"error": str(e)}