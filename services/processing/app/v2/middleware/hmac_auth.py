"""
HMAC Authentication Middleware
FastAPI dependency for HMAC signature authentication
"""

import logging
from typing import Dict, Any
from fastapi import Request, HTTPException, status
from ..repository.api_key_repository import APIKeyRepository
from ..services.hmac_auth_service import hmac_auth_service
from ..services.rate_limit_service import rate_limit_service
from ...core.config import settings

logger = logging.getLogger(__name__)

# Create repository instance
api_key_repo = APIKeyRepository()


async def verify_hmac_auth(request: Request) -> Dict[str, Any]:
    """
    FastAPI dependency to verify HMAC authentication
    
    Args:
        request: FastAPI Request object
        
    Returns:
        Dictionary with API key information
        
    Raises:
        HTTPException: If authentication fails
    """
    # Extract required headers
    api_key = request.headers.get("X-API-Key")
    timestamp = request.headers.get("X-Timestamp")
    nonce = request.headers.get("X-Nonce")
    signature = request.headers.get("X-Signature")
    
    # Check all headers are present
    if not all([api_key, timestamp, nonce, signature]):
        missing = []
        if not api_key:
            missing.append("X-API-Key")
        if not timestamp:
            missing.append("X-Timestamp")
        if not nonce:
            missing.append("X-Nonce")
        if not signature:
            missing.append("X-Signature")
        
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing required authentication headers: {', '.join(missing)}"
        )
    
    # Lookup API key in database
    try:
        api_key_doc = api_key_repo.find_by_api_key(api_key)
    except Exception as e:
        logger.error(f"Error looking up API key: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error during authentication"
        )
    
    if not api_key_doc:
        logger.warning(f"Invalid API key: {api_key[:8]}...")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key"
        )
    
    # Check if API key is active
    if not api_key_doc.get("is_active", False):
        logger.warning(f"Inactive API key: {api_key[:8]}...")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key is inactive"
        )
    
    # Get secret hash and verify (we need to get the secret from somewhere)
    # Since we only store hashed secrets, we need a different approach
    # For now, we'll need to store the secret in a way we can retrieve it
    # OR we need to change our approach to store encrypted secrets instead of hashed
    
    # Actually, for HMAC, we need the actual secret to verify the signature
    # So we should store encrypted secrets, not hashed ones
    # Let me check the encryption utility...
    
    # For now, let's assume we need to get the secret from the document
    # We'll need to decrypt it if it's encrypted, or use it directly if stored securely
    # Since we're using bcrypt hashing in the repository, we need to change approach
    # Let me update the approach: we'll store encrypted secrets instead
    
    # Actually, wait - for HMAC verification, we need the plain secret
    # So we should use encryption, not hashing
    # Let me update the API key repository to use encryption instead
    
    # For now, let's assume the secret is stored encrypted and we can decrypt it
    # We'll need to update the repository to handle this properly
    
    # Read body for signature verification
    # Note: Once body is read, it's consumed, so we store it for later use
    body = await request.body()
    body_hash = hmac_auth_service.get_body_hash(body)
    
    # Store body in request state so route handlers can access it
    # For GET requests, body will be empty
    request.state._body = body
    
    # Validate timestamp
    if not hmac_auth_service.validate_timestamp(timestamp):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Request timestamp expired or invalid"
        )
    
    # Validate nonce (check if already used)
    if not hmac_auth_service.validate_nonce(api_key, nonce):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Replay attack detected: nonce already used"
        )
    
    # Check rate limit
    if not rate_limit_service.check_rate_limit(api_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded"
        )
    
    # Get secret from repository (it will decrypt it)
    try:
        secret = api_key_repo.get_secret(api_key)
    except Exception as e:
        logger.error(f"Error getting secret for API key {api_key[:8]}...: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"API key configuration error: {str(e)}"
        )
    
    if not secret:
        logger.error(f"API key {api_key[:8]}... has no secret stored or decryption failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API key configuration error: secret not found or decryption failed"
        )
    
    # Verify signature
    is_valid = hmac_auth_service.verify_signature(
        method=request.method,
        path=request.url.path,
        body_hash=body_hash,
        secret=secret,
        provided_signature=signature,
        timestamp=timestamp,
        nonce=nonce
    )
    
    if not is_valid:
        logger.warning(f"Invalid signature for API key: {api_key[:8]}...")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid signature"
        )
    
    # Update last_used_at timestamp
    api_key_repo.update_last_used(api_key)
    
    # Add API key info to request state
    request.state.api_key = api_key
    request.state.client_name = api_key_doc.get("name", "unknown")
    
    # Return API key context
    return {
        "api_key": api_key,
        "client_name": api_key_doc.get("name", "unknown"),
        "correlation_id": request.headers.get("X-Correlation-ID", "unknown")
    }
