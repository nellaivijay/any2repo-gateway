"""Engine discovery and manifest API routes.

Exposes registered engines and their capabilities so that clients
can dynamically discover what engines are available and what inputs
they accept.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from app.engine_manifest import get_manifest, list_manifests
from app.engine_registry import list_supported_backends
from app.models.schemas import EngineManifest

router = APIRouter(prefix="/api/v1/engines", tags=["engines"])


@router.get("", response_model=list[EngineManifest])
async def list_engines(
    request: Request,
    backend: Optional[str] = None,
) -> list[EngineManifest]:
    """List all registered engines and their manifests.

    Optionally filter by supported backend.
    """
    manifests = list_manifests()
    if backend:
        manifests = [
            m for m in manifests
            if backend in [b.value for b in m.supported_backends]
        ]
    return manifests


@router.get("/backends")
async def get_backends(request: Request) -> dict:
    """List all supported cloud / execution backends."""
    return {"backends": list_supported_backends()}


@router.get("/{engine_id}", response_model=EngineManifest)
async def get_engine(engine_id: str, request: Request) -> EngineManifest:
    """Get the manifest for a specific engine."""
    manifest = get_manifest(engine_id)
    if manifest is None:
        raise HTTPException(
            status_code=404,
            detail=f"Engine '{engine_id}' not found",
        )
    return manifest
