"""
User repository for authentication service
"""

from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from bson import ObjectId
import asyncio
import bcrypt
import logging
from pymongo.collation import Collation

from fastapi import HTTPException, status
from .base_repository import BaseRepository
from ..core.dto import UserDTO, AuthUserDTO
from ..core.config import settings

logger = logging.getLogger(__name__)
CASE_INSENSITIVE_COLLATION = Collation(locale="en", strength=2)


# bcrypt is CPU-bound C code containing no await, so calling it straight from an
# `async def` holds the event loop for its full duration -- about 250ms per call
# at BCRYPT_ROUNDS=12. That used to make concurrent logins serialise AND freeze
# every other route on this service: measured, 60 simultaneous logins took 15-20s
# during which `GET /health` answered in 14,101ms instead of 1-3ms. Because every
# other service validates every request by HTTP call to this one, they hit their
# 10s client timeout and failed CLOSED with 401 -- a login rush 401'd the whole
# platform.
#
# asyncio.to_thread moves the work to the default executor. It does NOT make
# hashing cheaper (nor should it -- the cost factor is the security property);
# it stops that cost being paid out of the event loop's budget. Logins still
# queue against the executor's worker count, but the loop stays free to serve
# validation, health and everything else throughout.


async def _hash_password(password: str) -> bytes:
    """Hash a password without blocking the event loop."""

    def _hash() -> bytes:
        salt = bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS)
        return bcrypt.hashpw(password.encode("utf-8"), salt)

    return await asyncio.to_thread(_hash)


async def _verify_password(password: str, password_hash: Any) -> bool:
    """Verify a password without blocking the event loop."""
    return await asyncio.to_thread(
        bcrypt.checkpw, password.encode("utf-8"), password_hash
    )


