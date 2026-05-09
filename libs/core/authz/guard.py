# libs/core/authz/guard.py
"""
Production-Grade RBAC Guard with Industry Standards
- OWASP compliance
- Audit logging
- Rate limiting
- Session management
- Comprehensive security checks
"""
from __future__ import annotations
from typing import Any, Optional, Set, Dict
from fastapi import Depends, HTTPException, Request
import logging
import time
from datetime import datetime, timedelta
import hashlib
import json
import os

from libs.core.dependencies import get_db, jwt_validator
from libs.core.authz.config import required_permissions_for
from libs.core.authz.principal import UserPrincipal, compute_effective_permissions
from libs.core.authz.context import request_context, RequestContext

# Security logging
logger = logging.getLogger("rbac.security")

# Security constants
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION = timedelta(minutes=15)
RATE_LIMIT_WINDOW = timedelta(minutes=1)
MAX_REQUESTS_PER_MINUTE = 60

# Redis-based security storage
class _RedisSecurityStore:
    """Redis-backed store for rate limiting and failed attempts"""
    
    def __init__(self):
        self._redis: Any = None
        self._enabled = False
        self._init_redis()
    
    def _init_redis(self):
        try:
            import redis
            # Try to connect using common Redis configuration patterns
            # First try auth service settings, then fall back to env vars
            redis_host = os.getenv("REDIS_HOST", "localhost")
            redis_port = int(os.getenv("REDIS_PORT", "6379"))
            redis_db = int(os.getenv("REDIS_SECURITY_DB", "4"))
            redis_password = os.getenv("REDIS_PASSWORD") or None
            
            self._redis = redis.Redis(
                host=redis_host,
                port=redis_port,
                db=redis_db,
                password=redis_password,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
                retry_on_timeout=True,
            )
            self._redis.ping()
            self._enabled = True
            logger.info("Redis security store initialized")
        except Exception as e:
            logger.warning(f"Redis security store unavailable, falling back to in-memory: {e}")
            self._redis = None
            self._enabled = False
    
    def _rl_key(self, fingerprint: str) -> str:
        return f"guard:ratelimit:{fingerprint}"
    
    def _fa_key(self, user_id: str) -> str:
        return f"guard:failed:{user_id}"
    
    def get_rate_limit_data(self, fingerprint: str) -> Optional[Dict]:
        if not self._enabled or not self._redis:
            return None
        try:
            data = self._redis.get(self._rl_key(fingerprint))
            if data:
                import json
                return json.loads(data)
            return None
        except Exception as e:
            logger.error(f"Redis rate limit read error: {e}")
            return None
    
    def set_rate_limit_data(self, fingerprint: str, data: Dict, ttl: int = 120):
        if not self._enabled or not self._redis:
            return
        try:
            import json
            self._redis.setex(self._rl_key(fingerprint), ttl, json.dumps(data, default=str))
        except Exception as e:
            logger.error(f"Redis rate limit write error: {e}")
    
    def get_failed_attempts(self, user_id: str) -> Optional[Dict]:
        if not self._enabled or not self._redis:
            return None
        try:
            data = self._redis.get(self._fa_key(user_id))
            if data:
                import json
                parsed = json.loads(data)
                # Convert lockout_until back to datetime if present
                if parsed.get("lockout_until"):
                    parsed["lockout_until"] = datetime.fromisoformat(parsed["lockout_until"])
                return parsed
            return None
        except Exception as e:
            logger.error(f"Redis failed attempts read error: {e}")
            return None
    
    def set_failed_attempts(self, user_id: str, data: Dict, ttl: int = 900):
        if not self._enabled or not self._redis:
            return
        try:
            import json
            # Convert datetime to isoformat for serialization
            serializable = dict(data)
            if serializable.get("lockout_until"):
                serializable["lockout_until"] = serializable["lockout_until"].isoformat()
            self._redis.setex(self._fa_key(user_id), ttl, json.dumps(serializable))
        except Exception as e:
            logger.error(f"Redis failed attempts write error: {e}")
    
    def delete_failed_attempts(self, user_id: str):
        if not self._enabled or not self._redis:
            return
        try:
            self._redis.delete(self._fa_key(user_id))
        except Exception as e:
            logger.error(f"Redis failed attempts delete error: {e}")
    
    def clear_all(self):
        if not self._enabled or not self._redis:
            return
        try:
            for key in self._redis.scan_iter(match="guard:*"):
                self._redis.delete(key)
        except Exception as e:
            logger.error(f"Redis clear all error: {e}")

