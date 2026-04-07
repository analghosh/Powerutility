import logging
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from ..core.config import settings

logger = logging.getLogger("processing_service.auth")

class SimpleAuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware for simple JWT authentication for V2 routes.
    This is a self-contained middleware for the processing service.
    """
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        
        # Define paths that need authentication
        v2_path_prefix = f"{settings.BASE_PATH}/api/v2"
        v2_health_path = f"{v2_path_prefix}/health"

        # Allow OPTIONS requests to pass through for CORS preflight
        if request.method == "OPTIONS":
            return await call_next(request)

        # Check if the path requires authentication
        if path.startswith(v2_path_prefix) and not path.startswith(v2_health_path):
            logger.info(f"🔐 AUTH REQUIRED for: {path}")
            
            auth_header = request.headers.get("Authorization")
            
            if not auth_header:
                logger.warning(f"❌ NO AUTH HEADER for: {path}")
                # Return a proper 401 Response directly
                return Response(status_code=401, content='{"detail":"Authorization header required"}', media_type="application/json")

            if not auth_header.startswith("Bearer "):
                logger.warning(f"❌ INVALID AUTH FORMAT for: {path}")
                return Response(status_code=401, content='{"detail":"Bearer token required"}', media_type="application/json")

            try:
                token = auth_header.split(" ")[1]
                
                # --- Token Validation ---
                from jose import jwt, JWTError
                
                payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
                user_id: str = payload.get("sub") or payload.get("user_id")
                
                if user_id is None:
                    raise JWTError("user_id missing from token")

                # Attach user context to the request state
                request.state.current_user = {
                    "user_id": user_id,
                    "username": payload.get("username", user_id),
                    "token": token
                }
                logger.info(f"✅ USER CONTEXT SET for: {path} (User: {user_id})")

            except JWTError as e:
                logger.error(f"❌ JWT VALIDATION FAILED for: {path} - Error: {e}")
                return Response(status_code=401, content=f'{{"detail":"Invalid or expired token: {e}"}}', media_type="application/json")
            except Exception as e:
                logger.error(f"❌ AUTH PROCESSING FAILED for: {path} - Error: {e}")
                return Response(status_code=401, content='{"detail":"Invalid token or authorization header"}', media_type="application/json")
        
        else:
            logger.info(f"🔓 NO AUTH NEEDED for: {path}")

        # Continue with the request
        response = await call_next(request)
        return response
