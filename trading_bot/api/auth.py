"""
API Authentication for Trading Bot.

Simple API key authentication to protect sensitive endpoints.
"""

import os
import secrets
from typing import Optional
from functools import wraps

from fastapi import HTTPException, Security, Depends, Request
from fastapi.security import APIKeyHeader, APIKeyQuery

from ..utils.logging import get_logger

logger = get_logger(__name__)

# API Key header and query parameter
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
API_KEY_QUERY = APIKeyQuery(name="api_key", auto_error=False)

# Get API key from environment or generate one
_API_KEY: Optional[str] = None


def get_api_key() -> str:
    """Get or generate the API key."""
    global _API_KEY
    
    if _API_KEY is None:
        # Try to load from environment
        _API_KEY = os.environ.get("BOT_API_KEY")
        
        if not _API_KEY:
            # Generate a new key and save it
            _API_KEY = secrets.token_urlsafe(32)
            logger.warning(f"No API key configured. Generated: {_API_KEY}")
            logger.warning("Set BOT_API_KEY environment variable to use a persistent key")
            
            # Try to save to .env.local
            try:
                env_file = ".env.local"
                with open(env_file, "a") as f:
                    f.write(f"\n# Auto-generated API key\nBOT_API_KEY={_API_KEY}\n")
                logger.info(f"API key saved to {env_file}")
            except Exception as e:
                logger.warning(f"Could not save API key to file: {e}")
    
    return _API_KEY


def verify_api_key(
    api_key_header: Optional[str] = Security(API_KEY_HEADER),
    api_key_query: Optional[str] = Security(API_KEY_QUERY)
) -> str:
    """
    Verify API key from header or query parameter.
    
    Usage:
        @router.get("/protected")
        async def protected_route(api_key: str = Depends(verify_api_key)):
            ...
    """
    api_key = api_key_header or api_key_query
    
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="API key required. Provide via X-API-Key header or api_key query param"
        )
    
    if api_key != get_api_key():
        raise HTTPException(
            status_code=403,
            detail="Invalid API key"
        )
    
    return api_key


def optional_api_key(
    api_key_header: Optional[str] = Security(API_KEY_HEADER),
    api_key_query: Optional[str] = Security(API_KEY_QUERY)
) -> Optional[str]:
    """
    Optional API key verification - returns None if not provided.
    
    Useful for endpoints that work differently with/without auth.
    """
    api_key = api_key_header or api_key_query
    
    if api_key and api_key == get_api_key():
        return api_key
    
    return None


class RequireAuth:
    """
    Dependency class for requiring authentication on sensitive endpoints.
    
    Usage:
        @router.post("/sensitive", dependencies=[Depends(RequireAuth())])
        async def sensitive_route():
            ...
    """
    
    def __init__(self, required: bool = True):
        self.required = required
    
    async def __call__(
        self,
        api_key_header: Optional[str] = Security(API_KEY_HEADER),
        api_key_query: Optional[str] = Security(API_KEY_QUERY)
    ):
        if not self.required:
            return
        
        api_key = api_key_header or api_key_query
        
        if not api_key:
            raise HTTPException(
                status_code=401,
                detail="Authentication required"
            )
        
        if api_key != get_api_key():
            raise HTTPException(
                status_code=403,
                detail="Invalid API key"
            )


# Middleware for logging authenticated requests
async def log_authenticated_request(request: Request, call_next):
    """Log requests with API keys for audit trail."""
    api_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    
    if api_key:
        logger.debug(f"Authenticated request: {request.method} {request.url.path}")
    
    response = await call_next(request)
    return response


# List of endpoints that require authentication
PROTECTED_ENDPOINTS = [
    "/api/bot/start",
    "/api/bot/stop",
    "/api/bot/emergency-close",
    "/api/bot/weekly-review",
    "/api/bot/positions/",       # Close and modify positions
    "/api/config/trading",
    "/api/config/symbols",
    "/api/config/api-keys",
    "/api/config/alerts",
    "/api/orders/pending",       # Place/cancel pending orders
    "/api/trades/emergency-close-all",
    "/api/learning/prune",
    "/api/learning/consolidate",
]


def is_protected_endpoint(path: str) -> bool:
    """Check if an endpoint requires authentication."""
    for protected in PROTECTED_ENDPOINTS:
        if path.startswith(protected):
            return True
    return False
