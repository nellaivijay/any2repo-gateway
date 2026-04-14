"""Pydantic models for tenants, jobs, engine configuration, and plugin manifests."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────────


class EngineType(str, Enum):
    """Supported execution engines.

    This enum tracks built-in engines.  Third-party engines register
    dynamically via :class:`EngineManifest` and are referenced by their
    ``engine_id`` string directly.
    """
    RESEARCH2REPO = "research2repo"
    QUANT2REPO = "quant2repo"


class CloudBackend(str, Enum):
    """Cloud / execution platform where the engine runs."""
    GCP_VERTEX = "gcp_vertex"
    AWS_BEDROCK = "aws_bedrock"
    AZURE_ML = "azure_ml"
    ON_PREM = "on_prem"
    LOCAL = "local"


class JobStatus(str, Enum):
    """Lifecycle states for an async job."""
    PENDING = "pending"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    COMPLETED = "completed"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DeliveryMethod(str, Enum):
    """How artifacts are delivered to the tenant."""
    PRESIGNED_URL = "presigned_url"
    BYOC_TUNNEL = "byoc_tunnel"
    DIRECT_DOWNLOAD = "direct_download"


# ── Engine Protocol ──────────────────────────────────────────────────────


class EngineCapability(str, Enum):
    """Capabilities an engine can declare in its manifest."""
    PDF_INPUT = "pdf_input"
    TEXT_INPUT = "text_input"
    CATALOG_INPUT = "catalog_input"
    GITHUB_OUTPUT = "github_output"
    LOCAL_OUTPUT = "local_output"
    STREAMING_LOGS = "streaming_logs"
    INCREMENTAL_VALIDATION = "incremental_validation"


class EngineManifest(BaseModel):
    """Standardised engine plugin descriptor.

    Every engine that plugs into the gateway publishes a manifest (JSON
    or YAML) describing its identity, capabilities, and runtime
    requirements.  The gateway's :mod:`app.engine_manifest` loader reads
    these at startup and registers them in the engine registry.

    See ``docs/engine_protocol.md`` for the full specification.
    """
    engine_id: str = Field(..., description="Unique engine identifier (e.g. 'research2repo')")
    version: str = Field("0.1.0", description="SemVer version of the engine")
    display_name: str = Field("", description="Human-friendly name")
    description: str = Field("", description="Short description of what the engine does")

    # Capabilities
    capabilities: list[EngineCapability] = Field(default_factory=list)
    accepted_inputs: list[str] = Field(
        default_factory=lambda: ["pdf_url", "pdf_base64", "paper_text"],
        description="Input field names the engine accepts",
    )

    # Runtime
    container_image: str = Field("", description="OCI image URI for the engine")
    entrypoint: list[str] = Field(
        default_factory=list,
        description="Override entrypoint for container / subprocess",
    )
    env_defaults: dict[str, str] = Field(
        default_factory=dict,
        description="Default environment variables injected into the engine",
    )
    supported_backends: list[CloudBackend] = Field(
        default_factory=lambda: [CloudBackend.GCP_VERTEX, CloudBackend.AWS_BEDROCK],
        description="Cloud backends this engine can run on",
    )
    health_endpoint: str = Field(
        "/health",
        description="HTTP path for on-prem liveness probe",
    )
    protocol_version: str = Field(
        "1.0",
        description="Version of the Any2Repo Engine Protocol this manifest conforms to",
    )

    # Resource hints
    cpu_request: str = Field("2", description="CPU cores requested")
    memory_request: str = Field("8Gi", description="Memory requested")
    gpu_required: bool = Field(False, description="Whether the engine needs a GPU")
    timeout_seconds: int = Field(3600, description="Max execution time")


# ── Tenant ───────────────────────────────────────────────────────────────


class Tenant(BaseModel):
    """A registered tenant (customer) with BYOC settings."""
    tenant_id: str
    name: str = ""
    cloud_backend: CloudBackend = CloudBackend.GCP_VERTEX
    # Per-tenant overrides
    gcp_project_id: Optional[str] = None
    aws_role_arn: Optional[str] = None
    azure_subscription_id: Optional[str] = None
    azure_resource_group: Optional[str] = None
    azure_workspace_name: Optional[str] = None
    on_prem_endpoint: Optional[str] = None
    allowed_engines: list[EngineType] = Field(
        default_factory=lambda: [EngineType.RESEARCH2REPO, EngineType.QUANT2REPO]
    )
    # Also allow arbitrary engine IDs for plugin engines
    allowed_engine_ids: list[str] = Field(
        default_factory=list,
        description="Additional engine IDs beyond the built-in EngineType enum",
    )
    max_concurrent_jobs: int = 5
    active: bool = True
    # BYOC tunnel
    tunnel_url: Optional[str] = Field(
        None,
        description="Active Cloudflare Tunnel endpoint (e.g. https://<id>.cfargotunnel.com)",
    )
    tunnel_registered_at: Optional[datetime] = None
    delivery_method: DeliveryMethod = DeliveryMethod.PRESIGNED_URL


# ── Job Request / Response ───────────────────────────────────────────────


class JobRequest(BaseModel):
    """Incoming request to run a paper-to-repo conversion."""
    engine: EngineType = EngineType.RESEARCH2REPO
    # For plugin engines not in the EngineType enum
    engine_id: Optional[str] = None
    cloud_backend: Optional[CloudBackend] = None  # override tenant default

    # Paper source (exactly one must be provided)
    pdf_url: Optional[str] = None
    pdf_base64: Optional[str] = None
    paper_text: Optional[str] = None

    # Engine-specific options
    output_dir: str = ""
    options: dict = Field(default_factory=dict)

    # Quant2Repo specific
    catalog_id: Optional[str] = None

    @property
    def effective_engine_id(self) -> str:
        """Return the engine_id to use (explicit or derived from enum)."""
        return self.engine_id or self.engine.value


class JobResponse(BaseModel):
    """Acknowledgement returned immediately after job submission."""
    job_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    tenant_id: str = ""
    engine: EngineType = EngineType.RESEARCH2REPO
    engine_id: str = ""
    cloud_backend: CloudBackend = CloudBackend.GCP_VERTEX
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    message: str = "Job submitted successfully"


class JobStatusResponse(BaseModel):
    """Full status report for a running or completed job."""
    job_id: str
    tenant_id: str = ""
    engine: EngineType = EngineType.RESEARCH2REPO
    engine_id: str = ""
    cloud_backend: CloudBackend = CloudBackend.GCP_VERTEX
    status: JobStatus = JobStatus.PENDING
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    dispatched_at: Optional[datetime] = None
    elapsed_seconds: Optional[float] = None
    output_url: Optional[str] = None
    artifact_url: Optional[str] = Field(
        None,
        description="Pre-signed URL to the zipped output artifact (time-limited)",
    )
    artifact_size_bytes: Optional[int] = None
    error: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


# ── Webhook Payload (Engine → Gateway) ───────────────────────────────────


class EngineWebhookPayload(BaseModel):
    """Payload POSTed by the engine to /api/v1/webhooks/engine-complete.

    This is the body the engine sends when it finishes (success or failure).
    It carries only lightweight metadata + a pre-signed URL to the heavy
    artifact — the gateway never buffers the full repo zip in memory.
    """
    job_id: str = Field(..., description="Job ID that was passed to the engine at dispatch time")
    status: str = Field(..., description="'completed' or 'failed'")
    engine_id: str = Field("", description="Engine that processed the job")
    artifact_url: Optional[str] = Field(
        None,
        description=(
            "Pre-signed GCS/S3 URL pointing to the zipped output repo. "
            "Valid for ~15 minutes. Required when status='completed'."
        ),
    )
    artifact_size_bytes: Optional[int] = Field(
        None, description="Size of the zip artifact in bytes",
    )
    files_generated: int = Field(0, description="Number of files in the output repo")
    elapsed_seconds: float = Field(0.0, description="Wall-clock pipeline execution time")
    error: Optional[str] = Field(None, description="Error message (required when status='failed')")
    output_url: Optional[str] = Field(None, description="Optional GitHub / external repo URL")
    metadata: dict = Field(default_factory=dict, description="Engine-specific metadata")


class WebhookAck(BaseModel):
    """Response returned by the gateway after processing a webhook."""
    job_id: str
    status: JobStatus
    delivery_initiated: bool = False
    message: str = ""


# ── BYOC Tunnel Registration ────────────────────────────────────────────


class TunnelRegistrationRequest(BaseModel):
    """Request body for POST /api/v1/tenants/{tenant_id}/register-tunnel.

    Sent by the cloudflared initContainer in the customer's K8s cluster
    once the Cloudflare Tunnel is established.
    """
    tunnel_url: str = Field(
        ...,
        description="The Cloudflare Tunnel endpoint URL (e.g. https://<id>.cfargotunnel.com)",
    )


class TunnelRegistrationResponse(BaseModel):
    """Confirmation returned after tunnel registration."""
    tenant_id: str
    tunnel_url: str
    registered_at: datetime
    message: str = "Tunnel registered successfully"


# ── Engine Config ────────────────────────────────────────────────────────


class EngineConfig(BaseModel):
    """Runtime configuration for an execution engine."""
    engine: EngineType
    engine_id: str = ""
    cloud_backend: CloudBackend
    # GCP
    gcp_project_id: str = ""
    gcp_region: str = "us-central1"
    vertex_endpoint: str = ""
    # AWS
    aws_region: str = "us-east-1"
    aws_role_arn: str = ""
    bedrock_model_id: str = ""
    # Azure
    azure_subscription_id: str = ""
    azure_resource_group: str = ""
    azure_workspace_name: str = ""
    azure_region: str = "eastus"
    # On-prem
    on_prem_endpoint: str = ""
    on_prem_docker_network: str = "any2repo"
    # Plugin manifest (populated for dynamic engines)
    manifest: Optional[EngineManifest] = None
    # General
    timeout_seconds: int = 3600
    max_retries: int = 2
