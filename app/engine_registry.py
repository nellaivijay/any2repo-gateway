"""Engine registry — maps (engine, cloud_backend) pairs to backend instances.

Also provides the in-memory job store for tracking async job lifecycle.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.backends.base import BaseBackend
from app.backends.gcp_vertex import VertexAIBackend
from app.backends.aws_bedrock import AWSBedrockBackend
from app.config import settings
from app.models.schemas import (
    CloudBackend,
    EngineConfig,
    EngineType,
    JobResponse,
    JobStatus,
    JobStatusResponse,
    Tenant,
)

logger = logging.getLogger(__name__)


# ── Backend factory ──────────────────────────────────────────────────────

_BACKEND_MAP: dict[CloudBackend, type[BaseBackend]] = {
    CloudBackend.GCP_VERTEX: VertexAIBackend,
    CloudBackend.AWS_BEDROCK: AWSBedrockBackend,
}


def _build_engine_config(
    engine: EngineType,
    backend: CloudBackend,
    tenant: Optional[Tenant] = None,
) -> EngineConfig:
    """Build an EngineConfig from settings + optional tenant overrides."""
    return EngineConfig(
        engine=engine,
        cloud_backend=backend,
        gcp_project_id=(
            (tenant.gcp_project_id if tenant and tenant.gcp_project_id else "")
            or settings.gcp_project_id
        ),
        gcp_region=settings.gcp_region,
        aws_region=settings.aws_region,
        aws_role_arn=(
            (tenant.aws_role_arn if tenant and tenant.aws_role_arn else "")
            or settings.aws_role_arn
        ),
    )


def get_backend(
    engine: EngineType,
    backend: CloudBackend,
    tenant: Optional[Tenant] = None,
) -> BaseBackend:
    """Instantiate the appropriate backend for the given engine + cloud."""
    cls = _BACKEND_MAP.get(backend)
    if cls is None:
        raise ValueError(f"Unsupported cloud backend: {backend}")

    config = _build_engine_config(engine, backend, tenant)
    return cls(config)


# ── In-memory job store ──────────────────────────────────────────────────

_JOBS: dict[str, JobStatusResponse] = {}


def store_job(job: JobResponse) -> None:
    """Persist a new job record."""
    _JOBS[job.job_id] = JobStatusResponse(
        job_id=job.job_id,
        tenant_id=job.tenant_id,
        engine=job.engine,
        cloud_backend=job.cloud_backend,
        status=job.status,
        created_at=job.created_at,
        started_at=datetime.now(timezone.utc) if job.status == JobStatus.RUNNING else None,
    )


def get_job(job_id: str) -> Optional[JobStatusResponse]:
    """Look up a job by ID."""
    return _JOBS.get(job_id)


def update_job(job_id: str, **fields) -> Optional[JobStatusResponse]:
    """Update fields on an existing job record."""
    job = _JOBS.get(job_id)
    if job is None:
        return None
    for key, value in fields.items():
        if hasattr(job, key):
            setattr(job, key, value)
    return job


def list_jobs(tenant_id: str) -> list[JobStatusResponse]:
    """List all jobs for a tenant, most recent first."""
    jobs = [j for j in _JOBS.values() if j.tenant_id == tenant_id]
    jobs.sort(key=lambda j: j.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return jobs
