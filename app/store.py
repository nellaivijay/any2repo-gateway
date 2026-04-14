"""Persistent state store for jobs and tenants.

Provides a pluggable backend abstraction so the gateway can run with:
  - **memory** (default, for dev/test) — plain Python dicts
  - **firestore** — Google Cloud Firestore (production on GCP)
  - **dynamodb** — AWS DynamoDB (production on AWS)

The active backend is selected via ``STORE_BACKEND`` env var.
All backends implement the same interface so the rest of the gateway
is storage-agnostic.
"""

from __future__ import annotations

import abc
import logging
from datetime import datetime, timezone
from typing import Optional

from app.models.schemas import (
    CloudBackend,
    DeliveryMethod,
    EngineType,
    JobResponse,
    JobStatus,
    JobStatusResponse,
    Tenant,
)

logger = logging.getLogger(__name__)


# ── Abstract interface ───────────────────────────────────────────────────


class BaseStore(abc.ABC):
    """Storage backend interface for jobs and tenants."""

    # ── Jobs ─────────────────────────────────────────────────────────

    @abc.abstractmethod
    def store_job(self, job: JobResponse) -> None:
        """Persist a new job record."""

    @abc.abstractmethod
    def get_job(self, job_id: str) -> Optional[JobStatusResponse]:
        """Look up a job by ID."""

    @abc.abstractmethod
    def update_job(self, job_id: str, **fields) -> Optional[JobStatusResponse]:
        """Update fields on an existing job record."""

    @abc.abstractmethod
    def list_jobs(self, tenant_id: str) -> list[JobStatusResponse]:
        """List all jobs for a tenant, most recent first."""

    # ── Tenants ──────────────────────────────────────────────────────

    @abc.abstractmethod
    def store_tenant(self, tenant: Tenant) -> None:
        """Persist a tenant record."""

    @abc.abstractmethod
    def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """Look up a tenant by ID."""

    @abc.abstractmethod
    def update_tenant(self, tenant_id: str, **fields) -> Optional[Tenant]:
        """Update fields on an existing tenant."""

    @abc.abstractmethod
    def list_tenants(self) -> list[Tenant]:
        """Return all registered tenants."""

    @abc.abstractmethod
    def delete_tenant(self, tenant_id: str) -> bool:
        """Remove a tenant.  Returns True if it existed."""


# ── In-memory backend (dev / test) ───────────────────────────────────────


