"""
MFA controller for handling multi-factor authentication
"""

import secrets
from typing import List, Dict
from datetime import datetime
from fastapi import HTTPException, status
from bson import ObjectId
from ..models.user import user_model
from ..services.mfa_service import mfa_service
from ..schemas.mfa import (
    EnableMFARequest, EnableMFAResponse, VerifyMFARequest, VerifyMFAResponse,
    DisableMFARequest, DisableMFAResponse, MFARecoveryRequest, MFARecoveryResponse,
    MFAStatusResponse
)


class MFAController:
    """MFA controller"""
    
    def __init__(self):
        self.user_model = user_model
        self.mfa_service = mfa_service
    
    def generate_backup_codes(self, count: int = 8) -> List[str]:
        """Generate secure backup codes for account recovery"""
        codes = []
        for _ in range(count):
            # Generate 12-character alphanumeric codes for better security
            code = secrets.token_urlsafe(9).upper().replace('_', '').replace('-', '')[:12]
            # Format as XXXX-XXXX-XXXX for readability
            formatted_code = f"{code[:4]}-{code[4:8]}-{code[8:12]}"
            codes.append(formatted_code)
        return codes
    
    def hash_backup_codes(self, codes: List[str]) -> List[str]:
        """Hash backup codes for secure storage"""
        import hashlib
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
        import hashlib
        try:
            salt_hex, hash_hex = hashed_code.split(':')
            salt = bytes.fromhex(salt_hex)
            stored_hash = bytes.fromhex(hash_hex)
            
            code_hash = hashlib.pbkdf2_hmac('sha256', code.encode(), salt, 100000)
            return code_hash == stored_hash
        except Exception:
            return False
    
    async def enable_mfa(self, user_id: str, request: EnableMFARequest) -> EnableMFAResponse:
        """Enable MFA for user"""
        # Get user
        user = self.user_model.get_user_by_id(user_id)
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
        
        # Update user with MFA secret and hashed backup codes
        self.user_model.update_user(user_id, {
            "mfa_secret": secret,
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
        """Verify MFA code"""
        # Get user
        user = self.user_model.get_user_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Check if MFA is enabled
        if user.mfa_enabled:
            return VerifyMFAResponse(
                verified=True,
                message="MFA is already enabled and verified"
            )
        
        # Get MFA secret from raw document (for security)
        user_doc = self.user_model.collection.find_one({"_id": ObjectId(user_id)})
        if not user_doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        secret = user_doc.get("mfa_secret")
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
            # Mark MFA setup as complete if not already

            self.user_model.update_user(user_id, {
                    "mfa_setup_complete": True,
                    "mfa_enabled": True
            })
            
            return VerifyMFAResponse(
                verified=True,
                message="MFA code verified successfully"
            )
        else:
            # Include rate limit info in response
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=verification_result['message']
            )
    
    async def disable_mfa(self, user_id: str, request: DisableMFARequest) -> DisableMFAResponse:
        """
        Disable MFA for user
        
        IMPROVED SECURITY: Only requires TOTP code verification
        - User is already authenticated via access token
        - TOTP code proves they control the authenticator device
        - No password required (reduces security exposure)
        """
        # Get user
        user = self.user_model.get_user_by_id(user_id)
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
        
        # Get MFA secret from raw document (for security)
        user_doc = self.user_model.collection.find_one({"_id": ObjectId(user_id)})
        if not user_doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        secret = user_doc.get("mfa_secret")
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
        self.user_model.update_user(user_id, {
            "mfa_enabled": False,
            "mfa_secret": None,
            "mfa_setup_complete": False,
            "mfa_backup_codes": []
        })
        
        return DisableMFAResponse(
            disabled=True,
            message="MFA disabled successfully - no password required!"
        )
    
    async def recover_mfa(self, user_id: str, request: MFARecoveryRequest) -> MFARecoveryResponse:
        """Recover MFA using backup code"""
        # Get user
        user = self.user_model.get_user_by_id(user_id)
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
        user_doc = self.user_model.collection.find_one({"_id": ObjectId(user_id)})
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
        
        # Remove used backup code
        hashed_backup_codes.remove(valid_code)
        
        # Generate new backup codes
        new_backup_codes = self.generate_backup_codes()
        new_hashed_codes = self.hash_backup_codes(new_backup_codes)
        
        # Update user with remaining + new hashed codes
        self.user_model.update_user(user_id, {
            "mfa_backup_codes": hashed_backup_codes + new_hashed_codes
        })
        
        return MFARecoveryResponse(
            recovered=True,
            message="Account recovered successfully",
            new_backup_codes=new_backup_codes
        )
    
    async def get_mfa_status(self, user_id: str) -> MFAStatusResponse:
        """Get MFA status for user"""
        # Get user
        user = self.user_model.get_user_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Get backup codes count from raw document (for security)
        user_doc = self.user_model.collection.find_one({"_id": ObjectId(user_id)})
        backup_codes = user_doc.get("mfa_backup_codes", []) if user_doc else []
        
        return MFAStatusResponse(
            enabled=user.mfa_enabled,
            setup_complete=user.mfa_setup_complete,
            backup_codes_remaining=len(backup_codes)
        )
    
    async def emergency_disable_mfa(self, user_id: str, admin_user_id: str, reason: str) -> Dict:
        """
        Emergency MFA disable by admin (for account recovery).
        
        Args:
            user_id: Target user ID
            admin_user_id: Admin user performing the action
            reason: Reason for emergency disable
            
        Returns:
            Dict with operation result
        """
        # Get target user
        user = self.user_model.get_user_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Get admin user
        admin_user = self.user_model.get_user_by_id(admin_user_id)
        if not admin_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Admin user not found"
            )
        
        # Check if MFA is enabled
        if not user.mfa_enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA is not enabled for this user"
            )
        
        # Disable MFA
        self.user_model.update_user(user_id, {
            "mfa_enabled": False,
            "mfa_secret": None,
            "mfa_setup_complete": False,
            "mfa_backup_codes": []
        })
        
        # Reset rate limiting for user
        from .mfa_rate_limiter import mfa_rate_limiter
        mfa_rate_limiter.reset_user(user_id)
        
        logger.warning(f"Emergency MFA disable for user {user_id} by admin {admin_user_id}: {reason}")
        
        return {
            "disabled": True,
            "message": f"MFA emergency disabled by admin. Reason: {reason}",
            "admin_user": admin_user.username,
            "target_user": user.username,
            "timestamp": datetime.now().isoformat()
        }


# Create global MFA controller instance
mfa_controller = MFAController() 