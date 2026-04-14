"""Tests for the Any2Repo-Gateway API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.middleware.auth import register_tenant, _TENANTS
from app.models.schemas import Tenant, CloudBackend, EngineType


@pytest.fixture(autouse=True)
def _clear_tenants():
    """Reset tenant store between tests."""
    _TENANTS.clear()
    register_tenant(Tenant(
        tenant_id="test-tenant",
        name="Test Tenant",
        cloud_backend=CloudBackend.GCP_VERTEX,
        allowed_engines=[EngineType.RESEARCH2REPO, EngineType.QUANT2REPO],
    ))
    yield
    _TENANTS.clear()


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
