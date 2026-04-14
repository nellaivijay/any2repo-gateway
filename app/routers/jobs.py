"""Job submission and status API routes.

All routes require ``X-API-Key`` and ``X-Tenant-ID`` headers (enforced
by :class:`TenantAuthMiddleware`).
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from app.models.schemas import (
    CloudBackend,
    EngineType,
    JobRequest,
    JobResponse,
    JobStatus,
    JobStatusResponse,
    Tenant,
)
from app.engine_registry import get_backend

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def _get_store():
    """Deferred import to avoid circular dependency."""
    from app.main import get_store
    return get_store()


def _resolve_backend(tenant: Tenant, request: JobRequest) -> CloudBackend:
    """Determine which cloud backend to use for this request."""
    if request.cloud_backend is not None:
        return request.cloud_backend
    return tenant.cloud_backend


# ── Submit ───────────────────────────────────────────────────────────────

@router.post("", response_model=JobResponse, status_code=202)
async def submit_job(req: JobRequest, request: Request) -> JobResponse:
    """Submit a new paper-to-repo conversion job.

    The job runs asynchronously on the tenant's configured cloud backend.
    Returns a ``202 Accepted`` with a ``job_id`` for polling.
    """
    tenant: Tenant = request.state.tenant

    # Validate engine access
    if req.engine not in tenant.allowed_engines:
        raise HTTPException(
            status_code=403,
            detail=f"Engine '{req.engine.value}' not allowed for tenant '{tenant.tenant_id}'",
        )

    # Validate input
    if not req.pdf_url and not req.pdf_base64 and not req.paper_text and not req.catalog_id:
        raise HTTPException(
            status_code=422,
            detail="Provide at least one of: pdf_url, pdf_base64, paper_text, catalog_id",
        )

    # Check concurrency limit
    store = _get_store()
    active_jobs = [
        j for j in store.list_jobs(tenant.tenant_id)
        if j.status in (JobStatus.PENDING, JobStatus.RUNNING)
    ]
    if len(active_jobs) >= tenant.max_concurrent_jobs:
        raise HTTPException(
            status_code=429,
            detail=f"Concurrent job limit ({tenant.max_concurrent_jobs}) reached",
        )

    # Resolve backend and submit
    backend_type = _resolve_backend(tenant, req)
    backend = get_backend(req.engine, backend_type, tenant)

    payload = {
        "engine": req.engine.value,
        "pdf_url": req.pdf_url or "",
        "pdf_base64": req.pdf_base64 or "",
        "paper_text": req.paper_text or "",
        "catalog_id": req.catalog_id or "",
        "output_dir": req.output_dir,
        "options": req.options,
    }

    job_resp = await backend.submit_job(
        job_id=JobResponse().job_id,  # generate a new UUID
        tenant_id=tenant.tenant_id,
        payload=payload,
    )

    store.store_job(job_resp)

    logger.info(
        "Job %s submitted: engine=%s backend=%s tenant=%s",
        job_resp.job_id, req.engine.value, backend_type.value, tenant.tenant_id,
    )
    return job_resp


# ── Status ───────────────────────────────────────────────────────────────

@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str, request: Request) -> JobStatusResponse:
    """Get the current status of a job."""
    tenant: Tenant = request.state.tenant
    store = _get_store()
    job = store.get_job(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    if job.tenant_id != tenant.tenant_id:
        raise HTTPException(status_code=403, detail="Access denied to this job")

    # Optionally refresh from the cloud backend
    if job.status in (JobStatus.PENDING, JobStatus.RUNNING):
        backend = get_backend(job.engine, job.cloud_backend, tenant)
        cloud_status = await backend.get_job_status(job_id)
        if cloud_status.status != job.status:
            store.update_job(job_id, status=cloud_status.status,
                             output_url=cloud_status.output_url,
                             error=cloud_status.error,
                             metadata=cloud_status.metadata)
            job = store.get_job(job_id)

    return job


# ── List ─────────────────────────────────────────────────────────────────

@router.get("", response_model=list[JobStatusResponse])
async def list_tenant_jobs(
    request: Request,
    status: Optional[JobStatus] = None,
    engine: Optional[EngineType] = None,
    limit: int = 50,
) -> list[JobStatusResponse]:
    """List all jobs for the authenticated tenant."""
    tenant: Tenant = request.state.tenant
    store = _get_store()
    jobs = store.list_jobs(tenant.tenant_id)

    if status:
        jobs = [j for j in jobs if j.status == status]
    if engine:
        jobs = [j for j in jobs if j.engine == engine]

    return jobs[:limit]


# ── Cancel ───────────────────────────────────────────────────────────────

@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request) -> dict:
    """Cancel a running job."""
    tenant: Tenant = request.state.tenant
    store = _get_store()
    job = store.get_job(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    if job.tenant_id != tenant.tenant_id:
        raise HTTPException(status_code=403, detail="Access denied to this job")
    if job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
        raise HTTPException(status_code=409, detail=f"Job is already {job.status.value}")

    backend = get_backend(job.engine, job.cloud_backend, tenant)
    cancelled = await backend.cancel_job(job_id)

    if cancelled:
        store.update_job(job_id, status=JobStatus.CANCELLED)
        return {"job_id": job_id, "status": "cancelled"}
    else:
        return {"job_id": job_id, "status": "cancel_requested",
                "message": "Cancellation is best-effort for this backend"}
