"""Engine registry — maps (engine, cloud_backend) pairs to backend instances.

Supports four cloud backends (GCP Vertex AI, AWS Bedrock, Azure ML,
On-Prem) and dynamic engine registration via manifests.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.backends.base import BaseBackend
from app.backends.gcp_vertex import VertexAIBackend
from app.backends.aws_bedrock import AWSBedrockBackend
from app.backends.azure_ml import AzureMLBackend
from app.backends.on_prem import OnPremBackend
from app.config import settings
from app.engine_manifest import get_manifest
from app.models.schemas import (
    CloudBackend,
    EngineConfig,
    EngineType,
    Tenant,
)

logger = logging.getLogger(__name__)


# ── Backend factory ──────────────────────────────────────────────────────

_BACKEND_MAP: dict[CloudBackend, type[BaseBackend]] = {
    CloudBackend.GCP_VERTEX: VertexAIBackend,
    CloudBackend.AWS_BEDROCK: AWSBedrockBackend,
    CloudBackend.AZURE_ML: AzureMLBackend,
    CloudBackend.ON_PREM: OnPremBackend,
}


def _build_engine_config(
    engine: EngineType,
    backend: CloudBackend,
    tenant: Optional[Tenant] = None,
    engine_id: str = "",
) -> EngineConfig:
    """Build an EngineConfig from settings + optional tenant overrides."""
    eid = engine_id or engine.value
    manifest = get_manifest(eid)

    return EngineConfig(
        engine=engine,
        engine_id=eid,
        cloud_backend=backend,
        # GCP
        gcp_project_id=(
            (tenant.gcp_project_id if tenant and tenant.gcp_project_id else "")
            or settings.gcp_project_id
        ),
        gcp_region=settings.gcp_region,
        # AWS
        aws_region=settings.aws_region,
        aws_role_arn=(
            (tenant.aws_role_arn if tenant and tenant.aws_role_arn else "")
            or settings.aws_role_arn
        ),
        # Azure
        azure_subscription_id=(
            (tenant.azure_subscription_id if tenant and tenant.azure_subscription_id else "")
            or settings.azure_subscription_id
        ),
        azure_resource_group=(
            (tenant.azure_resource_group if tenant and tenant.azure_resource_group else "")
            or settings.azure_resource_group
        ),
        azure_workspace_name=(
            (tenant.azure_workspace_name if tenant and tenant.azure_workspace_name else "")
            or settings.azure_workspace_name
        ),
        azure_region=settings.azure_region,
        # On-prem
        on_prem_endpoint=(
            (tenant.on_prem_endpoint if tenant and tenant.on_prem_endpoint else "")
            or settings.on_prem_endpoint
        ),
        on_prem_docker_network=settings.on_prem_docker_network,
        # Plugin manifest
        manifest=manifest,
    )


def get_backend(
    engine: EngineType,
    backend: CloudBackend,
    tenant: Optional[Tenant] = None,
    engine_id: str = "",
) -> BaseBackend:
    """Instantiate the appropriate backend for the given engine + cloud."""
    cls = _BACKEND_MAP.get(backend)
    if cls is None:
        raise ValueError(f"Unsupported cloud backend: {backend}")

    config = _build_engine_config(engine, backend, tenant, engine_id=engine_id)
    return cls(config)


def list_supported_backends() -> list[str]:
    """Return the list of supported cloud backend identifiers."""
    return [b.value for b in _BACKEND_MAP]