class UserRepository(BaseRepository):
    """User repository with user-specific database operations"""

    def __init__(self):
        super().__init__(settings.MONGODB_AUTH_COLLECTION, settings.AUTH_DB_NAME)

    async def create_user(self, username: str, password: str, email: Optional[str] = None, **kwargs) -> UserDTO:
        if await self.get_user_by_username(username):
            raise ValueError("Username already exists")

        if email and await self.get_user_by_email(email):
            raise ValueError("Email already exists")

        hashed_password = await _hash_password(password)

        user_data = {
            "username": username,
            "password_hash": hashed_password,
            "is_active": True,
            "is_verified": False,
            "is_deleted": False,
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
            "last_login": None,
            "login_attempts": 0,
            "locked_until": None,
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
        }
        if email:
            user_data["email"] = email

        user_id = await self.create(user_data)
        user_data["_id"] = ObjectId(user_id)
        return UserDTO.from_db_document(user_data)

    async def get_user_by_id(self, user_id: str) -> Optional[UserDTO]:
        try:
            user_doc = await self.find_one({
                "_id": ObjectId(user_id),
                "is_deleted": {"$ne": True},
            })
            return UserDTO.from_db_document(user_doc) if user_doc else None
        except Exception as e:
            logger.debug("get_user_by_id failed for id %s: %s", user_id, type(e).__name__)
            return None

    async def get_user_by_username(self, username: str) -> Optional[UserDTO]:
        user_doc = await self.find_one({
            "username": username,
            "is_deleted": {"$ne": True},
        }, collation=CASE_INSENSITIVE_COLLATION)
        return UserDTO.from_db_document(user_doc) if user_doc else None

    async def get_user_by_email(self, email: str) -> Optional[UserDTO]:
        if not email:
            return None
        user_doc = await self.find_one({
            "email": email,
            "is_deleted": {"$ne": True},
        }, collation=CASE_INSENSITIVE_COLLATION)
        return UserDTO.from_db_document(user_doc) if user_doc else None

    async def authenticate_user(self, username: str, password: str) -> Optional[AuthUserDTO]:
        user_doc = await self.find_one({
            "$or": [
                {"username": username},
                {"email": username},
            ],
            "is_deleted": {"$ne": True},
        }, collation=CASE_INSENSITIVE_COLLATION)

        if not user_doc:
            return None

        if user_doc.get("locked_until") and user_doc["locked_until"] > datetime.now():
            if user_doc.get("requires_admin_unlock", False):
                logger.warning(f"Account locked for user: {username} - requires admin unlock")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Account is locked. Please contact the administrator.",
                )
            return None

        if not user_doc.get("is_active", False):
            logger.warning(f"Inactive account for user: {username}")
            return None

        try:
            if await _verify_password(password, user_doc["password_hash"]):
                await self._reset_login_attempts(ObjectId(user_doc["_id"]))
                await self.update_last_login(str(user_doc["_id"]))
                user_dto = UserDTO.from_db_document(user_doc)
                return AuthUserDTO.from_user_dto(user_dto)
            else:
                await self._increment_login_attempts(ObjectId(user_doc["_id"]))
                return None
        except Exception as e:
            logger.error(f"Password verification error: {e}")
            return None

    async def _increment_login_attempts(self, user_id: ObjectId):
        user_doc = await self.find_one({"_id": user_id})
        if not user_doc:
            return
        current_attempts = user_doc.get("login_attempts", 0) + 1
        update_data = {"login_attempts": current_attempts}
        if current_attempts >= settings.MAX_LOGIN_ATTEMPTS:
            update_data["locked_until"] = datetime.now() + timedelta(minutes=settings.LOCKOUT_DURATION_MINUTES)
            update_data["requires_admin_unlock"] = True
            logger.warning(f"Account locked for user ID: {user_id} due to {current_attempts} failed attempts")
        await self.update(str(user_id), update_data)

    async def _reset_login_attempts(self, user_id: ObjectId):
        await self.update(str(user_id), {
            "login_attempts": 0,
            "locked_until": None,
            "requires_admin_unlock": False,
        })

    # Fields that must NEVER be set through any user-facing update path regardless of caller
    _PRIVILEGED_FIELDS = frozenset([
        "password_hash", "login_attempts", "locked_until",
        "is_super_admin", "is_deleted", "requires_admin_unlock",
    ])

    async def update_user(self, user_id: str, update_data: dict, modified_by: Optional[str] = None) -> bool:
        try:
            for field in self._PRIVILEGED_FIELDS:
                update_data.pop(field, None)
            update_data["updated_at"] = datetime.now()
            return await self.update(user_id, update_data)
        except Exception as e:
            logger.error(f"Error updating user {user_id}: {e}")
            return False

    async def update_password(self, user_id: str, new_password: str) -> bool:
        try:
            hashed_password = await _hash_password(new_password)

            user_doc = await self.find_one({"_id": ObjectId(user_id)})
            if not user_doc:
                return False

            password_history = user_doc.get("password_history", [])
            if hashed_password in password_history:
                raise ValueError("Password has been used recently")

            password_history.append(hashed_password)
            if len(password_history) > settings.PASSWORD_HISTORY_COUNT:
                password_history.pop(0)

            return await self.update(user_id, {
                "password_hash": hashed_password,
                "password_changed_at": datetime.now(),
                "password_expires_at": datetime.now() + timedelta(days=settings.PASSWORD_EXPIRY_DAYS),
                "password_history": password_history,
                "login_attempts": 0,
                "locked_until": None,
            })
        except Exception as e:
            logger.error(f"Error updating password for user {user_id}: {e}")
            return False

    async def soft_delete_user(self, user_id: str) -> bool:
        try:
            return await self.soft_delete(user_id)
        except Exception as e:
            logger.error(f"Error soft deleting user {user_id}: {e}")
            return False

    async def delete_user(self, user_id: str) -> bool:
        return await self.soft_delete_user(user_id)

    async def update_last_login(self, user_id: str) -> bool:
        try:
            return await self.update(user_id, {"last_login": datetime.now()})
        except Exception as e:
            logger.error(f"Error updating last login for user {user_id}: {e}")
            return False

    async def update_login_attempts(self, user_id: str, attempts: int) -> bool:
        try:
            update_data = {"login_attempts": attempts}
            if attempts >= settings.MAX_LOGIN_ATTEMPTS:
                update_data["locked_until"] = datetime.now() + timedelta(
                    minutes=settings.LOCKOUT_DURATION_MINUTES
                )
            return await self.update(user_id, update_data)
        except Exception as e:
            logger.error(f"Error updating login attempts for user {user_id}: {e}")
            return False

    async def get_active_users(self, limit: int = 100) -> List[UserDTO]:
        try:
            user_docs = await self.find_many({"is_active": True, "is_deleted": {"$ne": True}}, limit=limit)
            return [UserDTO.from_db_document(doc) for doc in user_docs]
        except Exception as e:
            logger.error(f"Error getting active users: {e}")
            return []

    async def get_users_by_role(self, role: str, limit: int = 100) -> List[UserDTO]:
        try:
            user_docs = await self.find_many(
                {"roles": role, "is_active": True, "is_deleted": {"$ne": True}}, limit=limit
            )
            return [UserDTO.from_db_document(doc) for doc in user_docs]
        except Exception as e:
            logger.error(f"Error getting users by role {role}: {e}")
            return []

    async def get_users_with_pagination(
        self, skip: int = 0, limit: int = 100,
        role_filter: Optional[str] = None, is_active_filter: Optional[bool] = None
    ) -> List[UserDTO]:
        try:
            filter_query = {"is_deleted": {"$ne": True}}
            if role_filter:
                filter_query["roles"] = role_filter
            if is_active_filter is not None:
                filter_query["is_active"] = is_active_filter
            user_docs = await self.find_many(filter_query, skip=skip, limit=limit, sort=[("created_at", -1)])
            return [UserDTO.from_db_document(doc) for doc in user_docs]
        except Exception as e:
            logger.error(f"Error getting users with pagination: {e}")
            return []

    async def get_user_with_permissions(self, username: str) -> Optional[AuthUserDTO]:
        user = await self.get_user_by_username(username)
        if not user:
            return None

        all_permissions = []
        accessible_menus = []

        if "admin" in user.roles:
            all_permissions = ["*"]
        else:
            from .role_repository import RoleRepository
            from .menu_role_repository import MenuRoleRepository

            role_repo = RoleRepository()
            menu_role_repo = MenuRoleRepository()

            for role_name in user.roles:
                role = await role_repo.get_role_by_name(role_name)
                if role:
                    all_permissions.extend(role.permissions)
                    role_menus = await menu_role_repo.get_menus_for_role(role.id)
                    accessible_menus.extend(role_menus)

        user.permissions = list(dict.fromkeys(all_permissions))
        user.accessible_menus = list(dict.fromkeys(accessible_menus))
        return AuthUserDTO.from_user_dto(user)

    async def get_user_with_permissions_by_id(self, user_id: str) -> Optional[AuthUserDTO]:
        user = await self.get_user_by_id(user_id)
        if not user:
            return None
        return await self.get_user_with_permissions(user.username)

    async def admin_unlock_user(self, user_id: str, admin_id: str) -> bool:
        try:
            return await self.update(user_id, {
                "login_attempts": 0,
                "locked_until": None,
                "requires_admin_unlock": False,
                "unlocked_by_admin": admin_id,
                "unlocked_at": datetime.now(),
                "updated_at": datetime.now(),
            })
        except Exception as e:
            logger.error(f"Error unlocking user: {e}")
            return False

    async def admin_reset_password(self, user_id: str, new_password: str, admin_id: str) -> bool:
        try:
            logger.info(f"Admin reset password attempt for user_id: {user_id}")
            hashed_password = await _hash_password(new_password)

            user_doc = await self.find_one({"_id": ObjectId(user_id)})
            if not user_doc:
                logger.warning(f"User not found for admin reset: {user_id}")
                return False

            password_history = user_doc.get("password_history", [])
            if user_doc.get("password_hash"):
                password_history.append({
                    "password_hash": user_doc["password_hash"],
                    "changed_at": user_doc.get("password_changed_at", datetime.now()),
                })
            if len(password_history) > settings.PASSWORD_HISTORY_COUNT:
                password_history = password_history[-settings.PASSWORD_HISTORY_COUNT:]

            result = await self.update(user_id, {
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
            })
            logger.info(f"Admin reset password result for user_id {user_id}: {result}")
            return result
        except Exception as e:
            logger.error(f"Error resetting password: {e}")
            return False

    async def get_lockout_status(self, user_id: str) -> Dict[str, Any]:
        try:
            user_doc = await self.find_one({"_id": ObjectId(user_id)})
            if not user_doc:
                return {"error": "User not found"}
            lockout_until = user_doc.get("locked_until")
            is_locked = bool(lockout_until and lockout_until > datetime.now())
            return {
                "is_locked": is_locked,
                "requires_admin_unlock": user_doc.get("requires_admin_unlock", False),
                "remaining_attempts": max(0, settings.MAX_LOGIN_ATTEMPTS - user_doc.get("login_attempts", 0)),
                "lockout_until": lockout_until.isoformat() if lockout_until else None,
                "user_id": user_id,
            }
        except Exception as e:
            logger.error(f"Error getting lockout status: {e}")
            return {"error": str(e)}
