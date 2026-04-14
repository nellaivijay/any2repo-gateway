"""Tenant management routes (admin-level).

In production these would be protected by an additional admin-only
auth layer.  For now they share the same API-key auth.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from app.models.schemas import (
    Tenant,
    TunnelRegistrationRequest,
    TunnelRegistrationResponse,
)

router = APIRouter(prefix="/api/v1/tenants", tags=["tenants"])


def _get_store():
    """Deferred import to avoid circular dependency."""
    from app.main import get_store
    return get_store()


@router.post("", response_model=Tenant, status_code=201)
async def create_tenant(tenant: Tenant, request: Request) -> Tenant:
    """Register a new tenant."""
    store = _get_store()
    if store.get_tenant(tenant.tenant_id):
        raise HTTPException(
            status_code=409, detail=f"Tenant '{tenant.tenant_id}' already exists"
        )
    store.store_tenant(tenant)
    return tenant


@router.get("/{tenant_id}", response_model=Tenant)
async def read_tenant(tenant_id: str, request: Request) -> Tenant:
    """Get tenant details."""
    store = _get_store()
    tenant = store.get_tenant(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")
    return tenant


@router.get("", response_model=list[Tenant])
async def list_tenants(request: Request) -> list[Tenant]:
    """List all registered tenants."""
    store = _get_store()
    return store.list_tenants()


# ── BYOC Tunnel Registration ────────────────────────────────────────────


@router.post(
    "/{tenant_id}/register-tunnel",
    response_model=TunnelRegistrationResponse,
    status_code=201,
)
async def register_tunnel(
    tenant_id: str,
    body: TunnelRegistrationRequest,
    request: Request,
) -> TunnelRegistrationResponse:
    """Register a BYOC Cloudflare Tunnel endpoint for a tenant.

    Called by the ``cloudflared`` initContainer running in the customer's
    Kubernetes cluster once the tunnel is established.  The gateway
    stores the tunnel URL and uses it to push artifacts on job completion.
    """
    store = _get_store()
    tenant = store.get_tenant(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")

    now = datetime.now(timezone.utc)
    store.update_tenant(
        tenant_id,
        tunnel_url=body.tunnel_url,
        tunnel_registered_at=now,
    )

    return TunnelRegistrationResponse(
        tenant_id=tenant_id,
        tunnel_url=body.tunnel_url,
        registered_at=now,
    )
