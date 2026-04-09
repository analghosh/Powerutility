"""
User repository for authentication service
"""

from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from bson import ObjectId
import bcrypt
import logging

from fastapi import HTTPException,status
from .base_repository import BaseRepository
from ..repository.role_repository import RoleRepository
from ..core.dto import UserDTO, AuthUserDTO
from ..core.config import settings

logger = logging.getLogger(__name__)


class UserRepository(BaseRepository):
    """User repository with user-specific database operations"""

    def __init__(self):
        super().__init__(settings.MONGODB_AUTH_COLLECTION, settings.AUTH_DB_NAME)

    async def create_user(self, username: str, password: str, email: Optional[str] = None, **kwargs) -> UserDTO:
        """Create a new user (case-insensitive username)"""
        # Check if user already exists (case-insensitive)
        if await self.get_user_by_username(username):
            raise ValueError("Username already exists")

        if email and await self.get_user_by_email(email):
            raise ValueError("Email already exists")

        # Hash password
        salt = bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS)
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), salt)

        # Create user document
        user_data = {
            "username": username,
            "password_hash": hashed_password,
            "is_active": True,
            "is_verified": False,
            "is_deleted": False,  # Hidden field for soft delete
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
            "must_change_password": True  # Force password change on first login
        }
        if email:
            user_data["email"] = email

        user_id = await self.create(user_data)
        user_data["_id"] = ObjectId(user_id)

        return UserDTO.from_db_document(user_data)

    async def get_user_by_id(self, user_id: str) -> Optional[UserDTO]:
        """Get user by ID"""
        try:
            # Exclude deleted users
            user_doc = await self.find_one({
                "_id": ObjectId(user_id),
                "is_deleted": {"$ne": True}
            })
            return UserDTO.from_db_document(user_doc) if user_doc else None
        except:
            return None

    async def get_user_by_username(self, username: str) -> Optional[UserDTO]:
        """Get user by username (case-insensitive)"""
        # Username is case-insensitive, so we use case-insensitive match
        user_doc = await self.find_one({
            "username": {"$regex": f"^{username}$", "$options": "i"},
            "is_deleted": {"$ne": True}
        })
        return UserDTO.from_db_document(user_doc) if user_doc else None

    async def get_user_by_email(self, email: str) -> Optional[UserDTO]:
        """Get user by email (case-insensitive)"""
        # Email is case-insensitive, so we use case-insensitive match
        if not email:  # no-op if email is None/empty
            return None
        user_doc = await self.find_one({
            "email": {"$regex": f"^{email}$", "$options": "i"},
            "is_deleted": {"$ne": True}
        })
        return UserDTO.from_db_document(user_doc) if user_doc else None

    async def authenticate_user(self, username: str, password: str) -> Optional[AuthUserDTO]:
        """Authenticate user with username/email and password"""
        # Try to find user by username or email (case-insensitive)
        user_doc = await self.find_one({
            "$or": [
                {"username": {"$regex": f"^{username}$", "$options": "i"}},
                {"email": {"$regex": f"^{username}$", "$options": "i"}}
            ],
            "is_deleted": {"$ne": True}
        })

        if not user_doc:
            return None

        # Check if account is locked
        if user_doc.get("locked_until") and user_doc["locked_until"] > datetime.now():
            if user_doc.get("requires_admin_unlock", False):
                logger.warning(f"Account locked for user: {username} - requires admin unlock")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Account is locked. Please contact the administrator."
                )
            else:
                logger.warning(f"Account locked for user: {username}")
            return None

        # Check if account is active
        if not user_doc.get("is_active", False):
            logger.warning(f"Inactive account for user: {username}")
            return None

        # Verify password
        try:
            if bcrypt.checkpw(password.encode('utf-8'), user_doc["password_hash"]):
                # Reset login attempts on successful login
                await self._reset_login_attempts(ObjectId(user_doc["_id"]))

                # Update last login
                await self.update_last_login(str(user_doc["_id"]))

                # Create AuthUserDTO from UserDTO
                user_dto = UserDTO.from_db_document(user_doc)
                return AuthUserDTO.from_user_dto(user_dto)
            else:
                # Increment failed login attempts
                await self._increment_login_attempts(ObjectId(user_doc["_id"]))
                return None
        except Exception as e:
            logger.error(f"Password verification error: {e}")
            return None

    async def _increment_login_attempts(self, user_id: ObjectId):
        """Increment failed login attempts and lock account if needed"""
        user_doc = await self.find_one({"_id": user_id})
        if not user_doc:
            return

        current_attempts = user_doc.get("login_attempts", 0) + 1
        update_data = {"login_attempts": current_attempts}

        # Lock account if too many attempts - requires admin unlock
        if current_attempts >= settings.MAX_LOGIN_ATTEMPTS:
            # Set locked_until to a far future date to require admin intervention
            update_data["locked_until"] = datetime.now() + timedelta(days=365)  # 1 year - effectively permanent
            update_data["requires_admin_unlock"] = True
            logger.warning(f"Account locked for user ID: {user_id} due to {current_attempts} failed attempts - requires admin unlock")

        await self.update(str(user_id), update_data)

    async def _reset_login_attempts(self, user_id: ObjectId):
        """Reset failed login attempts"""
        await self.update(str(user_id), {
            "login_attempts": 0,
            "locked_until": None,
            "requires_admin_unlock": False
        })

    async def update_user(self, user_id: str, update_data: dict, modified_by: Optional[str] = None) -> bool:
        """Update user information"""
        try:
            # Remove sensitive fields that shouldn't be updated directly
            sensitive_fields = ["password_hash", "login_attempts", "locked_until"]
            for field in sensitive_fields:
                update_data.pop(field, None)
            # Add updated timestamp
            update_data["updated_at"] = datetime.now()

            return await self.update(user_id, update_data)
        except Exception as e:
            logger.error(f"Error updating user {user_id}: {e}")
            return False

    async def update_password(self, user_id: str, new_password: str) -> bool:
        """Update user password with history tracking"""
        try:
            # Hash new password
            salt = bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS)
            hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), salt)

            # Get current user to check password history
            user_doc = await self.find_one({"_id": ObjectId(user_id)})
            if not user_doc:
                return False

            # Check password history
            password_history = user_doc.get("password_history", [])
            if hashed_password in password_history:
                raise ValueError("Password has been used recently")

            # Update password with history
            password_history.append(hashed_password)
            if len(password_history) > settings.PASSWORD_HISTORY_COUNT:
                password_history.pop(0)  # Remove oldest password

            update_data = {
                "password_hash": hashed_password,
                "password_changed_at": datetime.now(),
                "password_expires_at": datetime.now() + timedelta(days=settings.PASSWORD_EXPIRY_DAYS),
                "password_history": password_history,
                "login_attempts": 0,  # Reset login attempts
                "locked_until": None,  # Unlock account
                "must_change_password": False  # Clear force-change flag after successful password change
            }

            return await self.update(user_id, update_data)
        except Exception as e:
            logger.error(f"Error updating password for user {user_id}: {e}")
            return False

    async def soft_delete_user(self, user_id: str) -> bool:
        """Soft delete user (mark as deleted)"""
        try:
            return await self.soft_delete(user_id)
        except Exception as e:
            logger.error(f"Error soft deleting user {user_id}: {e}")
            return False

    async def delete_user(self, user_id: str) -> bool:
        """Delete user (alias for soft_delete_user for API compatibility)"""
        return await self.soft_delete_user(user_id)

    async def update_last_login(self, user_id: str) -> bool:
        """Update user's last login timestamp"""
        try:
            return await self.update(user_id, {
                "last_login": datetime.now()
            })
        except Exception as e:
            logger.error(f"Error updating last login for user {user_id}: {e}")
            return False

    async def update_login_attempts(self, user_id: str, attempts: int) -> bool:
        """Update user's login attempts"""
        try:
            update_data = {"login_attempts": attempts}

            # Lock account if too many attempts
            if attempts >= settings.MAX_LOGIN_ATTEMPTS:
                lock_duration = settings.LOCKOUT_DURATION_MINUTES
                update_data["locked_until"] = datetime.now() + timedelta(minutes=lock_duration)

            return await self.update(user_id, update_data)
        except Exception as e:
            logger.error(f"Error updating login attempts for user {user_id}: {e}")
            return False

    async def get_active_users(self, limit: int = 100) -> List[UserDTO]:
        """Get all active users"""
        try:
            user_docs = await self.find_many({
                "is_active": True,
                "is_deleted": {"$ne": True}
            }, limit=limit)

            return [UserDTO.from_db_document(doc) for doc in user_docs]
        except Exception as e:
            logger.error(f"Error getting active users: {e}")
            return []

    async def get_users_by_role(self, role: str, limit: int = 100) -> List[UserDTO]:
        """Get users by role"""
        try:
            user_docs = await self.find_many({
                "roles": role,
                "is_active": True,
                "is_deleted": {"$ne": True}
            }, limit=limit)

            return [UserDTO.from_db_document(doc) for doc in user_docs]
        except Exception as e:
            logger.error(f"Error getting users by role {role}: {e}")
            return []

    async def get_users_with_pagination(self, skip: int = 0, limit: int = 100, role_filter: Optional[str] = None, is_active_filter: Optional[bool] = None) -> List[UserDTO]:
        """Get users with pagination and filtering"""
        try:
            # Build filter query
            filter_query = {"is_deleted": {"$ne": True}}

            if role_filter:
                filter_query["roles"] = role_filter

            if is_active_filter is not None:
                filter_query["is_active"] = is_active_filter

            # Get users with pagination
            user_docs = await self.find_many(
                filter_query,
                skip=skip,
                limit=limit,
                sort=[("created_at", -1)]  # Sort by creation date, newest first
            )

            return [UserDTO.from_db_document(doc) for doc in user_docs]
        except Exception as e:
            logger.error(f"Error getting users with pagination: {e}")
            return []


    async def get_user_with_permissions(self, username: str) -> Optional[AuthUserDTO]:
        """Load user + permissions from assigned roles (permissions are synced from menu assignments)"""
        user = await self.get_user_by_username(username)
        if not user:
            return None

        # Get consolidated permissions from all user roles
        all_permissions = []
        accessible_menus = []

        # Admin role gets all permissions
        if "admin" in user.roles:
            all_permissions = ["*"]
        else:
            # Get permissions from role assignments (already synced with menu permissions)
            from .role_repository import RoleRepository
            from .menu_role_repository import MenuRoleRepository

            role_repo = RoleRepository()
            menu_role_repo = MenuRoleRepository()

            # Get permissions from all user roles
            for role_name in user.roles:
                role = await role_repo.get_role_by_name(role_name)
                if role:
                    # Role permissions are already synced with menu permissions
                    all_permissions.extend(role.permissions)

                    # Get accessible menu IDs for this role
                    role_menus = await menu_role_repo.get_menus_for_role(role.id)
                    accessible_menus.extend(role_menus)

        # Remove duplicates while preserving order
        user.permissions = list(dict.fromkeys(all_permissions))
        user.accessible_menus = list(dict.fromkeys(accessible_menus))

        return AuthUserDTO.from_user_dto(user)

    async def get_user_with_permissions_by_id(self, user_id: str) -> Optional[AuthUserDTO]:
        """Load user + permissions by ID (optimized for verify_login)"""
        user = await self.get_user_by_id(user_id)
        if not user:
            return None

        # Use the same logic as get_user_with_permissions but with username
        return await self.get_user_with_permissions(user.username)

    async def admin_unlock_user(self, user_id: str, admin_id: str) -> bool:
        """Admin unlock user account"""
        try:
            result = await self.update(user_id, {
                "login_attempts": 0,
                "locked_until": None,
                "requires_admin_unlock": False,
                "unlocked_by_admin": admin_id,
                "unlocked_at": datetime.now(),
                "updated_at": datetime.now()
            })
            return result
        except Exception as e:
            logger.error(f"Error unlocking user: {e}")
            return False

    async def admin_reset_password(self, user_id: str, new_password: str, admin_id: str) -> bool:
        """Admin reset user password and unlock account"""
        try:
            logger.info(f"Admin reset password attempt for user_id: {user_id}")

            # Hash new password
            salt = bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS)
            hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), salt)

            # Get current user to preserve password history
            user_doc = await self.find_one({"_id": ObjectId(user_id)})
            if not user_doc:
                logger.warning(f"User not found for admin reset: {user_id}")
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
                "updated_at": datetime.now()
            })
            logger.info(f"Admin reset password result for user_id {user_id}: {result}")
            return result
        except Exception as e:
            logger.error(f"Error resetting password: {e}")
            return False

    async def get_lockout_status(self, user_id: str) -> Dict[str, Any]:
        """Get user lockout status"""
        try:
            user_doc = await self.find_one({"_id": ObjectId(user_id)})
            if not user_doc:
                return {"error": "User not found"}

            is_locked = False
            lockout_until = user_doc.get("locked_until")
            requires_admin_unlock = user_doc.get("requires_admin_unlock", False)

            if lockout_until and lockout_until > datetime.now():
                is_locked = True

            return {
                "is_locked": is_locked,
                "requires_admin_unlock": requires_admin_unlock,
                "remaining_attempts": max(0, settings.MAX_LOGIN_ATTEMPTS - user_doc.get("login_attempts", 0)),
                "lockout_until": lockout_until.isoformat() if lockout_until else None,
                "user_id": user_id
            }
        except Exception as e:
            logger.error(f"Error getting lockout status: {e}")
            return {"error": str(e)}