class MemoryStore(BaseStore):
    """Dict-backed store.  Data lives only for the process lifetime."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobStatusResponse] = {}
        self._tenants: dict[str, Tenant] = {}

    # ── Jobs ─────────────────────────────────────────────────────────

    def store_job(self, job: JobResponse) -> None:
        self._jobs[job.job_id] = JobStatusResponse(
            job_id=job.job_id,
            tenant_id=job.tenant_id,
            engine=job.engine,
            engine_id=job.engine_id,
            cloud_backend=job.cloud_backend,
            status=job.status,
            created_at=job.created_at,
            started_at=(
                datetime.now(timezone.utc)
                if job.status == JobStatus.RUNNING
                else None
            ),
        )

    def get_job(self, job_id: str) -> Optional[JobStatusResponse]:
        return self._jobs.get(job_id)

    def update_job(self, job_id: str, **fields) -> Optional[JobStatusResponse]:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        for key, value in fields.items():
            if hasattr(job, key):
                setattr(job, key, value)
        return job

    def list_jobs(self, tenant_id: str) -> list[JobStatusResponse]:
        jobs = [j for j in self._jobs.values() if j.tenant_id == tenant_id]
        jobs.sort(
            key=lambda j: j.created_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return jobs

    # ── Tenants ──────────────────────────────────────────────────────

    def store_tenant(self, tenant: Tenant) -> None:
        self._tenants[tenant.tenant_id] = tenant

    def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        return self._tenants.get(tenant_id)

    def update_tenant(self, tenant_id: str, **fields) -> Optional[Tenant]:
        tenant = self._tenants.get(tenant_id)
        if tenant is None:
            return None
        for key, value in fields.items():
            if hasattr(tenant, key):
                setattr(tenant, key, value)
        return tenant

    def list_tenants(self) -> list[Tenant]:
        return list(self._tenants.values())

    def delete_tenant(self, tenant_id: str) -> bool:
        return self._tenants.pop(tenant_id, None) is not None


# ── Firestore backend (GCP production) ───────────────────────────────────


class FirestoreStore(BaseStore):
    """Google Cloud Firestore backend.

    Collections:
      - ``jobs``    — keyed by job_id
      - ``tenants`` — keyed by tenant_id
    """

    def __init__(self, project_id: str = "") -> None:
        from google.cloud import firestore  # lazy import

        self._db = firestore.Client(project=project_id or None)
        self._jobs_col = self._db.collection("jobs")
        self._tenants_col = self._db.collection("tenants")
        logger.info("FirestoreStore initialised (project=%s)", project_id or "default")

    # ── Jobs ─────────────────────────────────────────────────────────

    def store_job(self, job: JobResponse) -> None:
        doc = {
            "job_id": job.job_id,
            "tenant_id": job.tenant_id,
            "engine": job.engine.value if hasattr(job.engine, "value") else str(job.engine),
            "engine_id": job.engine_id,
            "cloud_backend": job.cloud_backend.value if hasattr(job.cloud_backend, "value") else str(job.cloud_backend),
            "status": job.status.value if hasattr(job.status, "value") else str(job.status),
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "started_at": (
                datetime.now(timezone.utc).isoformat()
                if job.status == JobStatus.RUNNING
                else None
            ),
        }
        self._jobs_col.document(job.job_id).set(doc)

    def get_job(self, job_id: str) -> Optional[JobStatusResponse]:
        doc = self._jobs_col.document(job_id).get()
        if not doc.exists:
            return None
        return self._doc_to_job(doc.to_dict())

    def update_job(self, job_id: str, **fields) -> Optional[JobStatusResponse]:
        ref = self._jobs_col.document(job_id)
        doc = ref.get()
        if not doc.exists:
            return None
        # Serialize enum values
        updates = {}
        for k, v in fields.items():
            if hasattr(v, "value"):
                updates[k] = v.value
            elif isinstance(v, datetime):
                updates[k] = v.isoformat()
            else:
                updates[k] = v
        ref.update(updates)
        return self.get_job(job_id)

    def list_jobs(self, tenant_id: str) -> list[JobStatusResponse]:
        docs = (
            self._jobs_col
            .where("tenant_id", "==", tenant_id)
            .order_by("created_at", direction="DESCENDING")
            .stream()
        )
        return [self._doc_to_job(d.to_dict()) for d in docs]

    @staticmethod
    def _doc_to_job(data: dict) -> JobStatusResponse:
        """Convert a Firestore document dict to JobStatusResponse."""
        return JobStatusResponse(
            job_id=data.get("job_id", ""),
            tenant_id=data.get("tenant_id", ""),
            engine=data.get("engine", "research2repo"),
            engine_id=data.get("engine_id", ""),
            cloud_backend=data.get("cloud_backend", "gcp_vertex"),
            status=data.get("status", "pending"),
            created_at=data.get("created_at"),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            dispatched_at=data.get("dispatched_at"),
            elapsed_seconds=data.get("elapsed_seconds"),
            output_url=data.get("output_url"),
            artifact_url=data.get("artifact_url"),
            artifact_size_bytes=data.get("artifact_size_bytes"),
            error=data.get("error"),
            metadata=data.get("metadata", {}),
        )

    # ── Tenants ──────────────────────────────────────────────────────

    def store_tenant(self, tenant: Tenant) -> None:
        self._tenants_col.document(tenant.tenant_id).set(
            tenant.model_dump(mode="json")
        )

    def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        doc = self._tenants_col.document(tenant_id).get()
        if not doc.exists:
            return None
        return Tenant(**doc.to_dict())

    def update_tenant(self, tenant_id: str, **fields) -> Optional[Tenant]:
        ref = self._tenants_col.document(tenant_id)
        doc = ref.get()
        if not doc.exists:
            return None
        updates = {}
        for k, v in fields.items():
            if hasattr(v, "value"):
                updates[k] = v.value
            elif isinstance(v, datetime):
                updates[k] = v.isoformat()
            else:
                updates[k] = v
        ref.update(updates)
        return self.get_tenant(tenant_id)

    def list_tenants(self) -> list[Tenant]:
        return [Tenant(**d.to_dict()) for d in self._tenants_col.stream()]

    def delete_tenant(self, tenant_id: str) -> bool:
        ref = self._tenants_col.document(tenant_id)
        if not ref.get().exists:
            return False
        ref.delete()
        return True


# ── Factory ──────────────────────────────────────────────────────────────


def create_store(backend: str = "memory", **kwargs) -> BaseStore:
    """Instantiate the configured store backend.

    Args:
        backend: One of ``"memory"``, ``"firestore"``, ``"dynamodb"``.
        **kwargs: Backend-specific options (e.g. ``project_id`` for Firestore).
    """
    if backend == "memory":
        return MemoryStore()
    if backend == "firestore":
        return FirestoreStore(project_id=kwargs.get("project_id", ""))
    raise ValueError(f"Unknown store backend: {backend!r}")
