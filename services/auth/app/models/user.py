"""
User model for authentication service
"""

from datetime import datetime, timedelta
from typing import Optional, List
from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase
from bson import ObjectId
import bcrypt
from ..core.config import settings
from ..core.dto import UserDTO, AuthUserDTO
from ..services.redis_blacklist import redis_blacklist_service
import logging

logger = logging.getLogger(__name__)


class User:
    """User model for authentication"""

    def __init__(self):
        from ..core.database import get_auth_database
        self.db: AsyncIOMotorDatabase = get_auth_database()
        self.collection: AsyncIOMotorCollection = self.db[settings.MONGODB_AUTH_COLLECTION]

    async def create_user(self, username: str, email: str, password: str, **kwargs) -> UserDTO:
        if await self.get_user_by_username(username):
            raise ValueError("Username already exists")
        if await self.get_user_by_email(email):
            raise ValueError("Email already exists")

        salt = bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS)
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), salt)

        user_data = {
            "username": username,
            "email": email,
            "password_hash": hashed_password,
            "is_active": True,
            "is_verified": False,
            "is_deleted": False,
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
            "sessions": [],
        }

        result = await self.collection.insert_one(user_data)
        user_data["_id"] = result.inserted_id
        return UserDTO.from_db_document(user_data)

    async def get_user_by_id(self, user_id: str) -> Optional[UserDTO]:
        try:
            user_doc = await self.collection.find_one({
                "_id": ObjectId(user_id),
                "is_deleted": {"$ne": True},
            })
            return UserDTO.from_db_document(user_doc) if user_doc else None
        except Exception:
            return None

    async def get_user_by_username(self, username: str) -> Optional[UserDTO]:
        user_doc = await self.collection.find_one({
            "username": {"$regex": f"^{username}$", "$options": "i"},
            "is_deleted": {"$ne": True},
        })
        return UserDTO.from_db_document(user_doc) if user_doc else None

    async def get_user_by_email(self, email: str) -> Optional[UserDTO]:
        user_doc = await self.collection.find_one({
            "email": email,
            "is_deleted": {"$ne": True},
        })
        return UserDTO.from_db_document(user_doc) if user_doc else None

    async def authenticate_user(self, username: str, password: str) -> Optional[AuthUserDTO]:
        user = await self.get_user_by_username(username)
        if not user:
            user = await self.get_user_by_email(username)
        if not user:
            return None

        if user.locked_until and user.locked_until > datetime.now():
            user_doc = await self.collection.find_one({"_id": ObjectId(user.id)})
            if user_doc and user_doc.get("requires_admin_unlock", False):
                raise ValueError("Account is locked and requires admin unlock")
            else:
                raise ValueError("Account is locked")

        if not user.is_active:
            raise ValueError("Account is deactivated")

        user_doc = await self.collection.find_one({"_id": ObjectId(user.id)})
        if not user_doc:
            return None

        if not bcrypt.checkpw(password.encode('utf-8'), user_doc["password_hash"]):
            await self._increment_login_attempts(ObjectId(user.id))
            return None

        await self._reset_login_attempts(ObjectId(user.id))
        await self.collection.update_one(
            {"_id": ObjectId(user.id)},
            {"$set": {"last_login": datetime.now()}}
        )
        user.last_login = datetime.now()
        return AuthUserDTO.from_user_dto(user)

    async def _increment_login_attempts(self, user_id: ObjectId):
        user = await self.collection.find_one({"_id": user_id})
        if not user:
            return
        current_attempts = user.get("login_attempts", 0) + 1
        update_data = {"login_attempts": current_attempts}
        if current_attempts >= settings.MAX_LOGIN_ATTEMPTS:
            update_data["locked_until"] = datetime.now() + timedelta(days=365)
            update_data["requires_admin_unlock"] = True
        await self.collection.update_one({"_id": user_id}, {"$set": update_data})

    async def _reset_login_attempts(self, user_id: ObjectId):
        await self.collection.update_one(
            {"_id": user_id},
            {"$set": {"login_attempts": 0, "locked_until": None, "requires_admin_unlock": False}}
        )

    async def update_user(self, user_id: str, update_data: dict) -> bool:
        try:
            update_data["updated_at"] = datetime.now()
            result = await self.collection.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": update_data}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error updating user: {e}")
            return False

    async def delete_user(self, user_id: str) -> bool:
        try:
            result = await self.collection.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": {"is_deleted": True, "deleted_at": datetime.now(), "updated_at": datetime.now()}}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error marking user as deleted: {e}")
            return False

    async def add_session(self, user_id: str, session_data: dict) -> bool:
        try:
            await self.collection.update_one(
                {"_id": ObjectId(user_id)},
                {"$push": {"sessions": session_data}}
            )
            return True
        except Exception:
            return False

    async def remove_session(self, user_id: str, session_id: str) -> bool:
        try:
            await self.collection.update_one(
                {"_id": ObjectId(user_id)},
                {"$pull": {"sessions": {"session_id": session_id}}}
            )
            return True
        except Exception:
            return False

    async def invalidate_refresh_token(self, user_id: str, refresh_token: str) -> bool:
        try:
            redis_success = await redis_blacklist_service.invalidate_refresh_token(refresh_token, user_id)
            blacklist_collection = self.db["refresh_token_blacklist"]
            await blacklist_collection.insert_one({
                "refresh_token": refresh_token,
                "user_id": user_id,
                "invalidated_at": datetime.now(),
                "expires_at": datetime.now() + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS),
                "redis_success": redis_success,
            })
            return True
        except Exception as e:
            logger.error(f"Failed to invalidate refresh token: {e}")
            return False

    async def is_refresh_token_blacklisted(self, refresh_token: str) -> bool:
        try:
            if await redis_blacklist_service.is_refresh_token_blacklisted(refresh_token):
                return True
            blacklist_collection = self.db["refresh_token_blacklist"]
            return await blacklist_collection.find_one({"refresh_token": refresh_token}) is not None
        except Exception as e:
            logger.error(f"Failed to check blacklist: {e}")
            return False

    async def admin_unlock_user(self, user_id: str, admin_id: str) -> bool:
        try:
            result = await self.collection.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": {
                    "login_attempts": 0,
                    "locked_until": None,
                    "requires_admin_unlock": False,
                    "unlocked_by_admin": admin_id,
                    "unlocked_at": datetime.now(),
                    "updated_at": datetime.now(),
                }}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error unlocking user: {e}")
            return False

    async def admin_reset_password(self, user_id: str, new_password: str, admin_id: str) -> bool:
        try:
            salt = bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS)
            hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), salt)

            user_doc = await self.collection.find_one({"_id": ObjectId(user_id)})
            if not user_doc:
                return False

            password_history = user_doc.get("password_history", [])
            if user_doc.get("password_hash"):
                password_history.append({
                    "password_hash": user_doc["password_hash"],
                    "changed_at": user_doc.get("password_changed_at", datetime.now()),
                })
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
                    "updated_at": datetime.now(),
                }}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error resetting password: {e}")
            return False

    def close(self):
        pass


# Global user model instance
user_model = User()
