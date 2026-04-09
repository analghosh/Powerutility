"""
User model for authentication service
"""

from datetime import datetime, timedelta
from typing import Optional, List
from bson import ObjectId
import bcrypt
from ..core.config import settings
from ..core.dto import UserDTO, AuthUserDTO
from ..services.redis_blacklist import redis_blacklist_service
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
import logging

logger = logging.getLogger(__name__)


class User:
    """User model for authentication"""

    def __init__(self):
        # Use the singleton database connection instead of creating new ones
        from ..core.database import get_auth_database
        self.db = get_auth_database()
        self.collection = self.db[settings.MONGODB_AUTH_COLLECTION]
        # Index creation is deferred to async initialize()

    async def initialize(self):
        """Initialize async resources — call once at startup from async context."""
        await self._ensure_indexes()

    async def _ensure_indexes(self):
        """Ensure database indexes for optimal performance"""
        try:
            # Index for username lookups (case-sensitive, unique)
            await self.collection.create_index("username", unique=True)

            # Index for email lookups (case-sensitive, not unique to avoid collisions)
            await self.collection.create_index("email")

            # Compound index for active users
            await self.collection.create_index([
                ("is_deleted", 1),
                ("is_active", 1)
            ])

            # Index for login attempts tracking
            await self.collection.create_index("failed_login_attempts")

            # Index for account lockout queries
            await self.collection.create_index("locked_until")

            # Index for role-based queries
            await self.collection.create_index("roles")

            # Index for MFA users
            await self.collection.create_index("mfa_enabled")

            logger.debug("User collection indexes ensured")

        except Exception as e:
            logger.warning(f"Failed to create user indexes: {e}")

    async def create_user(self, username: str, email: str, password: str, **kwargs) -> UserDTO:
        """Create a new user (case-insensitive username)"""
        # Check if user already exists (case-insensitive)
        if await self.get_user_by_username(username):
            raise ValueError("Username already exists")

        if await self.get_user_by_email(email):
            raise ValueError("Email already exists")

        # Hash password
        salt = bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS)
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), salt)

        # Create user document
        user_data = {
            "username": username,
            "email": email,
            "password_hash": hashed_password,
            "is_active": True,
            "is_verified": False,
            "is_deleted": False,  # Hidden field for soft delete
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
            "last_login": None,
            "login_attempts": 0,
            "locked_until": None,
            "requires_admin_unlock": False,
            "password_changed_at": datetime.now(),
            "password_expires_at": datetime.now() + timedelta(days=settings.PASSWORD_EXPIRY_DAYS),
            "password_history": [],
            "roles": kwargs.get("roles", ["user"]),
            "permissions": kwargs.get("permissions", []),
            "profile": kwargs.get("profile", {}),
            "mfa_enabled": kwargs.get("mfa_enabled", False),
            "mfa_secret": kwargs.get("mfa_secret"),
            "mfa_backup_codes": kwargs.get("mfa_backup_codes", []),
            "mfa_setup_complete": kwargs.get("mfa_setup_complete", False),
            "sessions": []
        }

        result = await self.collection.insert_one(user_data)
        user_data["_id"] = result.inserted_id

        return UserDTO.from_db_document(user_data)

    async def get_user_by_id(self, user_id: str) -> Optional[UserDTO]:
        """Get user by ID"""
        try:
            # Exclude deleted users
            user_doc = await self.collection.find_one({
                "_id": ObjectId(user_id),
                "is_deleted": {"$ne": True}
            })
            return UserDTO.from_db_document(user_doc) if user_doc else None
        except Exception:
            return None

    async def get_user_by_username(self, username: str) -> Optional[UserDTO]:
        """Get user by username (case-insensitive)"""
        # Username is case-insensitive, so we use case-insensitive match
        # Exclude deleted users
        user_doc = await self.collection.find_one({
            "username": {"$regex": f"^{username}$", "$options": "i"},
            "is_deleted": {"$ne": True}
        })
        return UserDTO.from_db_document(user_doc) if user_doc else None

    async def get_user_by_email(self, email: str) -> Optional[UserDTO]:
        """Get user by email"""
        # Exclude deleted users
        user_doc = await self.collection.find_one({
            "email": email,
            "is_deleted": {"$ne": True}
        })
        return UserDTO.from_db_document(user_doc) if user_doc else None

    async def authenticate_user(self, username: str, password: str) -> Optional[AuthUserDTO]:
        """Authenticate user with username/email and password"""
        # Try to find user by username or email
        user = await self.get_user_by_username(username)
        if not user:
            user = await self.get_user_by_email(username)

        if not user:
            return None

        # Get raw document for password verification
        user_doc = await self.collection.find_one({"_id": ObjectId(user.id)})
        if not user_doc:
            return None

        # Check if account is locked
        if user.locked_until and user.locked_until > datetime.now():
            if user_doc.get("requires_admin_unlock", False):
                raise ValueError("Account is locked and requires admin unlock")
            else:
                raise ValueError("Account is locked")

        # Check if account is active
        if not user.is_active:
            raise ValueError("Account is deactivated")

        # Verify password
        if not bcrypt.checkpw(password.encode('utf-8'), user_doc["password_hash"]):
            # Increment login attempts
            await self._increment_login_attempts(ObjectId(user.id))

            # Failed login logging handled at route layer to avoid duplication
            return None

        # Reset login attempts on successful login
        await self._reset_login_attempts(ObjectId(user.id))

        # Update last login
        await self.collection.update_one(
            {"_id": ObjectId(user.id)},
            {"$set": {"last_login": datetime.now()}}
        )

        # Update the user DTO with new last_login
        user.last_login = datetime.now()

        # Login success logging handled at route layer to avoid duplication

        return AuthUserDTO.from_user_dto(user)

    async def _increment_login_attempts(self, user_id: ObjectId):
        """Increment failed login attempts"""
        user = await self.collection.find_one({"_id": user_id})
        if not user:
            return
        current_attempts = user.get("login_attempts", 0) + 1

        update_data = {"login_attempts": current_attempts}

        # Lock account if max attempts reached - requires admin unlock
        if current_attempts >= settings.MAX_LOGIN_ATTEMPTS:
            # Set locked_until to a far future date to require admin intervention
            lockout_until = datetime.now() + timedelta(days=365)  # 1 year - effectively permanent
            update_data["locked_until"] = lockout_until
            update_data["requires_admin_unlock"] = True

        await self.collection.update_one(
            {"_id": user_id},
            {"$set": update_data}
        )

    async def _reset_login_attempts(self, user_id: ObjectId):
        """Reset login attempts on successful login"""
        await self.collection.update_one(
            {"_id": user_id},
            {"$set": {
                "login_attempts": 0,
                "locked_until": None,
                "requires_admin_unlock": False
            }}
        )

    async def update_user(self, user_id: str, update_data: dict) -> bool:
        """Update user data"""
        try:
            update_data["updated_at"] = datetime.now()
            result = await self.collection.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": update_data}
            )

            if result.modified_count > 0:
                return True
            return False
        except Exception as e:
            logger.error(f"Error updating user: {e}")
            return False

    async def delete_user(self, user_id: str) -> bool:
        """Mark user as deleted (soft delete)"""
        try:
            result = await self.collection.update_one(
                {"_id": ObjectId(user_id)},
                {
                    "$set": {
                        "is_deleted": True,
                        "deleted_at": datetime.now(),
                        "updated_at": datetime.now()
                    }
                }
            )

            if result.modified_count > 0:
                return True
            return False
        except Exception as e:
            logger.error(f"Error marking user as deleted: {e}")
            return False

    async def add_session(self, user_id: str, session_data: dict) -> bool:
        """Add session to user (for audit purposes only)"""
        try:
            await self.collection.update_one(
                {"_id": ObjectId(user_id)},
                {"$push": {"sessions": session_data}}
            )
            return True
        except Exception:
            return False

    async def remove_session(self, user_id: str, session_id: str) -> bool:
        """Remove session from user (for audit purposes only)"""
        try:
            await self.collection.update_one(
                {"_id": ObjectId(user_id)},
                {"$pull": {"sessions": {"session_id": session_id}}}
            )
            return True
        except Exception:
            return False

    async def invalidate_refresh_token(self, user_id: str, refresh_token: str) -> bool:
        """Add refresh token to blacklist (hybrid approach: Redis + Database)"""
        try:
            # Try Redis first (fast)
            redis_success = redis_blacklist_service.invalidate_refresh_token(refresh_token, user_id)

            # Always store in database for audit (persistent)
            blacklist_collection = self.db["refresh_token_blacklist"]
            await blacklist_collection.insert_one({
                "refresh_token": refresh_token,
                "user_id": user_id,
                "invalidated_at": datetime.now(),
                "expires_at": datetime.now() + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS),
                "redis_success": redis_success
            })

            return True

        except Exception as e:
            logger.error(f"Failed to invalidate refresh token: {e}")
            return False

    async def is_refresh_token_blacklisted(self, refresh_token: str) -> bool:
        """Check if refresh token is blacklisted (hybrid approach: Redis first, then Database)"""
        try:
            # Check Redis first (fast)
            if redis_blacklist_service.is_refresh_token_blacklisted(refresh_token):
                return True

            # Fallback to database (slower but persistent)
            blacklist_collection = self.db["refresh_token_blacklist"]
            return await blacklist_collection.find_one({"refresh_token": refresh_token}) is not None

        except Exception as e:
            logger.error(f"Failed to check blacklist: {e}")
            return False

    async def admin_unlock_user(self, user_id: str, admin_id: str) -> bool:
        """Admin unlock user account"""
        try:
            result = await self.collection.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": {
                    "login_attempts": 0,
                    "locked_until": None,
                    "requires_admin_unlock": False,
                    "unlocked_by_admin": admin_id,
                    "unlocked_at": datetime.now(),
                    "updated_at": datetime.now()
                }}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error unlocking user: {e}")
            return False

    async def admin_reset_password(self, user_id: str, new_password: str, admin_id: str) -> bool:
        """Admin reset user password and unlock account"""
        try:
            # Hash new password
            salt = bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS)
            hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), salt)

            # Get current user to preserve password history
            user_doc = await self.collection.find_one({"_id": ObjectId(user_id)})
            if not user_doc:
                return False

            # Update password history
            password_history = user_doc.get("password_history", [])
            if user_doc.get("password_hash"):
                password_history.append({
                    "password_hash": user_doc["password_hash"],
                    "changed_at": user_doc.get("password_changed_at", datetime.now())
                })

            # Keep only recent passwords
            if len(password_history) > settings.PASSWORD_HISTORY_COUNT:
                password_history = password_history[-settings.PASSWORD_HISTORY_COUNT:]

            result = await self.collection.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": {
                    "password_hash": hashed_password,
                    "password_changed_at": datetime.now(),
                    "password_expires_at": datetime.now() + timedelta(days=settings.PASSWORD_EXPIRY_DAYS),
                    "password_history": password_history,
                    "login_attempts": 0,
                    "locked_until": None,
                    "requires_admin_unlock": False,
                    "password_reset_by_admin": admin_id,
                    "password_reset_at": datetime.now(),
                    "updated_at": datetime.now()
                }}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error resetting password: {e}")
            return False

    def close(self):
        """Close database connection - now handled by singleton"""
        # Connection is managed by the singleton pattern in database.py
        pass


# Create global user model instance
user_model = User()
