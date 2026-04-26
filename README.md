# Any2Repo-Gateway

**Educational orchestration gateway for scaling research-to-repo implementation pipelines**

Any2Repo-Gateway is an open source educational tool designed to help students and researchers understand cloud orchestration patterns for scaling research-to-implementation workflows. It demonstrates how to route and manage distributed processing jobs across cloud infrastructure.

## Educational Purpose

This tool serves educational purposes by helping students and researchers:
- Learn about cloud orchestration and job routing patterns
- Understand distributed system architecture for AI workflows
- Practice API gateway design and implementation
- Study job queue management and scaling strategies
- Explore cloud infrastructure integration (AWS, GCP, Azure)
- Gain hands-on experience with microservices architecture

## Key Features

- **Multi-Cloud Support**: Integration with AWS, GCP, and Azure cloud providers
- **Job Routing**: Intelligent routing based on resource requirements and availability
- **Queue Management**: Built-in job queue with priority scheduling
- **API Gateway**: RESTful API for job submission and monitoring
- **Scalable Architecture**: Horizontal scaling for high-throughput processing
- **Monitoring**: Real-time job status and resource utilization tracking
- **Fault Tolerance**: Automatic retry and failover mechanisms
- **Cost Optimization**: Spot instance utilization and resource optimization

## Architecture

### Gateway Architecture
```
Client → [API Gateway] → [Job Router] → [Queue Manager] → [Worker Pool]
                                    ↓
                            [Cloud Provider Manager]
                                    ↓
                            [Resource Monitor]
```

### Job Flow
1. Client submits job via API
2. Gateway validates and enqueues job
3. Router assigns to optimal cloud provider/region
4. Worker processes job using Research2Repo/Quant2Repo
5. Results stored and client notified

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/nellaivijay/Any2Repo-Gateway.git
cd Any2Repo-Gateway

# Install dependencies
pip install -e ".[dev]"
```

### Cloud Provider Setup

```bash
# AWS
export AWS_ACCESS_KEY_ID="your_key"
export AWS_SECRET_ACCESS_KEY="your_secret"
export AWS_REGION="us-east-1"

# GCP
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/credentials.json"

# Azure
export AZURE_SUBSCRIPTION_ID="your_subscription_id"
export AZURE_TENANT_ID="your_tenant_id"
export AZURE_CLIENT_ID="your_client_id"
export AZURE_CLIENT_SECRET="your_client_secret"
```

### Basic Usage

```bash
# Start the gateway server
uvicorn app.main:app --reload --port 8000

# Submit a job
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{"engine": "research2repo", "pdf_url": "...", "mode": "agent", "provider": "gemini"}'

# Check job status
curl http://localhost:8000/api/v1/jobs/{job_id}

# List all jobs
curl http://localhost:8000/api/v1/jobs

# Cancel a job
curl -X POST http://localhost:8000/api/v1/jobs/{job_id}/cancel
```

## API Endpoints

### Jobs
- `POST /api/v1/jobs` - Submit new job
- `GET /api/v1/jobs` - List all jobs
- `GET /api/v1/jobs/{id}` - Get job details
- `POST /api/v1/jobs/{id}/cancel` - Cancel job

### Engines
- `GET /api/v1/engines` - List registered engines
- `GET /api/v1/engines/backends` - List supported backends
- `GET /api/v1/engines/{id}` - Get engine manifest

### Tenants
- `POST /api/v1/tenants` - Register tenant
- `GET /api/v1/tenants` - List tenants
- `GET /api/v1/tenants/{id}` - Get tenant details

### System
- `GET /` - Service info
- `GET /health` - Health check

## Configuration

### Gateway Configuration
```yaml
gateway:
  host: "0.0.0.0"
  port: 8000
  workers: 4
  queue_size: 100

cloud:
  providers:
    - name: "aws"
      regions: ["us-east-1", "us-west-2"]
      instance_types: ["m5.large", "m5.xlarge"]
    - name: "gcp"
      regions: ["us-central1", "europe-west1"]
      instance_types: ["n1-standard-2", "n1-standard-4"]

routing:
  strategy: "cost_optimized"  # or "latency_optimized", "balanced"
  max_retries: 3
  timeout: 3600
```

## Project Structure

```
Any2Repo-Gateway/
├── app/                       # Application code
│   ├── main.py               # FastAPI application entry point
│   ├── api/                  # API layer
│   ├── gateway/              # Gateway core logic
│   ├── cloud/                # Cloud provider integration
│   ├── workers/              # Worker management
│   ├── monitoring/           # Monitoring and metrics
│   └── storage/              # Result storage
├── docs/                     # Documentation
├── examples/                 # Example manifests
├── tests/                    # Test suite
└── requirements.txt
```

## Development

### Adding New Cloud Providers

Implement the cloud provider interface in `app/cloud/` directory following existing patterns.

### Testing

Run the test suite:
```bash
pytest tests/ -v
```

### Local Development

Run in development mode:
```bash
uvicorn app.main:app --reload --port 8000
```

## Docker Deployment

```bash
# Build image
docker build -t any2repo-gateway .

# Run container
docker run -p 8000:8000 \
  -e API_KEYS="key1,key2" \
  -e GCP_PROJECT_ID="my-project" \
  any2repo-gateway
```

## License

Apache 2.0 License - See LICENSE file for details.

## Educational Use

This tool is provided for educational purposes to help students and researchers learn about:
- Cloud orchestration and job routing patterns
- Distributed system architecture for AI workflows
- API gateway design and implementation
- Job queue management and scaling strategies
- Multi-cloud infrastructure integration
- Microservices architecture patterns