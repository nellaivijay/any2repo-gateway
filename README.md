# Any2Repo-Gateway

Lightweight FastAPI control plane for routing paper-to-repository conversion jobs to execution engines (**Research2Repo**, **Quant2Repo**) running on **GCP Vertex AI** or **AWS Bedrock**.

**No LangChain. No LangGraph.** Just FastAPI + boto3 + Vertex AI SDK.

## Architecture

```
┌──────────────┐      ┌─────────────────────┐      ┌──────────────────┐
│   Client /   │─────▶│  Any2Repo-Gateway   │─────▶│  GCP Vertex AI   │
│   Frontend   │      │  (FastAPI + Auth)    │      │  Research2Repo   │
└──────────────┘      │                     │      │  Quant2Repo      │
                      │  - Tenant auth      │      └──────────────────┘
                      │  - Job routing      │
                      │  - Status tracking  │      ┌──────────────────┐
                      │  - IAM / WIF        │─────▶│  AWS Bedrock     │
                      └─────────────────────┘      │  Research2Repo   │
                                                   │  Quant2Repo      │
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

# Check job status
curl http://localhost:8000/api/v1/jobs/{job_id} \
  -H "X-API-Key: your-key" \
  -H "X-Tenant-ID: default"

# List all jobs
curl http://localhost:8000/api/v1/jobs \
  -H "X-API-Key: your-key" \
  -H "X-Tenant-ID: default"
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
| `POST` | `/api/v1/tenants` | Register tenant |
| `GET` | `/api/v1/tenants` | List tenants |
| `GET` | `/api/v1/tenants/{id}` | Get tenant details |
