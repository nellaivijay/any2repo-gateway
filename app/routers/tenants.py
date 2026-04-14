"""Tenant management routes (admin-level).

In production these would be protected by an additional admin-only
auth layer.  For now they share the same API-key auth.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.models.schemas import Tenant
from app.middleware.auth import register_tenant, get_tenant, _TENANTS

router = APIRouter(prefix="/api/v1/tenants", tags=["tenants"])


@router.post("", response_model=Tenant, status_code=201)
async def create_tenant(tenant: Tenant, request: Request) -> Tenant:
    """Register a new tenant."""
    if get_tenant(tenant.tenant_id):
        raise HTTPException(
            status_code=409, detail=f"Tenant '{tenant.tenant_id}' already exists"
        )
    register_tenant(tenant)
    return tenant


@router.get("/{tenant_id}", response_model=Tenant)
async def read_tenant(tenant_id: str, request: Request) -> Tenant:
    """Get tenant details."""
    tenant = get_tenant(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")
    return tenant


@router.get("", response_model=list[Tenant])
async def list_tenants(request: Request) -> list[Tenant]:
    """List all registered tenants."""
    return list(_TENANTS.values())
