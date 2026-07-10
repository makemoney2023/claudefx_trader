"""
API Authentication for Trading Bot.

Simple API key authentication to protect sensitive endpoints.
"""

import hashlib
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

# Explicit public allowlist — everything else mutating requires auth.
PUBLIC_ALLOWLIST = [
    "/api/health",
    "/api/bot/status",
    "/docs",
    "/openapi.json",
    "/redoc",
]


def is_api_key_configured() -> bool:
    """True when BOT_API_KEY was provided via environment."""
    return bool(os.environ.get("BOT_API_KEY"))


def api_key_fingerprint(key: Optional[str] = None) -> str:
    """Non-reversible fingerprint for audit logs (never log the raw key)."""
    raw = key if key is not None else get_api_key()
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return digest[:12]


def get_api_key() -> str:
    """Get or generate the API key."""
    global _API_KEY

    if _API_KEY is None:
        _API_KEY = os.environ.get("BOT_API_KEY")

        if not _API_KEY:
            _API_KEY = secrets.token_urlsafe(32)
            logger.warning(
                "No API key configured. Set BOT_API_KEY environment variable "
                "to use a persistent key."
            )

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


async def log_authenticated_request(request: Request, call_next):
    """Log requests with API keys for audit trail."""
    api_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")

    if api_key:
        logger.debug(f"Authenticated request: {request.method} {request.url.path}")

    response = await call_next(request)
    return response


def is_public_endpoint(path: str) -> bool:
    """True when endpoint is explicitly public."""
    for public in PUBLIC_ALLOWLIST:
        if path == public or path.startswith(public + "/"):
            return True
    return False


def requires_auth(method: str, path: str) -> bool:
    """Default-protect all mutating routes unless on the public allowlist."""
    if method not in {"POST", "PUT", "DELETE", "PATCH"}:
        return False
    return not is_public_endpoint(path)


# Backward-compatible alias used by legacy tests.
PROTECTED_ENDPOINTS = PUBLIC_ALLOWLIST


def is_protected_endpoint(path: str) -> bool:
    """Legacy helper — treat non-public paths as protected for mutations."""
    return not is_public_endpoint(path)
