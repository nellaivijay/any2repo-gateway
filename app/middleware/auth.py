"""Tenant authentication middleware.

Validates the ``X-API-Key`` and ``X-Tenant-ID`` headers on every request
(except health-check, OpenAPI docs, and webhook endpoints).  No LLM
frameworks, no heavy dependencies — just dict lookups.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from app.config import settings
from app.models.schemas import Tenant, CloudBackend, EngineType

logger = logging.getLogger(__name__)


# ── Paths exempt from auth ──────────────────────────────────────────────

_PUBLIC_PATHS: set[str] = {
    "/",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
}

_PUBLIC_PREFIXES: tuple[str, ...] = (
    "/api/v1/webhooks/",  # Webhooks use signature-based auth, not API keys
)


def _get_store():
    """Deferred import to avoid circular dependency at module load time."""
    from app.main import get_store
    return get_store()


def seed_default_tenant() -> None:
    """Create a default development tenant if none exist."""
    store = _get_store()
    if not store.list_tenants():
        store.store_tenant(Tenant(
            tenant_id="default",
            name="Development Tenant",
            cloud_backend=CloudBackend.GCP_VERTEX,
            allowed_engines=[EngineType.RESEARCH2REPO, EngineType.QUANT2REPO],
        ))


# ── Middleware ───────────────────────────────────────────────────────────

class TenantAuthMiddleware(BaseHTTPMiddleware):
    """Validate API key + tenant ID on every protected request.

    Headers required:
        X-API-Key:   Must match one of the keys in ``settings.valid_api_keys``.
        X-Tenant-ID: Must correspond to a registered, active tenant.

    On success the resolved :class:`Tenant` is stored in
    ``request.state.tenant`` for downstream route handlers.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path

        # Skip auth for public paths and webhook endpoints
        if path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES):
            return await call_next(request)

        # --- API Key ---
        api_key = request.headers.get("X-API-Key", "")
        valid_keys = settings.valid_api_keys

        if valid_keys and api_key not in valid_keys:
            logger.warning("Rejected request: invalid API key")
            return JSONResponse(status_code=401, content={"detail": "Invalid API key"})

        # --- Tenant ID ---
        tenant_id = request.headers.get("X-Tenant-ID", "")
        if not tenant_id:
            return JSONResponse(status_code=400, content={"detail": "Missing X-Tenant-ID header"})

        store = _get_store()
        tenant = store.get_tenant(tenant_id)
        if tenant is None:
            return JSONResponse(status_code=404, content={"detail": f"Tenant '{tenant_id}' not found"})
        if not tenant.active:
            return JSONResponse(status_code=403, content={"detail": f"Tenant '{tenant_id}' is deactivated"})

        # Attach tenant to request state for downstream use
        request.state.tenant = tenant

        return await call_next(request)
