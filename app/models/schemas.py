"""Pydantic models for tenants, jobs, and engine configuration."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────────


class EngineType(str, Enum):
    """Supported execution engines."""
    RESEARCH2REPO = "research2repo"
    QUANT2REPO = "quant2repo"


class CloudBackend(str, Enum):
    """Cloud platform where the engine runs."""
    GCP_VERTEX = "gcp_vertex"
    AWS_BEDROCK = "aws_bedrock"
    LOCAL = "local"


class JobStatus(str, Enum):
    """Lifecycle states for an async job."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ── Tenant ───────────────────────────────────────────────────────────────


class Tenant(BaseModel):
    """A registered tenant (customer) with BYOC settings."""
    tenant_id: str
    name: str = ""
    cloud_backend: CloudBackend = CloudBackend.GCP_VERTEX
    # Per-tenant overrides
    gcp_project_id: Optional[str] = None
    aws_role_arn: Optional[str] = None
    allowed_engines: list[EngineType] = Field(
        default_factory=lambda: [EngineType.RESEARCH2REPO, EngineType.QUANT2REPO]
    )
    max_concurrent_jobs: int = 5
    active: bool = True


# ── Job Request / Response ───────────────────────────────────────────────


class JobRequest(BaseModel):
    """Incoming request to run a paper-to-repo conversion."""
    engine: EngineType = EngineType.RESEARCH2REPO
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


class JobResponse(BaseModel):
    """Acknowledgement returned immediately after job submission."""
    job_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    tenant_id: str = ""
    engine: EngineType = EngineType.RESEARCH2REPO
    cloud_backend: CloudBackend = CloudBackend.GCP_VERTEX
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    message: str = "Job submitted successfully"


class JobStatusResponse(BaseModel):
    """Full status report for a running or completed job."""
    job_id: str
    tenant_id: str = ""
    engine: EngineType = EngineType.RESEARCH2REPO
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
    cloud_backend: CloudBackend
    # GCP
    gcp_project_id: str = ""
    gcp_region: str = "us-central1"
    vertex_endpoint: str = ""
    # AWS
    aws_region: str = "us-east-1"
    aws_role_arn: str = ""
    bedrock_model_id: str = ""
    # General
    timeout_seconds: int = 3600
    max_retries: int = 2
