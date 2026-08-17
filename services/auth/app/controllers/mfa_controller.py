"""
MFA controller for handling multi-factor authentication
"""

import hashlib
import logging
import secrets
import sys
import os
from datetime import datetime
from typing import Dict, List, Optional

from bson import ObjectId
from fastapi import HTTPException, status

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
from libs.crypto import decrypt_secret, encrypt_secret

from ..models.user import user_model
from ..services.mfa_service import mfa_service
from ..schemas.mfa import (
    EnableMFARequest, EnableMFAResponse, VerifyMFARequest, VerifyMFAResponse,
    DisableMFARequest, DisableMFAResponse, MFARecoveryRequest, MFARecoveryResponse,
    MFAStatusResponse
)

logger = logging.getLogger(__name__)

# Crockford-style alphabet: no O/0, I/1, L or U, so a code read off paper and typed back in
# does not fail for a reason the user cannot see. 30 symbols over 12 characters is ~58 bits
# per code, and -- unlike the version this replaces -- it is 58 bits for every code.
_BACKUP_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
_BACKUP_GROUPS = 3
_BACKUP_GROUP_LEN = 4


class MFAController:
    """MFA controller"""

    def __init__(self):
        self.user_model = user_model
        self.mfa_service = mfa_service

    def generate_backup_codes(self, count: int = 8) -> List[str]:
        """
        Generate secure backup codes for account recovery.

        Every code is exactly `XXXX-XXXX-XXXX`. The previous implementation took
        `secrets.token_urlsafe(9)`, stripped `_` and `-` out of it and then truncated to 12
        -- so whenever the random token happened to contain those characters the result was
        shorter than 12 and the last group was a stub. Code length, and therefore entropy,
        varied per code with no indication that it had.
        """
        codes = []
        for _ in range(count):
            raw = "".join(
                secrets.choice(_BACKUP_ALPHABET)
                for _ in range(_BACKUP_GROUPS * _BACKUP_GROUP_LEN)
            )
            groups = [
                raw[i:i + _BACKUP_GROUP_LEN]
                for i in range(0, len(raw), _BACKUP_GROUP_LEN)
            ]
            codes.append("-".join(groups))
        return codes

    def hash_backup_codes(self, codes: List[str]) -> List[str]:
        """Hash backup codes for secure storage"""
        hashed_codes = []
        for code in codes:
            # Use SHA-256 with salt for hashing
            salt = secrets.token_bytes(32)
            code_hash = hashlib.pbkdf2_hmac('sha256', code.encode(), salt, 100000)
            # Store salt + hash
            hashed_codes.append(salt.hex() + ':' + code_hash.hex())
        return hashed_codes

    def verify_backup_code(self, code: str, hashed_code: str) -> bool:
        """Verify backup code against hash"""
        try:
            salt_hex, hash_hex = hashed_code.split(':')
            salt = bytes.fromhex(salt_hex)
            stored_hash = bytes.fromhex(hash_hex)

            code_hash = hashlib.pbkdf2_hmac('sha256', code.encode(), salt, 100000)
            return secrets.compare_digest(code_hash, stored_hash)
        except Exception:
            return False

    async def _get_mfa_secret(self, user_id: str) -> Optional[str]:
        """
        Read the stored TOTP secret and return it in plaintext.

        Secrets are written encrypted, but accounts enrolled before that change hold
        plaintext base32. `decrypt_secret` passes those through unchanged, so both
        populations work from the moment this deploys -- nobody is locked out waiting for a
        migration to run.
        """
        user_doc = await self.user_model.collection.find_one({"_id": ObjectId(user_id)})
        if user_doc is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        stored = user_doc.get("mfa_secret")
        if not stored:
            return None
        try:
            return decrypt_secret(stored)
        except Exception:
            logger.error("Could not decrypt the MFA secret for user %s", user_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="MFA configuration error"
            )

    async def enable_mfa(self, user_id: str, request: EnableMFARequest) -> EnableMFAResponse:
        """Enable MFA for user"""
        # Get user
        user = await self.user_model.get_user_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        # Check if MFA is already enabled
        if user.mfa_enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA is already enabled"
            )

        # Generate MFA secret
        secret = self.mfa_service.generate_secret()

        # Generate backup codes
        backup_codes = self.generate_backup_codes()
        hashed_backup_codes = self.hash_backup_codes(backup_codes)

        # Store the secret encrypted. In plaintext, a single database read hands an attacker
        # a working second factor for every enrolled account, permanently and undetectably.
        await self.user_model.update_user(user_id, {
            "mfa_secret": encrypt_secret(secret),
            "mfa_enabled": False,
            "mfa_backup_codes": hashed_backup_codes,  # Store hashed versions
            "mfa_setup_complete": False  # Will be set to True after verification
        })

        # Generate QR code
        qr_code = self.mfa_service.generate_qr_code(secret, user.username)

        return EnableMFAResponse(
            secret=secret,
            qr_code=qr_code,
            backup_codes=backup_codes,
            remaining_time=self.mfa_service.get_remaining_time()
        )

    async def verify_mfa(self, user_id: str, request: VerifyMFARequest) -> VerifyMFAResponse:
        """
        Verify an MFA code.

        Note what is deliberately absent: the previous version short-circuited on
        `user.mfa_enabled` and returned `verified: true` **without looking at the code**, so
        any authenticated enrolled user got a pass for "000000". The endpoint is reachable
        with nothing but an access token, so that made it a confirmation oracle rather than
        a check.
        """
        # Get user
        user = await self.user_model.get_user_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        secret = await self._get_mfa_secret(user_id)
        if not secret:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA secret not found"
            )

        # Verify TOTP code with rate limiting
        verification_result = self.mfa_service.verify_totp_with_rate_limit(
            user_id, secret, request.code
        )

        if verification_result['rate_limited']:
            # User is rate limited
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=verification_result['message']
            )

        if verification_result['verified']:
            # Complete enrolment on the first successful code. Idempotent: verifying again
            # later re-asserts the same two flags.
            await self.user_model.update_user(user_id, {
                "mfa_setup_complete": True,
                "mfa_enabled": True
            })

            return VerifyMFAResponse(
                verified=True,
                message="MFA code verified successfully"
            )

        # Include rate limit info in response
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=verification_result['message']
        )

    async def disable_mfa(self, user_id: str, request: DisableMFARequest) -> DisableMFAResponse:
        """
        Disable MFA for user

        Only requires TOTP code verification:
        - User is already authenticated via access token
        - TOTP code proves they control the authenticator device
        - No password required (reduces password exposure)
        """
        # Get user
        user = await self.user_model.get_user_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        # Check if MFA is enabled
        if not user.mfa_enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA is not enabled"
            )

        secret = await self._get_mfa_secret(user_id)
        if not secret:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA secret not found"
            )

        # Verify TOTP code with rate limiting (only authentication required)
        verification_result = self.mfa_service.verify_totp_with_rate_limit(
            user_id, secret, request.code
        )

        if verification_result['rate_limited']:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=verification_result['message']
            )

        if not verification_result['verified']:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=verification_result['message']
            )

        # Disable MFA
        await self.user_model.update_user(user_id, {
            "mfa_enabled": False,
            "mfa_secret": None,
            "mfa_setup_complete": False,
            "mfa_backup_codes": []
        })

        return DisableMFAResponse(
            disabled=True,
            message="MFA disabled successfully"
        )

    async def recover_mfa(self, user_id: str, request: MFARecoveryRequest) -> MFARecoveryResponse:
        """
        Spend a backup code to receive a fresh set.

        This is *not* the locked-out-at-login path -- it requires a full access token, which
        somebody who cannot get past the MFA prompt does not have. That path is
        `POST /auth/verify-login` with `backup_code`, which accepts the temporary token
        issued by `/auth/login`. What this endpoint is genuinely for is topping the set back
        up: a backup-code login consumes one, and without this the user has no way to
        replace it short of disabling and re-enrolling MFA.
        """
        # Get user
        user = await self.user_model.get_user_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        # Check if MFA is enabled
        if not user.mfa_enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA is not enabled"
            )

        # Get backup codes from raw document (for security)
        user_doc = await self.user_model.collection.find_one({"_id": ObjectId(user_id)})
        if not user_doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        hashed_backup_codes = user_doc.get("mfa_backup_codes", [])

        # Verify backup code against hashed versions
        valid_code = None
        for hashed_code in hashed_backup_codes:
            if self.verify_backup_code(request.backup_code, hashed_code):
                valid_code = hashed_code
                break

        if not valid_code:
            return MFARecoveryResponse(
                recovered=False,
                message="Invalid backup code"
            )

        # Replace the whole set. The previous version appended the new codes to the old
        # ones, so every recovery grew the list -- eight codes became sixteen, then
        # twenty-four, and codes the user had long since written off stayed valid forever.
        new_backup_codes = self.generate_backup_codes()
        await self.user_model.update_user(user_id, {
            "mfa_backup_codes": self.hash_backup_codes(new_backup_codes)
        })

        return MFARecoveryResponse(
            recovered=True,
            message=(
                f"{len(new_backup_codes)} new backup codes issued. "
                "All previous backup codes are now invalid."
            ),
            new_backup_codes=new_backup_codes
        )

    async def get_mfa_status(self, user_id: str) -> MFAStatusResponse:
        """Get MFA status for user"""
        # Get user
        user = await self.user_model.get_user_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        # Get backup codes count from raw document (for security)
        user_doc = await self.user_model.collection.find_one({"_id": ObjectId(user_id)})
        backup_codes = user_doc.get("mfa_backup_codes", []) if user_doc else []

        return MFAStatusResponse(
            enabled=user.mfa_enabled,
            setup_complete=user.mfa_setup_complete,
            backup_codes_remaining=len(backup_codes)
        )

    async def emergency_disable_mfa(self, user_id: str, admin_user_id: str, reason: str) -> Dict:
        """
        Emergency MFA disable by admin (for account recovery).

        This is the only way back in for a user who has lost the device holding their
        authenticator and has run out of backup codes, so it has to actually work: before
        this change it raised `AttributeError` on an un-awaited coroutine and then
        `ModuleNotFoundError` on a mis-rooted import, and returned HTTP 500 either way.

        Args:
            user_id: Target user ID
            admin_user_id: Admin user performing the action
            reason: Reason for emergency disable

        Returns:
            Dict with operation result
        """
        # Get target user
        user = await self.user_model.get_user_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        # Get admin user -- this call was missing its `await`, so `admin_user` was a
        # coroutine: truthy, so the not-found check passed, and then `.username` blew up.
        admin_user = await self.user_model.get_user_by_id(admin_user_id)
        if not admin_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Admin user not found"
            )

        # Anything at all to clear? Checking `mfa_enabled` alone refused to help a user
        # stranded half-way through enrolment -- secret written, never confirmed -- which is
        # exactly the state a lost phone leaves behind.
        user_doc = await self.user_model.collection.find_one({"_id": ObjectId(user_id)})
        has_state = bool(
            user.mfa_enabled
            or user.mfa_setup_complete
            or (user_doc or {}).get("mfa_secret")
            or (user_doc or {}).get("mfa_backup_codes")
        )
        if not has_state:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA is not enabled for this user"
            )

        # Disable MFA
        await self.user_model.update_user(user_id, {
            "mfa_enabled": False,
            "mfa_secret": None,
            "mfa_setup_complete": False,
            "mfa_backup_codes": []
        })

        # Reset rate limiting for user. The import was `.mfa_rate_limiter`, relative to the
        # controllers package, while the module lives in services/ -- ModuleNotFoundError
        # every time, after the write had already happened.
        from ..services.mfa_rate_limiter import mfa_rate_limiter
        mfa_rate_limiter.reset_user(user_id)

        logger.warning(
            "Emergency MFA disable for user %s by admin %s: %s", user_id, admin_user_id, reason
        )

        return {
            "disabled": True,
            "message": f"MFA emergency disabled by admin. Reason: {reason}",
            "admin_user": admin_user.username,
            "target_user": user.username,
            "timestamp": datetime.now().isoformat()
        }


# Create global MFA controller instance
mfa_controller = MFAController()
