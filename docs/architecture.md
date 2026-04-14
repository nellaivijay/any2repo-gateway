# Any2Repo-Gateway — Architecture Documentation

> **Version:** 2.0.0  
> **Last Updated:** 2025-01-15  
> **Status:** Living document — updated with each major release

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [System Architecture Diagram](#2-system-architecture-diagram)
3. [Component Architecture](#3-component-architecture)
4. [End-to-End Request Flow](#4-end-to-end-request-flow)
5. [Dual-Mode Engine Architecture](#5-dual-mode-engine-architecture)
6. [Engine Protocol & Manifests](#6-engine-protocol--manifests)
7. [Backend Dispatch Deep Dive](#7-backend-dispatch-deep-dive)
8. [Multi-Tenancy & IAM](#8-multi-tenancy--iam)
9. [Worked Examples](#9-worked-examples)
10. [Status File & Callback Protocol](#10-status-file--callback-protocol)
11. [Data Model Reference](#11-data-model-reference)

---

## 1. System Overview

### What the Gateway Does

The **Any2Repo-Gateway** is a FastAPI-based HTTP control plane that sits between
clients (CLI tools, frontends, CI pipelines) and execution backends (cloud
platforms, on-prem infrastructure). It receives paper-to-repo conversion
requests, authenticates tenants, resolves which engine and backend to use,
dispatches the job, and tracks its lifecycle.

The gateway does **not** perform any conversion itself. It is purely a routing
and orchestration layer. The actual paper-to-code conversion is performed by
**engines** — self-contained containers that implement the Any2Repo Engine
Protocol.

### The 3-Repo Ecosystem

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       Any2Repo Ecosystem                                │
│                                                                         │
│  ┌───────────────────┐  ┌──────────────────┐  ┌──────────────────────┐  │
│  │  Any2Repo-Gateway  │  │  Research2Repo   │  │    Quant2Repo        │  │
│  │  (Control Plane)   │  │  (ML Engine)     │  │  (Finance Engine)    │  │
│  │                    │  │                  │  │                      │  │
│  │  Routes jobs,      │  │  ML/AI paper     │  │  Quant paper         │  │
│  │  manages tenants,  │  │  --> functional  │  │  --> backtest-ready  │  │
│  │  tracks status     │  │  code repository │  │  trading strategy    │  │
│  └───────────────────┘  └──────────────────┘  └──────────────────────┘  │
│           │                      ▲                       ▲              │
│           │    Engine Protocol    │    Engine Protocol    │              │
│           └──────────────────────┴───────────────────────┘              │
│                                                                         │
│  + Any third-party engine that implements the Engine Protocol            │
└─────────────────────────────────────────────────────────────────────────┘
```

| Repository          | Role                  | What It Does                                     |
|---------------------|-----------------------|--------------------------------------------------|
| **Any2Repo-Gateway** | Control plane        | HTTP API, tenant auth, job routing, status tracking |
| **Research2Repo**    | ML engine            | Converts ML/AI research papers to working code repos |
| **Quant2Repo**       | Finance engine       | Converts quant finance papers to backtest-ready repos |

### Design Principles

- **No LangChain. No LangGraph.** Just FastAPI + cloud SDKs (`google-cloud-aiplatform`,
  `boto3`, `azure-ai-ml`, `httpx`). No heavyweight orchestration frameworks.
- **Control plane only.** The gateway never touches PDFs or generates code. It dispatches
  and tracks.
- **Multi-cloud by default.** Every job can target GCP Vertex AI, AWS Bedrock, Azure ML,
  or on-prem Docker. The backend is selected per-request or per-tenant.
- **Pluggable engines.** Third-party engines register via JSON manifests — no gateway code
  changes required.
- **Zero-secret cross-cloud auth.** GCP Workload Identity Federation exchanges GCP tokens
  for temporary AWS credentials via STS. No hardcoded keys.

---

## 2. System Architecture Diagram

```
                     ┌───────────────────────────────────────────┐
                     │              C L I E N T S                │
                     │  curl  │  React Frontend  │  CI Pipeline  │
                     └───────────────┬───────────────────────────┘
                                     │
                                     │  HTTPS
                                     │  POST /api/v1/jobs
                                     │  Headers: X-API-Key, X-Tenant-ID
                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│                      A N Y 2 R E P O - G A T E W A Y                  │
│                           (FastAPI v2.0.0)                             │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                    MIDDLEWARE LAYER                               │  │
│  │                                                                  │  │
│  │  ┌────────────────────┐    ┌─────────────────────────────────┐  │  │
│  │  │  CORS Middleware   │───>│  TenantAuthMiddleware            │  │  │
│  │  │  (allow_origins=*) │    │  - Validates X-API-Key           │  │  │
│  │  └────────────────────┘    │  - Resolves X-Tenant-ID          │  │  │
│  │                            │  - Attaches Tenant to req.state  │  │  │
│  │                            │  - Skips /health, /docs          │  │  │
│  │                            └─────────────────────────────────┘  │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                     │                                  │
│  ┌──────────────────────────────────┼──────────────────────────────┐  │
│  │                    ROUTER LAYER  │                               │  │
│  │                                  ▼                               │  │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌────────────────┐  │  │
│  │  │  /api/v1/jobs    │  │  /api/v1/engines │  │ /api/v1/tenants│  │  │
│  │  │  POST   submit   │  │  GET   list      │  │ POST  create   │  │  │
│  │  │  GET    status   │  │  GET   by-id     │  │ GET   read     │  │  │
│  │  │  GET    list     │  │  GET   backends  │  │ GET   list     │  │  │
│  │  │  POST   cancel   │  └─────────────────┘  └────────────────┘  │  │
│  │  └────────┬────────┘                                             │  │
│  └───────────┼──────────────────────────────────────────────────────┘  │
│              │                                                         │
│  ┌───────────┼──────────────────────────────────────────────────────┐  │
│  │           │          CORE LAYER                                   │  │
│  │           ▼                                                       │  │
│  │  ┌─────────────────────┐     ┌──────────────────────────────┐    │  │
│  │  │  Engine Registry     │     │  Engine Manifest Loader      │    │  │
│  │  │  - Backend factory   │<────│  - Built-in R2R + Q2R        │    │  │
│  │  │  - Config builder    │     │  - JSON files from           │    │  │
│  │  │  - In-memory job     │     │    ENGINE_MANIFESTS_DIR      │    │  │
│  │  │    store (CRUD)      │     │  - Runtime registration      │    │  │
│  │  └────────┬─────────────┘     └──────────────────────────────┘    │  │
│  │           │                                                       │  │
│  │           │  get_backend(engine, cloud_backend, tenant)            │  │
│  │           ▼                                                       │  │
│  │  ┌─────────────────────────────────────────────────────────────┐  │  │
│  │  │                    BACKEND LAYER                             │  │  │
│  │  │                                                             │  │  │
│  │  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────┐│  │  │
│  │  │  │VertexAIBackend│ │AWSBedrock-  │ │AzureMLBackend        ││  │  │
│  │  │  │              │ │Backend      │ │                      ││  │  │
│  │  │  │ submit_job() │ │ submit_job()│ │ submit_job()         ││  │  │
│  │  │  │ get_status() │ │ get_status()│ │ get_status()         ││  │  │
│  │  │  │ cancel_job() │ │ cancel_job()│ │ cancel_job()         ││  │  │
│  │  │  └──────┬───────┘ └──────┬──────┘ └──────┬───────────────┘│  │  │
│  │  │         │                │               │                 │  │  │
│  │  └─────────┼────────────────┼───────────────┼─────────────────┘  │  │
│  └────────────┼────────────────┼───────────────┼────────────────────┘  │
└───────────────┼────────────────┼───────────────┼────────────────────────┘
                │                │               │
                ▼                ▼               ▼
┌───────────────────┐ ┌──────────────────┐ ┌─────────────────────────────┐
│  GCP Vertex AI    │ │  AWS Bedrock     │ │  Azure ML                   │
│                   │ │                  │ │                             │
│  google-cloud-    │ │  boto3           │ │  azure-ai-ml                │
│  aiplatform SDK   │ │  STS for WIF     │ │  DefaultAzureCredential     │
│  CustomJob API    │ │  Lambda invoke   │ │  MLClient                   │
│  ADC auth         │ │  DynamoDB status │ │  command() jobs             │
└───────────────────┘ └──────────────────┘ └─────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│  On-Prem Backend (OnPremBackend) — dual mode                            │
│                                                                         │
│  ┌──────────────────────────┐  ┌────────────────────────────────────┐  │
│  │  HTTP Mode                │  │  Docker Mode                       │  │
│  │  (on_prem_endpoint set)   │  │  (no endpoint --> spawns containers)│  │
│  │                           │  │                                    │  │
│  │  POST {endpoint}/run      │  │  docker run -d --network any2repo  │  │
│  │  GET  {endpoint}/status   │  │  -e JOB_ID=... -e PDF_URL=...     │  │
│  │  POST {endpoint}/cancel   │  │  any2repo/{engine}:latest          │  │
│  │  via httpx.AsyncClient    │  │  tracks in _containers dict        │  │
│  └──────────────────────────┘  └────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Component Architecture

### Directory Layout

```
Any2Repo-Gateway/
├── app/
│   ├── main.py                 # FastAPI app factory, lifespan events
│   ├── config.py               # Settings from environment variables
│   ├── engine_registry.py      # Backend factory + in-memory job store
│   ├── engine_manifest.py      # Manifest loader (built-in + plugins)
│   ├── iam.py                  # Cross-cloud Workload Identity Federation
│   ├── middleware/
│   │   └── auth.py             # TenantAuthMiddleware
│   ├── routers/
│   │   ├── jobs.py             # Job CRUD endpoints
│   │   ├── engines.py          # Engine discovery endpoints
│   │   └── tenants.py          # Tenant management endpoints
│   ├── backends/
│   │   ├── base.py             # BaseBackend ABC
│   │   ├── gcp_vertex.py       # GCP Vertex AI backend
│   │   ├── aws_bedrock.py      # AWS Bedrock backend
│   │   ├── azure_ml.py         # Azure ML backend
│   │   └── on_prem.py          # On-prem (HTTP + Docker) backend
│   └── models/
│       └── schemas.py          # All Pydantic models
├── docs/
│   ├── architecture.md         # This file
│   └── engine_protocol.md      # Engine Protocol v1.0 specification
├── examples/
│   ├── research2repo-manifest.json
│   ├── quant2repo-manifest.json
│   └── custom-engine-manifest.json
└── tests/
    ├── test_api.py
    └── conformance.py
```

### Component Details

#### `app/main.py` — Application Entry Point

The FastAPI app factory with async lifespan management. On startup:
1. Seeds a default development tenant (via `seed_default_tenant()`)
2. Loads engine manifests (built-in R2R/Q2R + JSON files from `ENGINE_MANIFESTS_DIR`)
3. Pre-warms Workload Identity Federation if `AWS_ROLE_ARN` is configured

Registers two middleware layers (CORS, TenantAuth) and three routers
(jobs, tenants, engines). Exposes `/` (service info) and `/health` (liveness probe).

#### `app/middleware/auth.py` — Tenant Authentication

Implements `TenantAuthMiddleware` (Starlette `BaseHTTPMiddleware`). Every request
(except public paths: `/`, `/health`, `/docs`, `/redoc`, `/openapi.json`) must include:

| Header         | Purpose                                            |
|----------------|----------------------------------------------------|
| `X-API-Key`    | Must match one of the comma-separated `API_KEYS`   |
| `X-Tenant-ID`  | Must resolve to a registered, active tenant         |

On success, the resolved `Tenant` object is attached to `request.state.tenant`.

Also provides the in-memory tenant store: `register_tenant()`, `get_tenant()`,
`seed_default_tenant()`. In production, swap for a database or secrets manager.

#### `app/routers/jobs.py` — Job Lifecycle

| Method | Path                           | Description                       | Status |
|--------|--------------------------------|-----------------------------------|--------|
| POST   | `/api/v1/jobs`                 | Submit a new conversion job       | 202    |
| GET    | `/api/v1/jobs/{job_id}`        | Get current status of a job       | 200    |
| GET    | `/api/v1/jobs`                 | List all jobs for the tenant      | 200    |
| POST   | `/api/v1/jobs/{job_id}/cancel` | Cancel a running job              | 200    |

The submit endpoint validates engine access (against `tenant.allowed_engines`),
checks concurrency limits (`tenant.max_concurrent_jobs`), resolves the backend
(from request override or tenant default), and dispatches via the engine registry.

Status polling refreshes from the cloud backend if the job is still pending/running.

#### `app/routers/engines.py` — Engine Discovery

| Method | Path                           | Description                        |
|--------|--------------------------------|------------------------------------|
| GET    | `/api/v1/engines`              | List all registered engine manifests |
| GET    | `/api/v1/engines/{engine_id}`  | Get manifest for a specific engine  |
| GET    | `/api/v1/engines/backends`     | List all supported backends         |

Supports optional `?backend=gcp_vertex` filter to list only engines that
support a specific backend.

#### `app/routers/tenants.py` — Tenant Management

| Method | Path                          | Description                    |
|--------|-------------------------------|--------------------------------|
| POST   | `/api/v1/tenants`             | Register a new tenant          |
| GET    | `/api/v1/tenants/{tenant_id}` | Get tenant details             |
| GET    | `/api/v1/tenants`             | List all registered tenants    |

In production, these endpoints would require an additional admin-only auth layer.

#### `app/engine_registry.py` — Backend Factory & Job Store

Two responsibilities:

1. **Backend factory** — `get_backend(engine, cloud_backend, tenant)` instantiates
   the correct backend class via `_BACKEND_MAP` and builds an `EngineConfig` merging
   global settings with per-tenant overrides.

2. **In-memory job store** — `store_job()`, `get_job()`, `update_job()`, `list_jobs()`
   manage the `_JOBS` dict. Jobs are stored as `JobStatusResponse` objects keyed by
   `job_id`.

```
_BACKEND_MAP:
    gcp_vertex  ──> VertexAIBackend
    aws_bedrock ──> AWSBedrockBackend
    azure_ml    ──> AzureMLBackend
    on_prem     ──> OnPremBackend
```

#### `app/engine_manifest.py` — Manifest Loading

Manages engine registration via a two-phase load:

```
init_manifests()
    │
    ├── 1. load_builtin_manifests()     ── registers R2R + Q2R
    │
    └── 2. load_manifests_from_dir()    ── scans ENGINE_MANIFESTS_DIR
                                            for *.json files, parses
                                            each as EngineManifest,
                                            can override built-ins
```

Provides `get_manifest(engine_id)` and `list_manifests()` for lookups.

#### `app/backends/base.py` — Backend ABC

Defines the `BaseBackend` abstract class with three methods:

```python
class BaseBackend(ABC):
    def __init__(self, config: EngineConfig) -> None
    async def submit_job(self, job_id, tenant_id, payload) -> JobResponse
    async def get_job_status(self, job_id) -> JobStatusResponse
    async def cancel_job(self, job_id) -> bool
```

All four backends implement this interface identically.

#### `app/backends/gcp_vertex.py` — GCP Vertex AI

Uses `google-cloud-aiplatform` SDK. Lazy-initializes the `aiplatform` client.
Submits jobs as Vertex AI `CustomJob` with `worker_pool_specs` containing the
engine's container image from Artifact Registry. Auth via ADC (Application
Default Credentials — supports WIF, SA key, GCE metadata).

#### `app/backends/aws_bedrock.py` — AWS Bedrock

Uses `boto3`. Supports two auth paths: standard credential chain, or
cross-cloud WIF (reads `AWS_WEB_IDENTITY_TOKEN_FILE`, calls STS
`AssumeRoleWithWebIdentity`). Submits jobs via async Lambda invocation
(`InvocationType="Event"`). Polls status from DynamoDB (`any2repo-jobs` table).
Cancel is best-effort for async Lambda.

#### `app/backends/azure_ml.py` — Azure ML

Uses `azure-ai-ml` `MLClient` with `DefaultAzureCredential`. Lazy-initializes
the client. Submits jobs as Azure ML `command()` jobs with an `Environment`
built from the engine's container image in Azure Container Registry. Maps
Azure job states (NotStarted, Provisioning, Queued, Running, Completed, etc.)
to internal `JobStatus` enum.

#### `app/backends/on_prem.py` — On-Prem (HTTP + Docker)

Dual-mode backend selected by the presence of `on_prem_endpoint`:

- **HTTP mode:** POSTs to `{endpoint}/api/v1/run`, polls
  `{endpoint}/api/v1/status/{job_id}`, cancels via
  `{endpoint}/api/v1/cancel/{job_id}`. Uses `httpx.AsyncClient`.
- **Docker mode:** Runs `docker run -d` with env vars, tracks container IDs
  in the class-level `_containers` dict. Checks status via `docker inspect`.
  Cancels via `docker stop`.

#### `app/models/schemas.py` — Pydantic Models

All request/response models, enums, and configuration types. See
[Section 11: Data Model Reference](#11-data-model-reference) for the full table.

#### `app/config.py` — Settings

`pydantic_settings.BaseSettings` subclass. All values from environment variables.
No secrets hardcoded. Provides a `valid_api_keys` property that parses the
comma-separated `API_KEYS` string.

#### `app/iam.py` — Cross-Cloud Workload Identity Federation

Three functions:
- `get_gcp_id_token()` — mints a GCP ID token via ADC
- `get_aws_session_via_wif()` — exchanges GCP token for temporary AWS creds
- `write_web_identity_token_file()` — writes token to disk for boto3's file-based flow

---

## 4. End-to-End Request Flow

### Sequence Diagram

```
┌────────┐         ┌──────────────┐      ┌───────────┐     ┌─────────────┐    ┌────────────┐
│ Client │         │ TenantAuth   │      │ Jobs      │     │ Engine      │    │ Cloud      │
│ (curl) │         │ Middleware   │      │ Router    │     │ Registry    │    │ Backend    │
└───┬────┘         └──────┬───────┘      └─────┬─────┘     └──────┬──────┘    └─────┬──────┘
    │                     │                    │                  │                 │
    │ POST /api/v1/jobs   │                    │                  │                 │
    │ X-API-Key: sk-...   │                    │                  │                 │
    │ X-Tenant-ID: acme   │                    │                  │                 │
    │ {engine, pdf_url}   │                    │                  │                 │
    │────────────────────>│                    │                  │                 │
    │                     │                    │                  │                 │
    │                     │ Validate API key   │                  │                 │
    │                     │ against API_KEYS   │                  │                 │
    │                     │                    │                  │                 │
    │                     │ Lookup tenant      │                  │                 │
    │                     │ "acme" in store    │                  │                 │
    │                     │                    │                  │                 │
    │                     │ Attach tenant to   │                  │                 │
    │                     │ request.state      │                  │                 │
    │                     │───────────────────>│                  │                 │
    │                     │                    │                  │                 │
    │                     │                    │ Check engine in  │                 │
    │                     │                    │ allowed_engines  │                 │
    │                     │                    │                  │                 │
    │                     │                    │ Validate input   │                 │
    │                     │                    │ (pdf_url set?)   │                 │
    │                     │                    │                  │                 │
    │                     │                    │ Check concurrency│                 │
    │                     │                    │ < max_concurrent │                 │
    │                     │                    │                  │                 │
    │                     │                    │ Resolve backend  │                 │
    │                     │                    │ (req override or │                 │
    │                     │                    │  tenant default) │                 │
    │                     │                    │                  │                 │
    │                     │                    │ get_backend()    │                 │
    │                     │                    │─────────────────>│                 │
    │                     │                    │                  │                 │
    │                     │                    │                  │ Build config   │
    │                     │                    │                  │ (merge global  │
    │                     │                    │                  │ + tenant       │
    │                     │                    │                  │ overrides)     │
    │                     │                    │                  │                 │
    │                     │                    │                  │ Instantiate    │
    │                     │                    │ <backend>        │ backend class  │
    │                     │                    │<─────────────────│                 │
    │                     │                    │                  │                 │
    │                     │                    │ backend.submit_job()               │
    │                     │                    │────────────────────────────────────>│
    │                     │                    │                  │                 │
    │                     │                    │                  │                 │ Launch
    │                     │                    │                  │                 │ container
    │                     │                    │                  │                 │ with envs:
    │                     │                    │                  │                 │ JOB_ID
    │                     │                    │                  │                 │ TENANT_ID
    │                     │                    │                  │                 │ PDF_URL
    │                     │                    │                  │                 │
    │                     │                    │ JobResponse      │                 │
    │                     │                    │<────────────────────────────────────│
    │                     │                    │                  │                 │
    │                     │                    │ store_job()      │                 │
    │                     │                    │─────────────────>│                 │
    │                     │                    │                  │                 │
    │ 202 Accepted        │                    │                  │                 │
    │ {job_id, status:    │                    │                  │                 │
    │  "running"}         │                    │                  │                 │
    │<────────────────────│────────────────────│                  │                 │
    │                     │                    │                  │                 │
    ·  (engine runs async)·                    ·                  ·                 ·
    │                     │                    │                  │                 │
    │ GET /api/v1/jobs/{id}                    │                  │                 │
    │────────────────────>│───────────────────>│                  │                 │
    │                     │                    │                  │                 │
    │                     │                    │ get_job() from   │                 │
    │                     │                    │ in-memory store  │                 │
    │                     │                    │                  │                 │
    │                     │                    │ If still running:│                 │
    │                     │                    │ backend.get_job_status()            │
    │                     │                    │────────────────────────────────────>│
    │                     │                    │                  │                 │
    │                     │                    │ Updated status   │                 │
    │                     │                    │<────────────────────────────────────│
    │                     │                    │                  │                 │
    │ 200 OK              │                    │ update_job()     │                 │
    │ {job_id, status:    │                    │─────────────────>│                 │
    │  "completed",       │                    │                  │                 │
    │  output_url: "..."}│                    │                  │                 │
    │<────────────────────│────────────────────│                  │                 │
    │                     │                    │                  │                 │
```

### Step-by-Step Walkthrough

1. **Client sends `POST /api/v1/jobs`** with `X-API-Key`, `X-Tenant-ID` headers and
   a JSON body containing `engine`, `pdf_url`, and optionally `cloud_backend`,
   `options`, `catalog_id`.

2. **TenantAuthMiddleware** intercepts the request. Validates the API key against
   `settings.valid_api_keys`. Looks up the tenant by `X-Tenant-ID` in the in-memory
   store. If invalid or inactive, returns 401/403/404. On success, attaches the
   `Tenant` object to `request.state.tenant`.

3. **Jobs router** (`submit_job`) receives the validated request:
   - Checks `req.engine` is in `tenant.allowed_engines` (403 if not)
   - Validates at least one input is provided (422 if not)
   - Counts active jobs for this tenant, rejects if at `max_concurrent_jobs` (429)

4. **Backend resolution** — uses `request.cloud_backend` if provided, otherwise falls
   back to `tenant.cloud_backend`.

5. **Engine registry** (`get_backend()`) builds an `EngineConfig` merging global
   settings with per-tenant overrides (e.g., tenant's own GCP project, AWS role ARN),
   then instantiates the backend class from `_BACKEND_MAP`.

6. **Backend submits the job** to the cloud platform (Vertex AI CustomJob, Lambda
   invoke, Azure ML command, or Docker run / HTTP POST).

7. **For cloud backends:** the container is launched with environment variables:
   `JOB_ID`, `TENANT_ID`, `PDF_URL`, `ENGINE_OPTIONS`, `CATALOG_ID`, `OUTPUT_DIR`,
   `CALLBACK_URL`.

8. **Engine container starts.** The engine's `main.py` calls `is_gateway_mode()` which
   checks for the `JOB_ID` environment variable. If found, enters gateway mode via
   `run_gateway_mode()`.

9. **Engine runs its pipeline** — downloads PDF, extracts content, generates code,
   validates output. Writes all artifacts to `OUTPUT_DIR`.

10. **Engine writes `.any2repo_status.json`** to `OUTPUT_DIR` with final status,
    `files_generated`, `elapsed_seconds`, and any errors.

11. **Engine POSTs to `CALLBACK_URL`** (if set) with the status payload, then exits
    with code 0 (success) or 1 (failure).

12. **Client polls `GET /api/v1/jobs/{id}`** — the gateway checks the in-memory store,
    and if the job is still pending/running, refreshes from the cloud backend before
    responding.

---

## 5. Dual-Mode Engine Architecture

Engines (Research2Repo and Quant2Repo) are designed to operate in two distinct modes.
This dual-mode architecture allows the same codebase to serve both standalone users and
gateway-managed deployments.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          ENGINE DUAL-MODE ARCHITECTURE                          │
│                                                                                 │
│  ┌─────────────────────────────────┐    ┌────────────────────────────────────┐  │
│  │  MODE 1: Standalone CLI         │    │  MODE 2: Gateway-Managed           │  │
│  │                                 │    │                                    │  │
│  │  User invokes directly:         │    │  Gateway launches container:       │  │
│  │                                 │    │                                    │  │
│  │  $ python main.py \             │    │  docker run -e JOB_ID=abc123 \    │  │
│  │      --pdf_url https://...      │    │    -e TENANT_ID=acme \            │  │
│  │      --output_dir ./output      │    │    -e PDF_URL=https://... \       │  │
│  │      --model gemini-2.5-flash   │    │    -e OUTPUT_DIR=/output \        │  │
│  │                                 │    │    -e CALLBACK_URL=https://gw/... │  │
│  │                                 │    │    any2repo/research2repo:latest   │  │
│  │  ┌───────────────────────────┐  │    │                                    │  │
│  │  │  main.py                  │  │    │  ┌──────────────────────────────┐  │  │
│  │  │                           │  │    │  │  main.py                     │  │  │
│  │  │  argparse handles args    │  │    │  │                              │  │  │
│  │  │         │                 │  │    │  │  is_gateway_mode()?          │  │  │
│  │  │         ▼                 │  │    │  │  -- checks JOB_ID env var   │  │  │
│  │  │  run_pipeline(args)       │  │    │  │         │                    │  │  │
│  │  │         │                 │  │    │  │    Yes ──┘                    │  │  │
│  │  │         ▼                 │  │    │  │         ▼                    │  │  │
│  │  │  Output to ./output/     │  │    │  │  run_gateway_mode()          │  │  │
│  │  │  Print summary to stdout │  │    │  │         │                    │  │  │
│  │  │  Exit                    │  │    │  │         ▼                    │  │  │
│  │  └───────────────────────────┘  │    │  │  Read params from env vars  │  │  │
│  │                                 │    │  │         │                    │  │  │
│  │  No gateway. No JOB_ID env var. │    │  │         ▼                    │  │  │
│  │  No status file. No callback.   │    │  │  run_pipeline(params)       │  │  │
│  │                                 │    │  │         │                    │  │  │
│  └─────────────────────────────────┘    │  │         ▼                    │  │  │
│                                         │  │  Write .any2repo_status.json│  │  │
│                                         │  │  to OUTPUT_DIR              │  │  │
│                                         │  │         │                    │  │  │
│                                         │  │         ▼                    │  │  │
│                                         │  │  POST to CALLBACK_URL       │  │  │
│                                         │  │  (if set)                    │  │  │
│                                         │  │         │                    │  │  │
│                                         │  │         ▼                    │  │  │
│                                         │  │  exit(0) or exit(1)         │  │  │
│                                         │  └──────────────────────────────┘  │  │
│                                         │                                    │  │
│                                         └────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Gateway Mode Detection

```python
def is_gateway_mode() -> bool:
    """Check if the engine is running under gateway control."""
    return bool(os.environ.get("JOB_ID"))
```

When `JOB_ID` is present, the engine knows it was launched by the gateway and should:
- Read **all** parameters from environment variables (not argparse)
- Write a `.any2repo_status.json` file upon completion
- POST to `CALLBACK_URL` if provided
- Exit with code 0 (success) or 1 (failure)

### Environment Variables in Gateway Mode

| Variable         | Source           | Purpose                                       |
|------------------|------------------|-----------------------------------------------|
| `JOB_ID`         | Gateway          | Unique job identifier (triggers gateway mode)  |
| `TENANT_ID`      | Gateway          | Owning tenant                                  |
| `PDF_URL`        | Client request   | URL to the input paper                         |
| `PDF_BASE64`     | Client request   | Base64-encoded PDF (alternative to URL)        |
| `PAPER_TEXT`     | Client request   | Raw text input (alternative to PDF)            |
| `CATALOG_ID`     | Client request   | Catalog reference (Quant2Repo)                 |
| `ENGINE_OPTIONS` | Client request   | JSON string of engine-specific options         |
| `OUTPUT_DIR`     | Cloud platform   | Where to write generated files                 |
| `CALLBACK_URL`   | Gateway          | URL to POST completion status                  |

---

## 6. Engine Protocol & Manifests

### How Manifests Work

Engine manifests are JSON files that declare an engine's identity, capabilities,
container image, and resource requirements. The gateway uses manifests to:

1. **Discover available engines** at startup
2. **Validate capabilities** (can this engine handle `catalog_input`?)
3. **Resolve container images** for cloud/Docker backends
4. **Apply resource hints** (CPU, memory, GPU, timeout)
5. **Determine supported backends** (which clouds can run this engine?)

### Manifest Loading Sequence

```
Gateway Startup
      │
      ▼
┌─────────────────────────────────┐
│  init_manifests()               │
│                                 │
│  Step 1: load_builtin_manifests │
│  ┌─────────────────────────┐    │
│  │ Register "research2repo"│    │
│  │ Register "quant2repo"   │    │
│  └─────────────────────────┘    │
│                                 │
│  Step 2: load_manifests_from_dir│
│  ┌──────────────────────────┐   │
│  │ Scan ENGINE_MANIFESTS_DIR │  │
│  │ for *.json files          │  │
│  │                           │  │
│  │ For each file:            │  │
│  │   Parse as EngineManifest │  │
│  │   Register (may override  │  │
│  │   built-in with same ID)  │  │
│  └──────────────────────────┘   │
│                                 │
│  Result: _MANIFESTS dict        │
│  populated and queryable        │
└─────────────────────────────────┘
```

### Manifest Schema (with example)

```json
{
  "engine_id":          "research2repo",
  "version":            "2.0.0",
  "display_name":       "Research2Repo",
  "description":        "Convert ML/AI research papers into functional repositories",
  "protocol_version":   "1.0",

  "capabilities": [
    "pdf_input", "text_input", "github_output",
    "local_output", "streaming_logs", "incremental_validation"
  ],
  "accepted_inputs":    ["pdf_url", "pdf_base64", "paper_text"],

  "container_image":    "any2repo/research2repo:latest",
  "entrypoint":         ["python", "-m", "main"],
  "env_defaults":       {},
  "supported_backends": ["gcp_vertex", "aws_bedrock", "azure_ml", "on_prem"],
  "health_endpoint":    "/health",

  "cpu_request":        "4",
  "memory_request":     "16Gi",
  "gpu_required":       false,
  "timeout_seconds":    3600
}
```

### Capability Tokens

| Token                      | Description                                    |
|----------------------------|------------------------------------------------|
| `pdf_input`                | Engine can process PDF documents                |
| `text_input`               | Engine can process raw text / markdown          |
| `catalog_input`            | Engine can process structured catalog refs      |
| `github_output`            | Engine can push results to GitHub               |
| `local_output`             | Engine can write to local filesystem            |
| `streaming_logs`           | Engine emits structured logs to stdout          |
| `incremental_validation`   | Engine validates output incrementally           |

### Conformance Requirements

An engine conforming to the Any2Repo Engine Protocol v1.0 **must**:

1. Accept input via environment variables (cloud mode) or JSON POST (on-prem mode)
2. Write all output to `OUTPUT_DIR`
3. Write `.any2repo_status.json` to `OUTPUT_DIR` upon completion or failure
4. Exit with code 0 on success, 1 on failure
5. Respond to `GET {health_endpoint}` with 200 OK (on-prem mode only)
6. Declare all capabilities accurately in the manifest

---

## 7. Backend Dispatch Deep Dive

### Backend Selection Decision Tree

```
                        ┌────────────────────────────┐
                        │  Incoming JobRequest        │
                        │  req.cloud_backend set?     │
                        └─────────────┬──────────────┘
                                      │
                        ┌─────────────┴──────────────┐
                        │                            │
                   Yes ─┘                            └─ No
                        │                            │
                        ▼                            ▼
              ┌──────────────────┐       ┌──────────────────────┐
              │ Use               │       │ Use tenant's default: │
              │ req.cloud_backend │       │ tenant.cloud_backend  │
              │ (client override) │       │                       │
              └────────┬─────────┘       └──────────┬───────────┘
                       │                            │
                       └────────────┬───────────────┘
                                    │
                                    ▼
                       ┌───────────────────────────┐
                       │  _BACKEND_MAP lookup       │
                       │                            │
                       │  gcp_vertex  --> VertexAI   │
                       │  aws_bedrock --> AWSBedrock │
                       │  azure_ml   --> AzureML    │
                       │  on_prem    --> OnPrem     │
                       └──────────┬────────────────┘
                                  │
                                  ▼
                       ┌──────────────────────┐
                       │  _build_engine_config │
                       │                       │
                       │  Merge:               │
                       │  1. Global settings   │
                       │  2. Tenant overrides   │
                       │  3. Engine manifest    │
                       └──────────┬───────────┘
                                  │
                                  ▼
                       ┌──────────────────────┐
                       │  backend = cls(config)│
                       │  backend.submit_job() │
                       └──────────────────────┘
```

### Backend Comparison

```
┌──────────────────┬──────────────────┬──────────────────┬──────────────────┐
│   GCP Vertex AI  │   AWS Bedrock    │   Azure ML       │   On-Prem        │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│                  │                  │                  │                  │
│ SDK:             │ SDK:             │ SDK:             │ SDK:             │
│ google-cloud-    │ boto3            │ azure-ai-ml      │ httpx / docker   │
│ aiplatform       │                  │ azure-identity   │ CLI              │
│                  │                  │                  │                  │
│ Auth:            │ Auth:            │ Auth:            │ Auth:            │
│ ADC (auto)       │ Standard chain   │ DefaultAzure-    │ None             │
│ - WIF            │ or WIF via STS   │ Credential       │ (network-level)  │
│ - SA key         │ AssumeRoleWith-  │                  │                  │
│ - GCE metadata   │ WebIdentity      │                  │                  │
│                  │                  │                  │                  │
│ Submit:          │ Submit:          │ Submit:          │ Submit:          │
│ CustomJob with   │ Lambda invoke    │ command() job    │ HTTP: POST /run  │
│ worker_pool_specs│ (async, fire-    │ with Environment │ Docker: docker   │
│ on Artifact Reg. │ and-forget)      │ from ACR image   │ run -d           │
│ container image  │                  │                  │                  │
│                  │                  │                  │                  │
│ Status:          │ Status:          │ Status:          │ Status:          │
│ CustomJob.list() │ DynamoDB table   │ client.jobs.get()│ HTTP: GET /status│
│ poll by display  │ "any2repo-jobs"  │ maps Azure state │ Docker: docker   │
│ name prefix      │                  │ to JobStatus     │ inspect          │
│                  │                  │                  │                  │
│ Cancel:          │ Cancel:          │ Cancel:          │ Cancel:          │
│ job.cancel()     │ Best-effort      │ client.jobs.     │ HTTP: POST       │
│                  │ (async Lambda    │ cancel()         │ /cancel          │
│                  │ limitation)      │                  │ Docker: docker   │
│                  │                  │                  │ stop             │
│                  │                  │                  │                  │
│ Init:            │ Init:            │ Init:            │ Init:            │
│ Lazy (first call)│ Lazy (first call)│ Lazy (first call)│ Immediate        │
│                  │                  │                  │                  │
│ Tenant Override: │ Tenant Override: │ Tenant Override:  │ Tenant Override: │
│ gcp_project_id   │ aws_role_arn     │ azure_subscription│ on_prem_endpoint │
│                  │                  │ azure_resource_grp│                  │
│                  │                  │ azure_workspace   │                  │
└──────────────────┴──────────────────┴──────────────────┴──────────────────┘
```

### On-Prem Backend: Dual Mode Detail

```
                    ┌──────────────────────────────────┐
                    │  OnPremBackend.__init__()         │
                    │  config.on_prem_endpoint set?     │
                    └───────────────┬──────────────────┘
                                    │
                     ┌──────────────┴──────────────┐
                     │                             │
                Yes ─┘                             └─ No (empty string)
                     │                             │
                     ▼                             ▼
          ┌──────────────────┐          ┌──────────────────────┐
          │  HTTP Mode       │          │  Docker Mode          │
          │  _is_http_mode   │          │  _is_http_mode        │
          │  = True          │          │  = False              │
          │                  │          │                       │
          │  submit:         │          │  submit:              │
          │  POST {endpoint} │          │  docker run -d        │
          │  /api/v1/run     │          │  --network any2repo   │
          │                  │          │  --name any2repo-{id} │
          │  status:         │          │  -e JOB_ID=...        │
          │  GET {endpoint}  │          │  -e PDF_URL=...       │
          │  /api/v1/status  │          │  {container_image}    │
          │  /{job_id}       │          │                       │
          │                  │          │  status:              │
          │  cancel:         │          │  docker inspect       │
          │  POST {endpoint} │          │  --format {{.State}}  │
          │  /api/v1/cancel  │          │                       │
          │  /{job_id}       │          │  cancel:              │
          │                  │          │  docker stop {id}     │
          │  Uses:           │          │                       │
          │  httpx.AsyncClient│         │  Tracks:              │
          └──────────────────┘          │  _containers dict     │
                                        │  {job_id: ctr_id}     │
                                        └──────────────────────┘
```

---

## 8. Multi-Tenancy & IAM

### Tenant Model

Each tenant represents an organization or user with their own cloud configuration.
The gateway isolates tenants at the data plane level (per-tenant job stores, cloud
credentials, engine access).

```
┌─────────────────────────────────────────────────────────────────────┐
│  Tenant: "acme-corp"                                                │
│                                                                     │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  Identity                                                      │ │
│  │  tenant_id:       "acme-corp"                                  │ │
│  │  name:            "Acme Corporation"                           │ │
│  │  active:          true                                         │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  Cloud Overrides (BYOC — Bring Your Own Cloud)                 │ │
│  │  cloud_backend:         gcp_vertex (default for this tenant)   │ │
│  │  gcp_project_id:        "acme-ml-prod"                         │ │
│  │  aws_role_arn:           "arn:aws:iam::123456:role/acme-r2r"   │ │
│  │  azure_subscription_id: "sub-abc-123"                          │ │
│  │  azure_resource_group:  "acme-ml-rg"                           │ │
│  │  azure_workspace_name:  "acme-ml-ws"                           │ │
│  │  on_prem_endpoint:      "http://acme-gpu-cluster:9000"         │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  Access Control                                                │ │
│  │  allowed_engines:      [research2repo, quant2repo]             │ │
│  │  allowed_engine_ids:   ["custom-bio-engine"]                   │ │
│  │  max_concurrent_jobs:  5                                       │ │
│  └────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

### Per-Tenant Cloud Overrides

When `_build_engine_config()` constructs the config for a backend, it applies
tenant overrides with fallback to global settings:

```
Priority: Tenant Override > Global Setting > Default

  gcp_project_id:
      tenant.gcp_project_id  OR  settings.gcp_project_id

  aws_role_arn:
      tenant.aws_role_arn    OR  settings.aws_role_arn

  azure_subscription_id:
      tenant.azure_subscription_id  OR  settings.azure_subscription_id

  on_prem_endpoint:
      tenant.on_prem_endpoint  OR  settings.on_prem_endpoint
```

### Engine Access Control

The jobs router enforces two levels of engine access:

1. **Built-in engines:** `req.engine` must be in `tenant.allowed_engines`
   (list of `EngineType` enum values).

2. **Plugin engines:** `req.engine_id` checked against `tenant.allowed_engine_ids`
   (list of arbitrary engine ID strings).

### Concurrency Limits

Before submitting a job, the router counts active jobs (status `PENDING` or `RUNNING`)
for the tenant. If the count equals `tenant.max_concurrent_jobs`, the request is
rejected with HTTP 429.

### Cross-Cloud Workload Identity Federation

The gateway supports **zero-secret** cross-cloud authentication from GCP to AWS:

```
┌──────────────────────┐          ┌──────────────────────┐
│  GCP (Gateway Host)  │          │  AWS (Target)         │
│                      │          │                       │
│  1. Gateway has a    │          │  4. AWS IAM has an    │
│     GCP service      │          │     OIDC Identity     │
│     account          │          │     Provider for      │
│                      │          │     accounts.google   │
│  2. Mint GCP ID      │          │     .com              │
│     token via ADC    │          │                       │
│     (audience:       │          │  5. IAM Role trusts   │
│      sts.amazonaws   │   STS    │     the GCP SA as     │
│      .com)           │ -------->│     federated         │
│                      │          │     principal         │
│  3. Send token to    │          │                       │
│     STS Assume-      │          │  6. STS returns       │
│     RoleWithWeb-     │          │     temporary AWS     │
│     Identity         │ <--------│     credentials       │
│                      │          │     (AccessKeyId,     │
│  7. Use temp creds   │          │      SecretAccessKey, │
│     to create boto3  │          │      SessionToken)    │
│     session          │          │                       │
└──────────────────────┘          └──────────────────────┘
```

**Implementation in `app/iam.py`:**

| Function                          | Purpose                                        |
|-----------------------------------|-------------------------------------------------|
| `get_gcp_id_token()`             | Mints a GCP ID token using ADC                  |
| `get_aws_session_via_wif()`      | Exchanges GCP token for temporary AWS session    |
| `write_web_identity_token_file()` | Writes token to disk for boto3 file-based flow   |

The WIF token file is pre-warmed at startup (if `AWS_ROLE_ARN` is configured) and
written to `/tmp/gcp-wif-token`. The `AWSBedrockBackend` reads this file via the
`AWS_WEB_IDENTITY_TOKEN_FILE` environment variable.

---

## 9. Worked Examples

### Example 1: Research2Repo Job on GCP Vertex AI

**Step 1 — Submit the job:**

```bash
curl -X POST http://gateway:8000/api/v1/jobs \
  -H "X-API-Key: sk-prod-abc123" \
  -H "X-Tenant-ID: acme-corp" \
  -H "Content-Type: application/json" \
  -d '{
    "engine": "research2repo",
    "pdf_url": "https://arxiv.org/pdf/1706.03762.pdf",
    "options": {"model": "gemini-2.5-flash", "mode": "agent"}
  }'
```

**Response (202 Accepted):**

```json
{
  "job_id": "a1b2c3d4e5f6789012345678",
  "tenant_id": "acme-corp",
  "engine": "research2repo",
  "engine_id": "",
  "cloud_backend": "gcp_vertex",
  "status": "running",
  "created_at": "2025-01-15T10:00:00Z",
  "message": "Vertex AI job submitted: projects/acme-ml-prod/locations/us-central1/customJobs/123"
}
```

**What happens inside:**

```
Gateway                              GCP Vertex AI
   │                                      │
   │  aiplatform.CustomJob(               │
   │    display_name=                     │
   │      "any2repo-research2repo-a1b2c3d4"
   │    worker_pool_specs=[{              │
   │      machine_spec: n1-standard-4,    │
   │      container_spec: {               │
   │        image_uri:                    │
   │          us-central1-docker.pkg.dev/ │
   │          acme-ml-prod/any2repo/      │
   │          research2repo:latest        │
   │        env: [JOB_ID, TENANT_ID,      │
   │              PDF_URL, ENGINE_OPTIONS] │
   │      }                               │
   │    }]                                │
   │  ).submit()                          │
   │─────────────────────────────────────>│
   │                                      │  Container starts
   │                                      │  R2R detects JOB_ID
   │                                      │  Enters gateway mode
   │                                      │  Downloads PDF
   │                                      │  Runs pipeline
   │                                      │  Writes status file
```

**Step 2 — Poll for status:**

```bash
curl http://gateway:8000/api/v1/jobs/a1b2c3d4e5f6789012345678 \
  -H "X-API-Key: sk-prod-abc123" \
  -H "X-Tenant-ID: acme-corp"
```

**Response (200 OK — completed):**

```json
{
  "job_id": "a1b2c3d4e5f6789012345678",
  "tenant_id": "acme-corp",
  "engine": "research2repo",
  "cloud_backend": "gcp_vertex",
  "status": "completed",
  "created_at": "2025-01-15T10:00:00Z",
  "started_at": "2025-01-15T10:00:05Z",
  "completed_at": "2025-01-15T10:03:12Z",
  "elapsed_seconds": 187.5,
  "output_url": "gs://acme-ml-prod-output/a1b2c3d4/",
  "metadata": {
    "vertex_state": "JOB_STATE_SUCCEEDED",
    "resource_name": "projects/acme-ml-prod/locations/us-central1/customJobs/123"
  }
}
```

---

### Example 2: Quant2Repo Job from Catalog on On-Prem Docker

**Submit with catalog ID and on-prem backend override:**

```bash
curl -X POST http://gateway:8000/api/v1/jobs \
  -H "X-API-Key: sk-prod-abc123" \
  -H "X-Tenant-ID: acme-corp" \
  -H "Content-Type: application/json" \
  -d '{
    "engine": "quant2repo",
    "catalog_id": "time-series-momentum",
    "cloud_backend": "on_prem"
  }'
```

**What happens inside (Docker mode — no `on_prem_endpoint` set):**

```
Gateway                              Local Docker Host
   │                                      │
   │  docker run -d                       │
   │    --network any2repo                │
   │    --name any2repo-a1b2c3d4e5f6      │
   │    -e JOB_ID=a1b2c3d4e5f6...        │
   │    -e TENANT_ID=acme-corp            │
   │    -e PDF_URL=                       │
   │    -e CATALOG_ID=time-series-momentum│
   │    -e ENGINE_OPTIONS={}              │
   │    any2repo/quant2repo:latest        │
   │─────────────────────────────────────>│
   │                                      │
   │  container_id = "3f7a..."            │  Container starts
   │  _containers[job_id] = "3f7a..."     │  Q2R detects JOB_ID
   │<─────────────────────────────────────│  Enters gateway mode
   │                                      │
   │                                      │  Q2R resolves catalog_id
   │                                      │  "time-series-momentum"
   │                                      │  --> finds PDF URL in
   │                                      │      internal catalog
   │                                      │  Downloads paper
   │                                      │  Generates backtest
   │                                      │  strategy code
   │                                      │  Runs validation
   │                                      │  Writes status file:
   │                                      │  .any2repo_status.json
   │                                      │  exit(0)
   │                                      │
   │  Poll: docker inspect 3f7a...        │
   │─────────────────────────────────────>│
   │  State.Status = "exited"             │
   │  State.ExitCode = 0                  │
   │<─────────────────────────────────────│
   │  --> status = "completed"            │
```

**Response (202 Accepted):**

```json
{
  "job_id": "f9e8d7c6b5a4321098765432",
  "tenant_id": "acme-corp",
  "engine": "quant2repo",
  "cloud_backend": "on_prem",
  "status": "running",
  "message": "Docker container started: 3f7a"
}
```

---

### Example 3: Custom Engine Registration

**Step 1 — Create a manifest JSON file:**

```json
{
  "engine_id": "bio2repo",
  "version": "0.1.0",
  "display_name": "Bio2Repo",
  "description": "Convert bioinformatics papers into analysis pipelines",
  "protocol_version": "1.0",
  "capabilities": ["pdf_input", "text_input", "local_output"],
  "accepted_inputs": ["pdf_url", "paper_text"],
  "container_image": "myregistry.example.com/bio2repo:0.1.0",
  "entrypoint": ["python", "-m", "bio2repo.main"],
  "env_defaults": {"LOG_LEVEL": "INFO"},
  "supported_backends": ["on_prem", "gcp_vertex"],
  "health_endpoint": "/health",
  "cpu_request": "2",
  "memory_request": "8Gi",
  "gpu_required": false,
  "timeout_seconds": 1800
}
```

**Step 2 — Place it in the manifests directory:**

```bash
export ENGINE_MANIFESTS_DIR=/etc/any2repo/engines
cp bio2repo-manifest.json "$ENGINE_MANIFESTS_DIR/"
```

**Step 3 — Restart the gateway (manifests load at startup via `init_manifests()`):**

```bash
uvicorn app.main:app --reload --port 8000

# Logs:
# 10:00:01 [app.engine_manifest] INFO: Registered engine: research2repo v2.0.0 (Research2Repo)
# 10:00:01 [app.engine_manifest] INFO: Registered engine: quant2repo v2.0.0 (Quant2Repo)
# 10:00:01 [app.engine_manifest] INFO: Registered engine: bio2repo v0.1.0 (Bio2Repo)
# 10:00:01 [app.engine_manifest] INFO: Loaded 1 engine manifest(s) from /etc/any2repo/engines
# 10:00:01 [app.main] INFO: Loaded 3 engine manifest(s)
```

**Step 4 — Verify via API:**

```bash
curl http://gateway:8000/api/v1/engines \
  -H "X-API-Key: sk-prod-abc123" \
  -H "X-Tenant-ID: default"
```

```json
[
  {"engine_id": "research2repo", "version": "2.0.0", "display_name": "Research2Repo", "...": "..."},
  {"engine_id": "quant2repo", "version": "2.0.0", "display_name": "Quant2Repo", "...": "..."},
  {"engine_id": "bio2repo", "version": "0.1.0", "display_name": "Bio2Repo", "...": "..."}
]
```

**Step 5 — Grant a tenant access and submit a job:**

```bash
# Add "bio2repo" to the tenant's allowed_engine_ids
# (via tenant update or re-creation)

# Submit a job using the custom engine
curl -X POST http://gateway:8000/api/v1/jobs \
  -H "X-API-Key: sk-prod-abc123" \
  -H "X-Tenant-ID: acme-corp" \
  -H "Content-Type: application/json" \
  -d '{
    "engine_id": "bio2repo",
    "pdf_url": "https://example.com/genomics-paper.pdf",
    "cloud_backend": "on_prem"
  }'
```

---

## 10. Status File & Callback Protocol

### `.any2repo_status.json`

Every engine **must** write this file to `OUTPUT_DIR` upon completion or failure.
The gateway and external systems use it to determine final job state.

**Format:**

```json
{
  "job_id": "a1b2c3d4e5f6789012345678",
  "status": "completed",
  "engine_id": "research2repo",
  "output_url": "gs://acme-ml-prod-output/a1b2c3d4/",
  "error": "",
  "files_generated": 42,
  "elapsed_seconds": 187.5,
  "completed_at": "2025-01-15T10:30:00Z",
  "metadata": {
    "tenant_id": "acme-corp",
    "mode": "agent",
    "provider": "gemini",
    "model": "gemini-2.5-flash",
    "paper_title": "Attention Is All You Need"
  }
}
```

**Field Reference:**

| Field              | Type     | Required | Description                                    |
|--------------------|----------|----------|------------------------------------------------|
| `job_id`           | string   | Yes      | The gateway-assigned job identifier             |
| `status`           | string   | Yes      | One of: `completed`, `failed`                   |
| `engine_id`        | string   | Yes      | Engine that processed the job                   |
| `output_url`       | string   | No       | URL/path to the output artifacts                |
| `error`            | string   | No       | Error message (populated on failure)            |
| `files_generated`  | integer  | No       | Count of files produced                         |
| `elapsed_seconds`  | float    | No       | Wall-clock execution time                       |
| `completed_at`     | string   | No       | ISO 8601 timestamp of completion                |
| `metadata`         | object   | No       | Engine-specific metadata (model, mode, etc.)    |

### Callback POST Protocol

If `CALLBACK_URL` is set, the engine **must** POST the status payload to that URL
upon completion or failure.

**Request:**

```
POST {CALLBACK_URL}
Content-Type: application/json

{
  "job_id": "a1b2c3d4e5f6789012345678",
  "status": "completed",
  "engine_id": "research2repo",
  "output_url": "gs://acme-ml-prod-output/a1b2c3d4/",
  "error": "",
  "files_generated": 42,
  "elapsed_seconds": 187.5,
  "completed_at": "2025-01-15T10:30:00Z",
  "metadata": {}
}
```

**Rules:**

- The callback body is identical to the `.any2repo_status.json` content.
- The engine should attempt the callback **once**. If it fails, the engine logs
  the error and exits normally. The gateway relies on polling as the primary
  status mechanism; callbacks are a performance optimization.
- The callback URL is provided by the gateway. Engines must not hardcode it.

**Lifecycle with callback:**

```
Engine Container                     Gateway
      │                                 │
      │  (pipeline completes)           │
      │                                 │
      │  Write .any2repo_status.json    │
      │  to OUTPUT_DIR                  │
      │                                 │
      │  POST CALLBACK_URL             │
      │  {status: "completed", ...}     │
      │────────────────────────────────>│
      │                                 │  update_job(job_id,
      │  200 OK                         │    status=completed)
      │<────────────────────────────────│
      │                                 │
      │  exit(0)                        │
      │                                 │
```

---

## 11. Data Model Reference

All models are defined in `app/models/schemas.py` using Pydantic v2.

### Enums

```
┌──────────────────────────────────────────────────────────────────────┐
│  EngineType                    │  CloudBackend                       │
│  ──────────                    │  ────────────                       │
│  RESEARCH2REPO = "research2repo" │  GCP_VERTEX  = "gcp_vertex"      │
│  QUANT2REPO    = "quant2repo"    │  AWS_BEDROCK = "aws_bedrock"     │
│                                │  AZURE_ML    = "azure_ml"          │
│                                │  ON_PREM     = "on_prem"           │
│                                │  LOCAL       = "local"             │
├────────────────────────────────┼─────────────────────────────────────┤
│  JobStatus                     │  EngineCapability                   │
│  ─────────                     │  ────────────────                   │
│  PENDING   = "pending"         │  PDF_INPUT      = "pdf_input"      │
│  RUNNING   = "running"         │  TEXT_INPUT     = "text_input"     │
│  COMPLETED = "completed"       │  CATALOG_INPUT  = "catalog_input"  │
│  FAILED    = "failed"          │  GITHUB_OUTPUT  = "github_output"  │
│  CANCELLED = "cancelled"       │  LOCAL_OUTPUT   = "local_output"   │
│                                │  STREAMING_LOGS = "streaming_logs" │
│                                │  INCREMENTAL_VALIDATION            │
│                                │    = "incremental_validation"      │
└────────────────────────────────┴─────────────────────────────────────┘
```

### EngineManifest

| Field               | Type                     | Default                              | Description                         |
|---------------------|--------------------------|--------------------------------------|-------------------------------------|
| `engine_id`         | `str`                    | (required)                           | Unique engine identifier            |
| `version`           | `str`                    | `"0.1.0"`                            | SemVer version                      |
| `display_name`      | `str`                    | `""`                                 | Human-friendly name                 |
| `description`       | `str`                    | `""`                                 | Short description                   |
| `capabilities`      | `list[EngineCapability]` | `[]`                                 | Declared capabilities               |
| `accepted_inputs`   | `list[str]`              | `["pdf_url","pdf_base64","paper_text"]` | Input field names accepted       |
| `container_image`   | `str`                    | `""`                                 | OCI image URI                       |
| `entrypoint`        | `list[str]`              | `[]`                                 | Container entrypoint override       |
| `env_defaults`      | `dict[str,str]`          | `{}`                                 | Default env vars                    |
| `supported_backends`| `list[CloudBackend]`     | `[gcp_vertex, aws_bedrock]`          | Where this engine can run           |
| `health_endpoint`   | `str`                    | `"/health"`                          | Liveness probe path (on-prem)       |
| `protocol_version`  | `str`                    | `"1.0"`                              | Engine Protocol version             |
| `cpu_request`       | `str`                    | `"2"`                                | CPU cores requested                 |
| `memory_request`    | `str`                    | `"8Gi"`                              | Memory requested                    |
| `gpu_required`      | `bool`                   | `false`                              | GPU needed?                         |
| `timeout_seconds`   | `int`                    | `3600`                               | Max execution time                  |

### Tenant

| Field                    | Type               | Default                           | Description                     |
|--------------------------|--------------------|---------------------------------  |---------------------------------|
| `tenant_id`              | `str`              | (required)                        | Unique tenant identifier        |
| `name`                   | `str`              | `""`                              | Display name                    |
| `cloud_backend`          | `CloudBackend`     | `gcp_vertex`                      | Default backend for this tenant |
| `gcp_project_id`         | `str or None`      | `None`                            | GCP project override            |
| `aws_role_arn`           | `str or None`      | `None`                            | AWS IAM role override           |
| `azure_subscription_id`  | `str or None`      | `None`                            | Azure subscription override     |
| `azure_resource_group`   | `str or None`      | `None`                            | Azure resource group override   |
| `azure_workspace_name`   | `str or None`      | `None`                            | Azure ML workspace override     |
| `on_prem_endpoint`       | `str or None`      | `None`                            | On-prem endpoint override       |
| `allowed_engines`        | `list[EngineType]` | `[research2repo, quant2repo]`     | Built-in engines allowed        |
| `allowed_engine_ids`     | `list[str]`        | `[]`                              | Plugin engine IDs allowed       |
| `max_concurrent_jobs`    | `int`              | `5`                               | Concurrency limit               |
| `active`                 | `bool`             | `true`                            | Whether tenant is active        |

### JobRequest

| Field            | Type                   | Default          | Description                            |
|------------------|------------------------|------------------|----------------------------------------|
| `engine`         | `EngineType`           | `research2repo`  | Built-in engine to use                 |
| `engine_id`      | `str or None`          | `None`           | Plugin engine ID (overrides `engine`)  |
| `cloud_backend`  | `CloudBackend or None` | `None`           | Backend override (else tenant default) |
| `pdf_url`        | `str or None`          | `None`           | URL to input PDF                       |
| `pdf_base64`     | `str or None`          | `None`           | Base64-encoded PDF                     |
| `paper_text`     | `str or None`          | `None`           | Raw text input                         |
| `output_dir`     | `str`                  | `""`             | Output directory path                  |
| `options`        | `dict`                 | `{}`             | Engine-specific options                |
| `catalog_id`     | `str or None`          | `None`           | Catalog reference (Quant2Repo)         |

**Derived property:** `effective_engine_id` returns `engine_id` if set, else `engine.value`.

### JobResponse

| Field            | Type            | Default              | Description                     |
|------------------|-----------------|----------------------|---------------------------------|
| `job_id`         | `str`           | `uuid4().hex`        | Generated job identifier        |
| `tenant_id`      | `str`           | `""`                 | Owning tenant                   |
| `engine`         | `EngineType`    | `research2repo`      | Engine used                     |
| `engine_id`      | `str`           | `""`                 | Plugin engine ID                |
| `cloud_backend`  | `CloudBackend`  | `gcp_vertex`         | Backend used                    |
| `status`         | `JobStatus`     | `pending`            | Initial status                  |
| `created_at`     | `datetime`      | `now(utc)`           | Submission timestamp            |
| `message`        | `str`           | `"Job submitted..."` | Human-readable message          |

### JobStatusResponse

| Field             | Type               | Default         | Description                       |
|-------------------|--------------------|-----------------|-----------------------------------|
| `job_id`          | `str`              | (required)      | Job identifier                    |
| `tenant_id`       | `str`              | `""`            | Owning tenant                     |
| `engine`          | `EngineType`       | `research2repo` | Engine used                       |
| `engine_id`       | `str`              | `""`            | Plugin engine ID                  |
| `cloud_backend`   | `CloudBackend`     | `gcp_vertex`    | Backend used                      |
| `status`          | `JobStatus`        | `pending`       | Current status                    |
| `created_at`      | `datetime or None` | `None`          | Submission timestamp              |
| `started_at`      | `datetime or None` | `None`          | Execution start timestamp         |
| `completed_at`    | `datetime or None` | `None`          | Completion timestamp              |
| `elapsed_seconds` | `float or None`    | `None`          | Wall-clock execution time         |
| `output_url`      | `str or None`      | `None`          | URL/path to output artifacts      |
| `error`           | `str or None`      | `None`          | Error message (on failure)        |
| `metadata`        | `dict`             | `{}`            | Backend/engine-specific metadata  |

### EngineConfig

| Field                    | Type                     | Default         | Description                 |
|--------------------------|--------------------------|-----------------|-----------------------------|
| `engine`                 | `EngineType`             | (required)      | Engine type                 |
| `engine_id`              | `str`                    | `""`            | Engine identifier           |
| `cloud_backend`          | `CloudBackend`           | (required)      | Target backend              |
| `gcp_project_id`         | `str`                    | `""`            | GCP project                 |
| `gcp_region`             | `str`                    | `"us-central1"` | GCP region                  |
| `vertex_endpoint`        | `str`                    | `""`            | Vertex AI endpoint          |
| `aws_region`             | `str`                    | `"us-east-1"`   | AWS region                  |
| `aws_role_arn`           | `str`                    | `""`            | AWS IAM role ARN            |
| `bedrock_model_id`       | `str`                    | `""`            | Bedrock model ID            |
| `azure_subscription_id`  | `str`                    | `""`            | Azure subscription          |
| `azure_resource_group`   | `str`                    | `""`            | Azure resource group        |
| `azure_workspace_name`   | `str`                    | `""`            | Azure ML workspace          |
| `azure_region`           | `str`                    | `"eastus"`      | Azure region                |
| `on_prem_endpoint`       | `str`                    | `""`            | On-prem service URL         |
| `on_prem_docker_network` | `str`                    | `"any2repo"`    | Docker network name         |
| `manifest`               | `EngineManifest or None` | `None`          | Resolved engine manifest    |
| `timeout_seconds`        | `int`                    | `3600`          | Max execution time          |
| `max_retries`            | `int`                    | `2`             | Retry count                 |

---

*This document is maintained alongside the codebase. For the Engine Protocol
specification, see [`docs/engine_protocol.md`](engine_protocol.md). For API
usage examples, see the [`README.md`](../README.md).*
