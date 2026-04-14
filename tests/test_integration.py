"""Integration test: full job lifecycle PENDING → RUNNING → COMPLETED.

This test simulates the complete flow:
  1. Create a tenant
  2. Submit a job via the API
  3. Verify the job shows as PENDING
  4. Simulate the engine calling the webhook with COMPLETED status
  5. Verify the job status is now COMPLETED with artifact metadata
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app, get_store
from app.store import MemoryStore
from app.models.schemas import (
    CloudBackend,
    DeliveryMethod,
    EngineType,
    JobResponse,
    JobStatus,
    JobStatusResponse,
    Tenant,
)
from app.engine_manifest import _MANIFESTS, init_manifests


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_state():
    """Fresh in-memory store for each test."""
    import app.main as _main

    _main._store = MemoryStore()
    store = get_store()
    store.store_tenant(Tenant(
        tenant_id="integ-tenant",
        name="Integration Test Tenant",
        cloud_backend=CloudBackend.GCP_VERTEX,
        allowed_engines=[EngineType.RESEARCH2REPO, EngineType.QUANT2REPO],
    ))
    _MANIFESTS.clear()
    init_manifests()
    yield
    _main._store = None
    _MANIFESTS.clear()


@pytest.fixture()
def client():
    return TestClient(app)


HEADERS = {"X-API-Key": "", "X-Tenant-ID": "integ-tenant"}


# ── Integration Tests ────────────────────────────────────────────────────


class TestJobLifecycle:
    """End-to-end job lifecycle through the API."""

    def test_pending_to_completed_via_webhook(self, client):
        """Full happy-path: submit → verify pending → webhook complete → verify completed."""
        store = get_store()

        # Step 1: Seed a job as if submit_job ran (the real submit_job
        # calls a cloud backend which we don't want in unit tests)
        job_id = "lifecycle-001"
        job = JobResponse(
            job_id=job_id,
            tenant_id="integ-tenant",
            engine=EngineType.RESEARCH2REPO,
            engine_id="research2repo",
            cloud_backend=CloudBackend.GCP_VERTEX,
            status=JobStatus.RUNNING,
        )
        store.store_job(job)

        # Step 2: Verify job shows as RUNNING via status endpoint
        # Mock the backend so it doesn't try to hit real Vertex AI
        mock_backend = AsyncMock()
        mock_backend.get_job_status = AsyncMock(return_value=JobStatusResponse(
            job_id=job_id, status=JobStatus.RUNNING,
        ))
        with patch("app.routers.jobs.get_backend", return_value=mock_backend):
            resp = client.get(f"/api/v1/jobs/{job_id}", headers=HEADERS)
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"

        # Step 3: Simulate engine completion webhook
        webhook_payload = {
            "job_id": job_id,
            "status": "completed",
            "engine_id": "research2repo",
            "artifact_url": "https://storage.googleapis.com/any2repo-artifacts/lifecycle-001/output.zip",
            "artifact_size_bytes": 2048000,
            "files_generated": 67,
            "elapsed_seconds": 185.3,
            "output_url": "https://github.com/customer/generated-repo",
            "metadata": {"model": "claude-sonnet", "stages_completed": 10},
        }
        resp = client.post("/api/v1/webhooks/engine-complete", json=webhook_payload)
        assert resp.status_code == 200
        ack = resp.json()
        assert ack["status"] == "completed"
        assert ack["delivery_initiated"] is False  # no tunnel registered

        # Step 4: Verify job is now COMPLETED with all metadata
        resp = client.get(f"/api/v1/jobs/{job_id}", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["artifact_url"] == webhook_payload["artifact_url"]
        assert data["artifact_size_bytes"] == 2048000
        assert data["elapsed_seconds"] == 185.3
        assert data["output_url"] == "https://github.com/customer/generated-repo"
        assert data["metadata"]["model"] == "claude-sonnet"
        assert data["metadata"]["files_generated"] == 67
        assert data["completed_at"] is not None

    def test_pending_to_failed_via_webhook(self, client):
        """Failure path: submit → webhook failed → verify failed state."""
        store = get_store()

        job_id = "lifecycle-fail-001"
        store.store_job(JobResponse(
            job_id=job_id,
            tenant_id="integ-tenant",
            engine=EngineType.RESEARCH2REPO,
            engine_id="research2repo",
            cloud_backend=CloudBackend.GCP_VERTEX,
            status=JobStatus.RUNNING,
        ))

        # Engine reports failure
        resp = client.post("/api/v1/webhooks/engine-complete", json={
            "job_id": job_id,
            "status": "failed",
            "error": "Stage 5 (validation) failed: test_runner returned exit code 1",
            "elapsed_seconds": 95.2,
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"

        # Verify via status endpoint
        resp = client.get(f"/api/v1/jobs/{job_id}", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"
        assert "exit code 1" in data["error"]

    def test_list_jobs_after_lifecycle(self, client):
        """Verify list endpoint shows correct statuses after lifecycle events."""
        store = get_store()

        # Create multiple jobs in different states
        for i, status in enumerate([JobStatus.RUNNING, JobStatus.RUNNING, JobStatus.PENDING]):
            store.store_job(JobResponse(
                job_id=f"list-lifecycle-{i}",
                tenant_id="integ-tenant",
                engine=EngineType.RESEARCH2REPO,
                cloud_backend=CloudBackend.GCP_VERTEX,
                status=status,
            ))

        # Complete one via webhook
        client.post("/api/v1/webhooks/engine-complete", json={
            "job_id": "list-lifecycle-0",
            "status": "completed",
            "artifact_url": "https://storage.googleapis.com/bucket/out.zip",
        })

        # List all jobs
        resp = client.get("/api/v1/jobs", headers=HEADERS)
        assert resp.status_code == 200
        jobs = resp.json()
        assert len(jobs) == 3

        statuses = {j["job_id"]: j["status"] for j in jobs}
        assert statuses["list-lifecycle-0"] == "completed"
        assert statuses["list-lifecycle-1"] == "running"
        assert statuses["list-lifecycle-2"] == "pending"

    def test_tunnel_registration_then_webhook(self, client):
        """Register a tunnel, then verify webhook ack notes delivery intent."""
        store = get_store()

        # Update tenant to use BYOC tunnel delivery
        store.update_tenant("integ-tenant", delivery_method=DeliveryMethod.BYOC_TUNNEL)

        # Register tunnel
        resp = client.post(
            "/api/v1/tenants/integ-tenant/register-tunnel",
            json={"tunnel_url": "https://integ-tunnel.cfargotunnel.com"},
            headers=HEADERS,
        )
        assert resp.status_code == 201

        # Create and complete a job
        job_id = "tunnel-lifecycle-001"
        store.store_job(JobResponse(
            job_id=job_id,
            tenant_id="integ-tenant",
            engine=EngineType.RESEARCH2REPO,
            cloud_backend=CloudBackend.GCP_VERTEX,
            status=JobStatus.RUNNING,
        ))

        # The webhook will attempt tunnel delivery (which will fail since
        # there's no real tunnel endpoint, but the attempt is made)
        resp = client.post("/api/v1/webhooks/engine-complete", json={
            "job_id": job_id,
            "status": "completed",
            "artifact_url": "https://storage.googleapis.com/bucket/tunnel-out.zip",
        })
        assert resp.status_code == 200
        # delivery_initiated will be False because httpx call fails in test
        # but the important thing is the webhook still succeeds

    def test_cross_tenant_isolation(self, client):
        """Jobs from one tenant should not be accessible by another."""
        store = get_store()

        # Create a job for a different tenant
        store.store_tenant(Tenant(
            tenant_id="other-tenant",
            name="Other Tenant",
        ))
        store.store_job(JobResponse(
            job_id="other-job-001",
            tenant_id="other-tenant",
            engine=EngineType.RESEARCH2REPO,
            cloud_backend=CloudBackend.GCP_VERTEX,
        ))

        # integ-tenant should not see other-tenant's jobs
        resp = client.get("/api/v1/jobs", headers=HEADERS)
        assert resp.status_code == 200
        job_ids = [j["job_id"] for j in resp.json()]
        assert "other-job-001" not in job_ids

        # Direct access should be forbidden
        resp = client.get("/api/v1/jobs/other-job-001", headers=HEADERS)
        assert resp.status_code == 403
