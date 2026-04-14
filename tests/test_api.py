"""Tests for the Any2Repo-Gateway API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app, get_store
from app.store import MemoryStore
from app.models.schemas import (
    Tenant, CloudBackend, EngineType, EngineCapability, EngineManifest,
)
from app.engine_manifest import (
    _MANIFESTS, register_manifest, get_manifest, init_manifests,
)
from tests.conformance import (
    validate_manifest, validate_status_file, validate_health_response,
    check_conformance,
)


@pytest.fixture(autouse=True)
def _clear_state():
    """Reset tenant and manifest stores between tests."""
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


# ── Health ───────────────────────────────────────────────────────────────

def test_root(client):
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert "engines" in data
    assert "research2repo" in data["engines"]
    assert "backends" in data
    assert "gcp_vertex" in data["backends"]
    assert "azure_ml" in data["backends"]
    assert "on_prem" in data["backends"]


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


# ── Auth ─────────────────────────────────────────────────────────────────

def test_missing_tenant_id(client):
    resp = client.get("/api/v1/jobs", headers={"X-API-Key": ""})
    assert resp.status_code == 400


def test_unknown_tenant(client):
    resp = client.get(
        "/api/v1/jobs",
        headers={"X-API-Key": "", "X-Tenant-ID": "nonexistent"},
    )
    assert resp.status_code == 404


# ── Tenants ──────────────────────────────────────────────────────────────

def test_create_and_get_tenant(client):
    tenant_data = {
        "tenant_id": "new-tenant",
        "name": "New Tenant",
        "cloud_backend": "gcp_vertex",
    }
    resp = client.post("/api/v1/tenants", json=tenant_data, headers=HEADERS)
    assert resp.status_code == 201

    resp = client.get("/api/v1/tenants/new-tenant", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Tenant"


def test_create_azure_tenant(client):
    tenant_data = {
        "tenant_id": "azure-tenant",
        "name": "Azure Tenant",
        "cloud_backend": "azure_ml",
        "azure_subscription_id": "sub-123",
        "azure_resource_group": "rg-test",
        "azure_workspace_name": "ws-test",
    }
    resp = client.post("/api/v1/tenants", json=tenant_data, headers=HEADERS)
    assert resp.status_code == 201
    data = resp.json()
    assert data["cloud_backend"] == "azure_ml"
    assert data["azure_subscription_id"] == "sub-123"


def test_create_onprem_tenant(client):
    tenant_data = {
        "tenant_id": "onprem-tenant",
        "name": "On-Prem Tenant",
        "cloud_backend": "on_prem",
        "on_prem_endpoint": "http://engine:8080",
    }
    resp = client.post("/api/v1/tenants", json=tenant_data, headers=HEADERS)
    assert resp.status_code == 201
    data = resp.json()
    assert data["cloud_backend"] == "on_prem"
    assert data["on_prem_endpoint"] == "http://engine:8080"


def test_duplicate_tenant(client):
    tenant_data = {"tenant_id": "test-tenant", "name": "Dup"}
    resp = client.post("/api/v1/tenants", json=tenant_data, headers=HEADERS)
    assert resp.status_code == 409


def test_list_tenants(client):
    resp = client.get("/api/v1/tenants", headers=HEADERS)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


# ── Jobs ─────────────────────────────────────────────────────────────────

def test_submit_missing_input(client):
    """Should reject a job with no paper source."""
    resp = client.post(
        "/api/v1/jobs",
        json={"engine": "research2repo"},
        headers=HEADERS,
    )
    assert resp.status_code == 422


def test_list_jobs_empty(client):
    resp = client.get("/api/v1/jobs", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_unknown_job(client):
    resp = client.get("/api/v1/jobs/nonexistent", headers=HEADERS)
    assert resp.status_code == 404


def test_cancel_unknown_job(client):
    resp = client.post("/api/v1/jobs/nonexistent/cancel", headers=HEADERS)
    assert resp.status_code == 404


# ── Engine Discovery ─────────────────────────────────────────────────────

def test_list_engines(client):
    resp = client.get("/api/v1/engines", headers=HEADERS)
    assert resp.status_code == 200
    engines = resp.json()
    engine_ids = [e["engine_id"] for e in engines]
    assert "research2repo" in engine_ids
    assert "quant2repo" in engine_ids


def test_get_engine(client):
    resp = client.get("/api/v1/engines/research2repo", headers=HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["engine_id"] == "research2repo"
    assert data["protocol_version"] == "1.0"
    assert "pdf_input" in data["capabilities"]


def test_get_engine_not_found(client):
    resp = client.get("/api/v1/engines/nonexistent", headers=HEADERS)
    assert resp.status_code == 404


def test_list_engines_filter_backend(client):
    resp = client.get("/api/v1/engines?backend=azure_ml", headers=HEADERS)
    assert resp.status_code == 200
    engines = resp.json()
    for e in engines:
        assert "azure_ml" in e["supported_backends"]


def test_list_backends(client):
    resp = client.get("/api/v1/engines/backends", headers=HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "gcp_vertex" in data["backends"]
    assert "aws_bedrock" in data["backends"]
    assert "azure_ml" in data["backends"]
    assert "on_prem" in data["backends"]


# ── Engine Manifest ──────────────────────────────────────────────────────

def test_builtin_manifests_loaded():
    """Built-in manifests should be registered after init."""
    r2r = get_manifest("research2repo")
    q2r = get_manifest("quant2repo")
    assert r2r is not None
    assert q2r is not None
    assert r2r.version == "2.0.0"
    assert EngineCapability.PDF_INPUT in r2r.capabilities
    assert EngineCapability.CATALOG_INPUT in q2r.capabilities


def test_register_custom_manifest():
    """Dynamic engine registration via manifest."""
    manifest = EngineManifest(
        engine_id="custom-engine",
        version="1.0.0",
        display_name="Custom Engine",
        description="A test custom engine",
        capabilities=[EngineCapability.PDF_INPUT],
        accepted_inputs=["pdf_url"],
        supported_backends=[CloudBackend.ON_PREM],
        protocol_version="1.0",
    )
    register_manifest(manifest)
    assert get_manifest("custom-engine") is not None
    assert get_manifest("custom-engine").display_name == "Custom Engine"


# ── Protocol Conformance ─────────────────────────────────────────────────

def test_conformance_valid_manifest():
    """A valid manifest should produce no errors."""
    data = {
        "engine_id": "test-engine",
        "protocol_version": "1.0",
        "capabilities": ["pdf_input", "text_input"],
        "accepted_inputs": ["pdf_url", "paper_text"],
        "supported_backends": ["gcp_vertex", "on_prem"],
    }
    errors = validate_manifest(data)
    assert errors == []


def test_conformance_missing_engine_id():
    errors = validate_manifest({"protocol_version": "1.0"})
    assert any("engine_id" in e for e in errors)


def test_conformance_unknown_capability():
    data = {
        "engine_id": "test",
        "protocol_version": "1.0",
        "capabilities": ["nonexistent_cap"],
        "accepted_inputs": ["pdf_url"],
        "supported_backends": ["gcp_vertex"],
    }
    errors = validate_manifest(data)
    assert any("nonexistent_cap" in e for e in errors)


def test_conformance_valid_status_file():
    data = {
        "job_id": "abc123",
        "status": "completed",
        "output_url": "https://github.com/org/repo",
        "files_generated": 10,
        "elapsed_seconds": 120.5,
    }
    errors = validate_status_file(data)
    assert errors == []


def test_conformance_failed_status_needs_error():
    data = {"job_id": "abc123", "status": "failed"}
    errors = validate_status_file(data)
    assert any("error" in e for e in errors)


def test_conformance_valid_health():
    data = {"status": "healthy", "engine_id": "research2repo", "version": "2.0.0"}
    errors = validate_health_response(data)
    assert errors == []


def test_conformance_unhealthy_response():
    data = {"status": "unhealthy"}
    errors = validate_health_response(data)
    assert len(errors) >= 1


def test_full_conformance_check():
    result = check_conformance(
        manifest={
            "engine_id": "test",
            "protocol_version": "1.0",
            "capabilities": ["pdf_input"],
            "accepted_inputs": ["pdf_url"],
            "supported_backends": ["on_prem"],
        },
        status_file={"job_id": "x", "status": "completed"},
        health_response={"status": "healthy", "engine_id": "test"},
    )
    assert all(len(errs) == 0 for errs in result.values())