# Backward-compatible in-memory fallback
_rate_limit_store: Dict[str, Dict] = {}
_failed_attempts: Dict[str, Dict] = {}
_redis_store = _RedisSecurityStore()

class SecurityViolation(Exception):
    """Custom exception for security violations"""
    def __init__(self, violation_type: str, details: str, user_id: str = None):
        self.violation_type = violation_type
        self.details = details
        self.user_id = user_id
        super().__init__(f"{violation_type}: {details}")

# --- Security helpers ------------------------------------------------

def _get_client_fingerprint(request: Request) -> str:
    """Generate client fingerprint for security tracking"""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "")
    x_forwarded = request.headers.get("x-forwarded-for", "")
    
    fingerprint_data = f"{client_ip}:{user_agent}:{x_forwarded}"
    return hashlib.sha256(fingerprint_data.encode()).hexdigest()[:16]

def _check_rate_limit(client_fingerprint: str) -> bool:
    """Check if client is within rate limits"""
    now = datetime.now()
    
    # Try Redis first
    redis_data = _redis_store.get_rate_limit_data(client_fingerprint)
    if redis_data is not None:
        requests = redis_data.get("requests", [])
        window_start = now - RATE_LIMIT_WINDOW
        # Filter out old requests
        requests = [
            datetime.fromisoformat(r) if isinstance(r, str) else r
            for r in requests
            if (datetime.fromisoformat(r) if isinstance(r, str) else r) > window_start
        ]
        if len(requests) >= MAX_REQUESTS_PER_MINUTE:
            _redis_store.set_rate_limit_data(client_fingerprint, {"requests": requests}, ttl=120)
            return False
        requests.append(now.isoformat())
        _redis_store.set_rate_limit_data(client_fingerprint, {"requests": requests}, ttl=120)
        return True
    
    # Fallback to in-memory
    if client_fingerprint not in _rate_limit_store:
        _rate_limit_store[client_fingerprint] = {
            "requests": [],
            "window_start": now
        }
    
    client_data = _rate_limit_store[client_fingerprint]
    
    # Clean old requests outside window
    window_start = now - RATE_LIMIT_WINDOW
    client_data["requests"] = [
        req_time for req_time in client_data["requests"] 
        if req_time > window_start
    ]
    
    # Check if over limit
    if len(client_data["requests"]) >= MAX_REQUESTS_PER_MINUTE:
        return False
    
    # Add current request
    client_data["requests"].append(now)
    return True

def _check_account_lockout(user_id: str) -> bool:
    """Check if account is locked due to failed attempts"""
    # Try Redis first
    redis_data = _redis_store.get_failed_attempts(user_id)
    if redis_data is not None:
        lockout_until = redis_data.get("lockout_until")
        if lockout_until and datetime.now() < lockout_until:
            return True
        return False
    
    # Fallback to in-memory
    if user_id not in _failed_attempts:
        return False
    
    attempt_data = _failed_attempts[user_id]
    lockout_until = attempt_data.get("lockout_until")
    
    if lockout_until and datetime.now() < lockout_until:
        return True
    
    return False

def _record_failed_attempt(user_id: str):
    """Record failed authentication attempt"""
    now = datetime.now()
    
    # Try Redis first
    redis_data = _redis_store.get_failed_attempts(user_id)
    if redis_data is not None:
        redis_data["count"] = redis_data.get("count", 0) + 1
        redis_data["last_attempt"] = now.isoformat()
        if redis_data["count"] >= MAX_FAILED_ATTEMPTS:
            redis_data["lockout_until"] = (now + LOCKOUT_DURATION).isoformat()
            logger.warning(f"Account locked due to failed attempts: {user_id}")
        _redis_store.set_failed_attempts(user_id, redis_data, ttl=900)
        return
    
    # Fallback to in-memory
    if user_id not in _failed_attempts:
        _failed_attempts[user_id] = {
            "count": 0,
            "first_attempt": now,
            "last_attempt": now
        }
    
    attempt_data = _failed_attempts[user_id]
    attempt_data["count"] += 1
    attempt_data["last_attempt"] = now
    
    # Lock account if too many failures
    if attempt_data["count"] >= MAX_FAILED_ATTEMPTS:
        attempt_data["lockout_until"] = now + LOCKOUT_DURATION
        logger.warning(f"Account locked due to failed attempts: {user_id}")

