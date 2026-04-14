"""Tests for webhook ingestion and BYOC tunnel registration endpoints."""

from __future__ import annotations

import hashlib
import hmac
import json

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
        tenant_id="test-tenant",
        name="Test Tenant",
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


HEADERS = {"X-API-Key": "", "X-Tenant-ID": "test-tenant"}


def _seed_job(job_id: str = "test-job-001", tenant_id: str = "test-tenant") -> str:
    """Insert a pending job into the store and return its job_id."""
    store = get_store()
    job = JobResponse(
        job_id=job_id,
        tenant_id=tenant_id,
        engine=EngineType.RESEARCH2REPO,
        engine_id="research2repo",
        cloud_backend=CloudBackend.GCP_VERTEX,
        status=JobStatus.RUNNING,
    )
    store.store_job(job)
    return job_id


# ── Webhook: engine-complete ─────────────────────────────────────────────


class TestWebhookEngineComplete:
    """POST /api/v1/webhooks/engine-complete"""

    def test_completed_webhook(self, client):
        """A completed webhook should update the job to COMPLETED."""
        job_id = _seed_job()
        payload = {
            "job_id": job_id,
            "status": "completed",
            "engine_id": "research2repo",
            "artifact_url": "https://storage.googleapis.com/bucket/output.zip",
            "artifact_size_bytes": 1024000,
            "files_generated": 42,
            "elapsed_seconds": 120.5,
        }
        resp = client.post("/api/v1/webhooks/engine-complete", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == job_id
        assert data["status"] == "completed"

        # Verify job was updated in store
        store = get_store()
        job = store.get_job(job_id)
        assert job.status == JobStatus.COMPLETED
        assert job.artifact_url == "https://storage.googleapis.com/bucket/output.zip"
        assert job.artifact_size_bytes == 1024000
        assert job.elapsed_seconds == 120.5
        assert job.completed_at is not None

    def test_failed_webhook(self, client):
        """A failed webhook should update the job to FAILED."""
        job_id = _seed_job()
        payload = {
            "job_id": job_id,
            "status": "failed",
            "engine_id": "research2repo",
            "error": "Pipeline stage 3 (code generation) failed: timeout",
            "elapsed_seconds": 45.0,
        }
        resp = client.post("/api/v1/webhooks/engine-complete", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"

        store = get_store()
        job = store.get_job(job_id)
        assert job.status == JobStatus.FAILED
        assert "timeout" in job.error

    def test_webhook_unknown_job(self, client):
        """Webhook for a non-existent job should 404."""
        payload = {
            "job_id": "nonexistent-job",
            "status": "completed",
        }
        resp = client.post("/api/v1/webhooks/engine-complete", json=payload)
        assert resp.status_code == 404

    def test_webhook_metadata_merged(self, client):
        """Engine metadata should be merged into the job's metadata."""
        job_id = _seed_job()
        payload = {
            "job_id": job_id,
            "status": "completed",
            "engine_id": "research2repo",
            "artifact_url": "https://storage.googleapis.com/bucket/output.zip",
            "files_generated": 10,
            "elapsed_seconds": 60.0,
            "metadata": {"model": "gpt-4", "tokens_used": 50000},
        }
        resp = client.post("/api/v1/webhooks/engine-complete", json=payload)
        assert resp.status_code == 200

        store = get_store()
        job = store.get_job(job_id)
        assert job.metadata["model"] == "gpt-4"
        assert job.metadata["files_generated"] == 10

    def test_webhook_bypasses_tenant_auth(self, client):
        """Webhook endpoint should NOT require X-API-Key / X-Tenant-ID."""
        job_id = _seed_job()
        payload = {
            "job_id": job_id,
            "status": "completed",
            "artifact_url": "https://storage.googleapis.com/bucket/output.zip",
        }
        # No auth headers — should still succeed
        resp = client.post("/api/v1/webhooks/engine-complete", json=payload)
        assert resp.status_code == 200

    def test_webhook_with_hmac_signature(self, client, monkeypatch):
        """When WEBHOOK_SECRET is set, a valid HMAC signature is required."""
        secret = "test-webhook-secret-123"
        monkeypatch.setattr("app.routers.webhooks.settings.webhook_secret", secret)

        job_id = _seed_job()
        payload = {
            "job_id": job_id,
            "status": "completed",
            "artifact_url": "https://storage.googleapis.com/bucket/output.zip",
        }
        body = json.dumps(payload).encode()
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        resp = client.post(
            "/api/v1/webhooks/engine-complete",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": sig,
            },
        )
        assert resp.status_code == 200

    def test_webhook_rejects_bad_signature(self, client, monkeypatch):
        """An invalid HMAC signature should be rejected."""
        monkeypatch.setattr(
            "app.routers.webhooks.settings.webhook_secret", "real-secret"
        )

        job_id = _seed_job()
        payload = {"job_id": job_id, "status": "completed"}
        resp = client.post(
            "/api/v1/webhooks/engine-complete",
            json=payload,
            headers={"X-Webhook-Signature": "bad-signature"},
        )
        assert resp.status_code == 401

    def test_webhook_missing_signature_when_required(self, client, monkeypatch):
        """Missing signature header should be rejected when secret is set."""
        monkeypatch.setattr(
            "app.routers.webhooks.settings.webhook_secret", "real-secret"
        )
        job_id = _seed_job()
        payload = {"job_id": job_id, "status": "completed"}
        resp = client.post("/api/v1/webhooks/engine-complete", json=payload)
        assert resp.status_code == 401


# ── Tunnel Registration ──────────────────────────────────────────────────


class TestTunnelRegistration:
    """POST /api/v1/tenants/{tenant_id}/register-tunnel"""

    def test_register_tunnel(self, client):
        """Should register a tunnel URL for an existing tenant."""
        body = {"tunnel_url": "https://abc123.cfargotunnel.com"}
        resp = client.post(
            "/api/v1/tenants/test-tenant/register-tunnel",
            json=body,
            headers=HEADERS,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["tenant_id"] == "test-tenant"
        assert data["tunnel_url"] == "https://abc123.cfargotunnel.com"
        assert "registered_at" in data

        # Verify tenant was updated in store
        store = get_store()
        tenant = store.get_tenant("test-tenant")
        assert tenant.tunnel_url == "https://abc123.cfargotunnel.com"
        assert tenant.tunnel_registered_at is not None

    def test_register_tunnel_unknown_tenant(self, client):
        """Should 404 for a non-existent tenant."""
        body = {"tunnel_url": "https://abc123.cfargotunnel.com"}
        resp = client.post(
            "/api/v1/tenants/nonexistent/register-tunnel",
            json=body,
            headers=HEADERS,
        )
        assert resp.status_code == 404

    def test_register_tunnel_overwrites_previous(self, client):
        """Registering again should overwrite the previous tunnel URL."""
        body1 = {"tunnel_url": "https://old-tunnel.cfargotunnel.com"}
        client.post(
            "/api/v1/tenants/test-tenant/register-tunnel",
            json=body1,
            headers=HEADERS,
        )

        body2 = {"tunnel_url": "https://new-tunnel.cfargotunnel.com"}
        resp = client.post(
            "/api/v1/tenants/test-tenant/register-tunnel",
            json=body2,
            headers=HEADERS,
        )
        assert resp.status_code == 201

        store = get_store()
        tenant = store.get_tenant("test-tenant")
        assert tenant.tunnel_url == "https://new-tunnel.cfargotunnel.com"


# ── Store Unit Tests ─────────────────────────────────────────────────────


class TestMemoryStore:
    """Direct unit tests for the MemoryStore."""

    def test_store_and_get_job(self):
        store = get_store()
        job = JobResponse(
            job_id="store-test-001",
            tenant_id="test-tenant",
            engine=EngineType.RESEARCH2REPO,
            cloud_backend=CloudBackend.GCP_VERTEX,
            status=JobStatus.PENDING,
        )
        store.store_job(job)
        retrieved = store.get_job("store-test-001")
        assert retrieved is not None
        assert retrieved.job_id == "store-test-001"
        assert retrieved.status == JobStatus.PENDING

    def test_update_job(self):
        store = get_store()
        job = JobResponse(
            job_id="store-test-002",
            tenant_id="test-tenant",
        )
        store.store_job(job)
        store.update_job("store-test-002", status=JobStatus.RUNNING)
        updated = store.get_job("store-test-002")
        assert updated.status == JobStatus.RUNNING

    def test_list_jobs_by_tenant(self):
        store = get_store()
        for i in range(3):
            store.store_job(JobResponse(
                job_id=f"list-test-{i}",
                tenant_id="test-tenant",
            ))
        store.store_job(JobResponse(
            job_id="other-tenant-job",
            tenant_id="other-tenant",
        ))
        jobs = store.list_jobs("test-tenant")
        assert len(jobs) == 3
        assert all(j.tenant_id == "test-tenant" for j in jobs)

    def test_store_and_get_tenant(self):
        store = get_store()
        store.store_tenant(Tenant(
            tenant_id="new-t",
            name="New Tenant",
        ))
        t = store.get_tenant("new-t")
        assert t is not None
        assert t.name == "New Tenant"

    def test_delete_tenant(self):
        store = get_store()
        store.store_tenant(Tenant(tenant_id="del-me"))
        assert store.delete_tenant("del-me") is True
        assert store.get_tenant("del-me") is None
        assert store.delete_tenant("del-me") is False

    def test_update_tenant(self):
        store = get_store()
        store.update_tenant("test-tenant", name="Updated Name")
        t = store.get_tenant("test-tenant")
        assert t.name == "Updated Name"

    def test_get_nonexistent_job(self):
        store = get_store()
        assert store.get_job("nonexistent") is None

    def test_update_nonexistent_job(self):
        store = get_store()
        assert store.update_job("nonexistent", status=JobStatus.RUNNING) is None
