# Any2Repo-Gateway v2.0

Control-plane gateway for **Research2Repo**, **Quant2Repo**, and pluggable engines. Routes conversion jobs to **GCP Vertex AI**, **AWS Bedrock**, **Azure ML**, or **on-premise infrastructure**.

**No LangChain. No LangGraph.** Just FastAPI + boto3 + Vertex AI SDK + azure-ai-ml + httpx.

## Architecture

```
                                ┌─────────────────────────┐
                                │   Engine Manifests       │
                                │   (ENGINE_MANIFESTS_DIR) │
                                └───────────┬─────────────┘
                                            │
                                            v
┌──────────────┐      ┌─────────────────────────────┐      ┌──────────────────┐
│   Client /   │─────>│     Any2Repo-Gateway        │─────>│  GCP Vertex AI   │
│   Frontend   │      │     (FastAPI + Auth)         │      └──────────────────┘
└──────────────┘      │                             │
                      │  - Tenant auth              │      ┌──────────────────┐
  Pluggable Engine    │  - Job routing              │─────>│  AWS Bedrock     │
  Protocol            │  - Status tracking          │      └──────────────────┘
  ───────────────>    │  - IAM / WIF                │
  JSON manifests      │  - Engine registry          │      ┌──────────────────┐
  register engines    │  - Backend abstraction      │─────>│  Azure ML        │
                      │                             │      └──────────────────┘
                      │                             │
                      │                             │      ┌──────────────────┐
                      │                             │─────>│  On-Prem         │
                      └─────────────────────────────┘      │  (Docker / K8s)  │
                                                           └──────────────────┘
```

## Quick Start

```bash
# Install
pip install -e ".[dev]"

# Run locally
uvicorn app.main:app --reload --port 8000

# Run tests
pytest tests/ -v
```

## Pluggable Engine Protocol

Engines register with the gateway via JSON manifests. Each manifest declares the
engine name, supported backends, input schema, and runtime requirements. The
gateway discovers manifests at startup by scanning `ENGINE_MANIFESTS_DIR`.

See [docs/engine_protocol.md](docs/engine_protocol.md) for the full specification.

To register a new engine, drop a manifest into the directory:

```bash
export ENGINE_MANIFESTS_DIR=/etc/any2repo/engines

# Copy your engine manifest
cp my_engine.json "$ENGINE_MANIFESTS_DIR/"

# Restart or send SIGHUP to reload
kill -HUP $(pgrep -f uvicorn)
```

## API Usage

```bash
# Submit a Research2Repo job
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "X-API-Key: your-key" \
  -H "X-Tenant-ID: default" \
  -H "Content-Type: application/json" \
  -d '{
    "engine": "research2repo",
    "pdf_url": "https://arxiv.org/pdf/1706.03762.pdf"
  }'

# Submit a Quant2Repo job
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "X-API-Key: your-key" \
  -H "X-Tenant-ID: default" \
  -H "Content-Type: application/json" \
  -d '{
    "engine": "quant2repo",
    "catalog_id": "time-series-momentum"
  }'

# Submit a job on Azure ML backend
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "X-API-Key: your-key" \
  -H "X-Tenant-ID: default" \
  -H "Content-Type: application/json" \
  -d '{
    "engine": "research2repo",
    "pdf_url": "https://arxiv.org/pdf/1706.03762.pdf",
    "cloud_backend": "azure_ml"
  }'

# Submit a job on on-prem infrastructure
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "X-API-Key: your-key" \
  -H "X-Tenant-ID: default" \
  -H "Content-Type: application/json" \
  -d '{
    "engine": "research2repo",
    "pdf_url": "https://arxiv.org/pdf/1706.03762.pdf",
    "cloud_backend": "on_prem"
  }'

# Check job status
curl http://localhost:8000/api/v1/jobs/{job_id} \
  -H "X-API-Key: your-key" \
  -H "X-Tenant-ID: default"

# List all jobs
curl http://localhost:8000/api/v1/jobs \
  -H "X-API-Key: your-key" \
  -H "X-Tenant-ID: default"

# List registered engines
curl http://localhost:8000/api/v1/engines \
  -H "X-API-Key: your-key"

# List supported backends
curl http://localhost:8000/api/v1/engines/backends \
  -H "X-API-Key: your-key"
```

## Configuration

All settings are environment variables (see `.env.example`):

| Variable | Description | Default |
|---|---|---|
| `API_KEYS` | Comma-separated valid API keys | (empty = no auth) |
| `GCP_PROJECT_ID` | GCP project for Vertex AI | |
| `GCP_REGION` | GCP region | `us-central1` |
| `AWS_REGION` | AWS region for Bedrock | `us-east-1` |
| `AWS_ROLE_ARN` | IAM role for WIF | |
| `GOOGLE_APPLICATION_CREDENTIALS` | GCP SA key path (optional if using WIF) | |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID for Azure ML | |
| `AZURE_RESOURCE_GROUP` | Azure resource group name | |
| `AZURE_WORKSPACE_NAME` | Azure ML workspace name | |
| `AZURE_REGION` | Azure region | `eastus` |
| `ON_PREM_ENDPOINT` | Base URL of on-prem execution service | `http://localhost:9000` |
| `ON_PREM_DOCKER_NETWORK` | Docker network for on-prem containers | `any2repo` |
| `ENGINE_MANIFESTS_DIR` | Directory containing engine JSON manifests | `./manifests` |

## Cross-Cloud IAM (Workload Identity Federation)

The gateway supports **zero-secret** cross-cloud auth:

1. Gateway runs on GCP with a service account
2. GCP ID token is exchanged for temporary AWS credentials via STS
3. No AWS access keys are stored anywhere

Setup:
1. Create an AWS IAM OIDC provider for `accounts.google.com`
2. Create an IAM role with a trust policy allowing your GCP SA
3. Set `AWS_ROLE_ARN` in the gateway environment

## Docker

```bash
docker build -t any2repo-gateway .
docker run -p 8000:8000 \
  -e API_KEYS="key1,key2" \
  -e GCP_PROJECT_ID="my-project" \
  any2repo-gateway
```

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Service info |
| `GET` | `/health` | Health check |
| `POST` | `/api/v1/jobs` | Submit conversion job |
| `GET` | `/api/v1/jobs` | List tenant jobs |
| `GET` | `/api/v1/jobs/{id}` | Get job status |
| `POST` | `/api/v1/jobs/{id}/cancel` | Cancel job |
| `GET` | `/api/v1/engines` | List registered engines |
| `GET` | `/api/v1/engines/backends` | List supported backends |
| `GET` | `/api/v1/engines/{id}` | Get engine manifest |
| `POST` | `/api/v1/tenants` | Register tenant |
| `GET` | `/api/v1/tenants` | List tenants |
| `GET` | `/api/v1/tenants/{id}` | Get tenant details |

## Supported Backends

| Backend | Provider | SDK | Auth |
|---|---|---|---|
| `gcp_vertex` | Google Cloud | `google-cloud-aiplatform` | ADC / WIF |
| `aws_bedrock` | AWS | `boto3` | STS / WIF |
| `azure_ml` | Microsoft Azure | `azure-ai-ml` | DefaultAzureCredential |
| `on_prem` | Self-hosted | `httpx` / Docker CLI | N/A |