def _clear_failed_attempts(user_id: str):
    """Clear failed attempts on successful auth"""
    _redis_store.delete_failed_attempts(user_id)
    if user_id in _failed_attempts:
        del _failed_attempts[user_id]

async def _load_user_doc(db, user_id: str) -> Optional[dict]:
    """Load user document with security checks"""
    if not user_id:
        return None
    
    # Try to find user by string ID first, then by ObjectId
    user_doc = await db.users.find_one({"_id": user_id})
    
    if not user_doc:
        # Try with ObjectId format (for compatibility with auth service)
        try:
            from bson import ObjectId
            user_doc = await db.users.find_one({"_id": ObjectId(user_id)})
        except Exception:
            # If ObjectId conversion fails, user doesn't exist
            pass
    
    # Security check: ensure user is active
    if user_doc and not user_doc.get("is_active", True):
        raise SecurityViolation("INACTIVE_USER", f"User account is inactive: {user_id}", user_id)
    
    return user_doc

def _extract_bearer_token(request: Request) -> str:
    """Extract and validate bearer token with security checks"""
    auth = request.headers.get("Authorization", "")
    
    if not auth:
        raise HTTPException(status_code=401, detail="Authorization header required")
    
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization format")
    
    token = auth.split(" ", 1)[1]
    
    # Basic token format validation
    if len(token) < 10:  # Minimum reasonable token length
        raise HTTPException(status_code=401, detail="Invalid token format")
    
    return token

def _log_security_event(event_type: str, details: dict, request: Request, user_id: str = None):
    """Log security events for audit trail"""
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "")
    
    log_data = {
        "timestamp": datetime.now().isoformat(),
        "event_type": event_type,
        "client_ip": client_ip,
        "user_agent": user_agent,
        "user_id": user_id,
        "path": request.url.path,
        "method": request.method,
        **details
    }
    
    logger.info(f"Security Event: {json.dumps(log_data)}")

# --- Enhanced principal builder --------------------------------------

async def get_current_user(
    request: Request,
    ctx: RequestContext = Depends(request_context),
    db: Any = Depends(get_db),
) -> UserPrincipal:
    """
    Production-grade user authentication with comprehensive security checks
    """
    client_fingerprint = _get_client_fingerprint(request)
    
    try:
        # Rate limiting check
        if not _check_rate_limit(client_fingerprint):
            _log_security_event("RATE_LIMIT_EXCEEDED", {
                "client_fingerprint": client_fingerprint
            }, request)
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        
        # Extract and validate token
        token = _extract_bearer_token(request)
        
        # Validate JWT token
        payload, err = jwt_validator.validate_token_with_details(token)
        if err or payload is None:
            _log_security_event("INVALID_TOKEN", {
                "error": err,
                "client_fingerprint": client_fingerprint
            }, request)
            raise HTTPException(status_code=401, detail=err or "Invalid or expired token")
        
        user_id = payload.user_id
        
        # Check account lockout
        if _check_account_lockout(user_id):
            _log_security_event("ACCOUNT_LOCKED", {
                "user_id": user_id,
                "client_fingerprint": client_fingerprint
            }, request, user_id)
            raise HTTPException(status_code=423, detail="Account temporarily locked")
        
        # Load user with security checks
        user_doc = await _load_user_doc(db, user_id)
        if not user_doc:
            _record_failed_attempt(user_id)
            _log_security_event("USER_NOT_FOUND", {
                "user_id": user_id,
                "client_fingerprint": client_fingerprint
            }, request, user_id)
            raise HTTPException(status_code=401, detail="User not found")
        
        # Clear failed attempts on successful auth
        _clear_failed_attempts(user_id)
        
        tenant_id = ctx.tenant_id
        user_authz_ver = int(user_doc.get("authz_version", 0))
        
        # Compute effective permissions with caching
        db_perms: Set[str] = await compute_effective_permissions(user_doc, tenant_id, db)
        token_perms: Set[str] = set(payload.permissions or [])
        effective: Set[str] = db_perms | token_perms
        
        # Enhanced super-admin detection
        is_super_admin = (
            bool(user_doc.get("is_super_admin", False)) or
            ("admin" in (payload.roles or [])) or
            ("*" in effective) or
            ("admin:*" in effective)
        )
        
        # Log successful authentication
        _log_security_event("AUTH_SUCCESS", {
            "user_id": user_id,
            "username": user_doc.get("username"),
            "roles": payload.roles or [],
            "is_super_admin": is_super_admin,
            "client_fingerprint": client_fingerprint
        }, request, user_id)
        
        return UserPrincipal(
            user_id=user_id,
            tenant_id=tenant_id,
            is_super_admin=is_super_admin,
            authz_version=user_authz_ver,
            effective_permissions=effective,
            roles=payload.roles or [],
        )
        
    except HTTPException:
        raise
    except SecurityViolation as e:
        _log_security_event("SECURITY_VIOLATION", {
            "violation_type": e.violation_type,
            "details": e.details,
            "client_fingerprint": client_fingerprint
        }, request, e.user_id)
        raise HTTPException(status_code=403, detail=e.details)
    except Exception as e:
        _log_security_event("AUTH_ERROR", {
            "error": str(e),
            "error_type": type(e).__name__,
            "client_fingerprint": client_fingerprint
        }, request)
        raise HTTPException(status_code=500, detail="Authentication error")

