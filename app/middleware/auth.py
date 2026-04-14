"""Tenant authentication middleware.

Validates the ``X-API-Key`` and ``X-Tenant-ID`` headers on every request
(except health-check and OpenAPI docs).  No LLM frameworks, no heavy
dependencies — just dict lookups.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from app.config import settings
from app.models.schemas import Tenant, CloudBackend, EngineType

logger = logging.getLogger(__name__)

# ── In-memory tenant store (swap for DB / secrets manager in prod) ───────

_TENANTS: dict[str, Tenant] = {}


def register_tenant(tenant: Tenant) -> None:
    """Register a tenant (called at startup or via admin API)."""
    _TENANTS[tenant.tenant_id] = tenant


def get_tenant(tenant_id: str) -> Optional[Tenant]:
    """Look up a tenant by ID."""
    return _TENANTS.get(tenant_id)


def seed_default_tenant() -> None:
    """Create a default development tenant if none exist."""
    if not _TENANTS:
        register_tenant(Tenant(
            tenant_id="default",
            name="Development Tenant",
            cloud_backend=CloudBackend.GCP_VERTEX,
            allowed_engines=[EngineType.RESEARCH2REPO, EngineType.QUANT2REPO],
        ))


# ── Paths exempt from auth ──────────────────────────────────────────────

_PUBLIC_PATHS: set[str] = {
    "/",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
}


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
        # Skip auth for public paths
        if request.url.path in _PUBLIC_PATHS:
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

        tenant = get_tenant(tenant_id)
        if tenant is None:
            return JSONResponse(status_code=404, content={"detail": f"Tenant '{tenant_id}' not found"})
        if not tenant.active:
            return JSONResponse(status_code=403, content={"detail": f"Tenant '{tenant_id}' is deactivated"})

        # Attach tenant to request state for downstream use
        request.state.tenant = tenant

        return await call_next(request)
