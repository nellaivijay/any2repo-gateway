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
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


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
    elapsed_seconds: Optional[float] = None
    output_url: Optional[str] = None
    error: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


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