# --- Production-grade global guard ----------------------------------

async def global_authz_guard(
    request: Request,
    user: UserPrincipal = Depends(get_current_user),
    db: Any = Depends(get_db),
):
    """
    Production-grade authorization guard with comprehensive security
    - OWASP compliance
    - Detailed audit logging
    - Permission validation
    - Security violation detection
    """
    start_time = time.time()
    
    try:
        # Super admin bypass with logging
        if user.is_super_admin:
            _log_security_event("SUPER_ADMIN_ACCESS", {
                "user_id": user.user_id,
                "path": request.url.path,
                "method": request.method
            }, request, user.user_id)
            return
        
        # Get route information
        route = request.scope.get("route")
        if not route:
            return
        
        path_template = getattr(route, "path_format", request.url.path)
        method = request.method.upper()
        
        # Determine required permissions
        required = await required_permissions_for(db, method, path_template)
        
        # Public route check
        if not required:
            _log_security_event("PUBLIC_ACCESS", {
                "user_id": user.user_id,
                "path": path_template,
                "method": method
            }, request, user.user_id)
            return
        
        user_permissions = user.effective_permissions
        
        # Admin wildcard check
        if "admin:*" in required:
            if ("admin:*" in user_permissions) or ("*" in user_permissions):
                _log_security_event("ADMIN_ACCESS_GRANTED", {
                    "user_id": user.user_id,
                    "path": path_template,
                    "method": method,
                    "required": list(required)
                }, request, user.user_id)
                return
        
        # Detailed permission checking
        missing = [p for p in required if p not in user_permissions]
        
        if missing:
            # Log authorization failure
            _log_security_event("ACCESS_DENIED", {
                "user_id": user.user_id,
                "path": path_template,
                "method": method,
                "required_permissions": list(required),
                "user_permissions": list(user_permissions),
                "missing_permissions": missing
            }, request, user.user_id)
            
            raise HTTPException(
                status_code=403, 
                detail=f"Access denied. Missing permissions: {', '.join(missing)}"
            )
        
        # Log successful authorization
        _log_security_event("ACCESS_GRANTED", {
            "user_id": user.user_id,
            "path": path_template,
            "method": method,
            "required_permissions": list(required),
            "processing_time_ms": round((time.time() - start_time) * 1000, 2)
        }, request, user.user_id)
        
    except HTTPException:
        raise
    except Exception as e:
        _log_security_event("AUTHZ_ERROR", {
            "user_id": user.user_id,
            "path": request.url.path,
            "method": request.method,
            "error": str(e),
            "error_type": type(e).__name__
        }, request, user.user_id)
        raise HTTPException(status_code=500, detail="Authorization error")

# --- Security management functions ----------------------------------

def reset_rate_limits():
    """Reset all rate limits (admin function)"""
    global _rate_limit_store
    _rate_limit_store.clear()
    _redis_store.clear_all()
    logger.info("Rate limits reset")

def reset_failed_attempts():
    """Reset all failed attempts (admin function)"""
    global _failed_attempts
    _failed_attempts.clear()
    _redis_store.clear_all()
    logger.info("Failed attempts reset")

def get_security_stats() -> dict:
    """Get current security statistics"""
    locked_in_memory = len([
        user_id for user_id, data in _failed_attempts.items()
        if data.get("lockout_until") and datetime.now() < data["lockout_until"]
    ])
    return {
        "active_rate_limits": len(_rate_limit_store),
        "locked_accounts": locked_in_memory,
        "failed_attempt_records": len(_failed_attempts),
        "redis_enabled": _redis_store._enabled
    }
