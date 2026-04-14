# Any2Repo Engine Protocol v1.0

> **Status:** Stable  
> **Version:** 1.0  
> **Last Updated:** 2025-01-15  
> **Authors:** Vijayakumar Ramdoss (nellaivijay@gmail.com)

---

## Table of Contents

1. [Overview](#1-overview)
2. [Engine Manifest](#2-engine-manifest-engine-manifestjson)
3. [Input Contract](#3-input-contract)
4. [Output Contract](#4-output-contract)
5. [Health Check](#5-health-check)
6. [Lifecycle](#6-lifecycle)
7. [On-Prem HTTP API](#7-on-prem-http-api)
8. [Error Handling](#8-error-handling)
9. [Conformance Checklist](#9-conformance-checklist)
10. [Examples](#10-examples)

---

## 1. Overview

The **Any2Repo Engine Protocol** defines a standard communication contract between the Any2Repo-Gateway and execution engines. Any engine — whether first-party (Research2Repo, Quant2Repo) or third-party — that implements this protocol can be registered with the gateway and invoked as a processing backend.

### Design Goals

- **Pluggability:** New engines can be added without modifying the gateway.
- **Infrastructure Agnosticism:** Engines can run on GCP Vertex AI, AWS Bedrock, Azure ML, or on-premise infrastructure.
- **Transport Agnosticism:** Engines receive input as environment variables (cloud/container mode) or via HTTP JSON (on-prem mode). The protocol does not mandate a specific transport layer.
- **Observability:** Engines report structured status, support health checks, and emit machine-readable output metadata.
- **Simplicity:** The protocol is intentionally minimal. An engine is a container (or HTTP service) that accepts input, produces output, and reports its status.

### Terminology

| Term | Definition |
|------|-----------|
| **Gateway** | The Any2Repo-Gateway orchestrator that receives user requests, selects an engine, and dispatches jobs. |
| **Engine** | A self-contained execution unit that transforms input (e.g., a PDF, text, or catalog) into a repository or artifact. |
| **Job** | A single invocation of an engine with a specific set of inputs. Each job has a unique `job_id`. |
| **Manifest** | A JSON descriptor that declares an engine's identity, capabilities, and resource requirements. |
| **Backend** | The infrastructure provider where an engine runs (e.g., `gcp_vertex`, `aws_bedrock`, `on_prem`). |

---

## 2. Engine Manifest (`engine-manifest.json`)

Every engine **must** publish a manifest file at the root of its container image or repository. The gateway reads this manifest during engine registration to understand the engine's capabilities and requirements.

### Schema

```json
{
  "engine_id": "string (required, unique identifier)",
  "version": "string (semver, e.g., \"1.2.0\")",
  "display_name": "string (human-readable name)",
  "description": "string (brief description of what the engine does)",
  "protocol_version": "1.0",
  "capabilities": [
    "pdf_input",
    "text_input",
    "catalog_input",
    "github_output",
    "local_output",
    "streaming_logs",
    "incremental_validation"
  ],
  "accepted_inputs": [
    "pdf_url",
    "pdf_base64",
    "paper_text"
  ],
  "container_image": "string (OCI-compliant image URI, e.g., \"gcr.io/my-project/research2repo:1.2.0\")",
  "entrypoint": ["python", "-m", "engine.main"],
  "env_defaults": {
    "LOG_LEVEL": "INFO",
    "MAX_RETRIES": "3"
  },
  "supported_backends": ["gcp_vertex", "aws_bedrock", "azure_ml", "on_prem"],
  "health_endpoint": "/health",
  "cpu_request": "2",
  "memory_request": "8Gi",
  "gpu_required": false,
  "timeout_seconds": 3600
}
```

### Field Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `engine_id` | `string` | **Yes** | Globally unique engine identifier. Must match `^[a-z][a-z0-9_-]{2,63}$`. |
| `version` | `string` | **Yes** | Semantic version of the engine (e.g., `"1.2.0"`). |
| `display_name` | `string` | **Yes** | Human-readable display name (max 128 characters). |
| `description` | `string` | **Yes** | Brief description of the engine's purpose (max 512 characters). |
| `protocol_version` | `string` | **Yes** | Must be `"1.0"` for this specification. |
| `capabilities` | `string[]` | **Yes** | List of declared capabilities (see Capability Tokens below). |
| `accepted_inputs` | `string[]` | **Yes** | Input formats the engine can process. At least one required. |
| `container_image` | `string` | **Yes** | Fully-qualified OCI image URI with tag or digest. |
| `entrypoint` | `string[]` | No | Override for the container's default entrypoint. |
| `env_defaults` | `object` | No | Default environment variables. Gateway-provided vars take precedence. |
| `supported_backends` | `string[]` | **Yes** | Infrastructure backends the engine has been tested on. |
| `health_endpoint` | `string` | No | Path for the health check endpoint (default: `"/health"`). Only applicable for on-prem engines. |
| `cpu_request` | `string` | No | CPU cores to request (default: `"1"`). Kubernetes-style notation. |
| `memory_request` | `string` | No | Memory to request (default: `"4Gi"`). Kubernetes-style notation. |
| `gpu_required` | `boolean` | No | Whether the engine requires a GPU (default: `false`). |
| `timeout_seconds` | `integer` | No | Maximum allowed execution time in seconds (default: `3600`). |

### Capability Tokens

| Token | Description |
|-------|-------------|
| `pdf_input` | Engine can process PDF documents. |
| `text_input` | Engine can process raw text / markdown. |
| `catalog_input` | Engine can process structured catalog references. |
| `github_output` | Engine can push results directly to a GitHub repository. |
| `local_output` | Engine can write results to a local filesystem directory. |
| `streaming_logs` | Engine emits structured log lines to stdout during execution. |
| `incremental_validation` | Engine validates output incrementally (e.g., per-file linting). |

### Accepted Input Tokens

| Token | Description |
|-------|-------------|
| `pdf_url` | A publicly accessible or pre-signed URL pointing to a PDF document. |
| `pdf_base64` | A base64-encoded PDF document passed inline. |
| `paper_text` | Raw text content (plain text or Markdown) of the source material. |

---

## 3. Input Contract

Engines receive job input through one of two mechanisms, depending on the deployment backend.

### 3.1 Cloud Mode (Environment Variables)

When running as a container job (GCP Vertex AI Custom Jobs, AWS Bedrock, Azure ML Pipelines), the gateway injects input as environment variables before launching the container.

| Environment Variable | Type | Required | Description |
|---------------------|------|----------|-------------|
| `JOB_ID` | `string` | **Yes** | Unique identifier for this job. Format: UUID v4. |
| `TENANT_ID` | `string` | **Yes** | Identifier of the tenant (user or organization) that owns this job. |
| `PDF_URL` | `string` | Conditional | URL to the input PDF. Required if engine declares `pdf_url` in `accepted_inputs` and this input type is used. |
| `PDF_BASE64` | `string` | Conditional | Base64-encoded PDF content. Required if engine declares `pdf_base64` in `accepted_inputs` and this input type is used. |
| `PAPER_TEXT` | `string` | Conditional | Raw text content. Required if engine declares `paper_text` in `accepted_inputs` and this input type is used. |
| `CATALOG_ID` | `string` | No | Reference to a catalog entry for structured input. |
| `ENGINE_OPTIONS` | `string` | No | JSON-encoded string of engine-specific options (e.g., `{"model":"gpt-4","temperature":0.2}`). |
| `OUTPUT_DIR` | `string` | **Yes** | Filesystem path where the engine must write its output. Guaranteed to exist and be writable. |
| `CALLBACK_URL` | `string` | No | HTTPS URL the engine should POST status updates to upon completion or failure. |

**Rules:**
- At least one of `PDF_URL`, `PDF_BASE64`, or `PAPER_TEXT` must be provided.
- `ENGINE_OPTIONS`, when present, is always a valid JSON string. Engines should parse it and apply options gracefully, ignoring unknown keys.
- The `OUTPUT_DIR` is an absolute path. Engines must not write outside this directory.

### 3.2 On-Prem HTTP Mode

When running as a persistent HTTP service, the gateway sends a `POST` request with a JSON body.

**Endpoint:** `POST /api/v1/run`

**Request Body:**

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "tenant_id": "tenant_acme_corp",
  "pdf_url": "https://storage.example.com/papers/sample.pdf",
  "pdf_base64": null,
  "paper_text": null,
  "catalog_id": null,
  "engine_options": {
    "model": "gpt-4",
    "temperature": 0.2,
    "repo_structure": "standard"
  },
  "output_dir": "/jobs/550e8400-e29b-41d4-a716-446655440000/output",
  "callback_url": "https://gateway.example.com/api/v1/callbacks/550e8400"
}
```

**Field Mapping (env var -> JSON):**

| Environment Variable | JSON Field | Notes |
|---------------------|-----------|-------|
| `JOB_ID` | `job_id` | Identical semantics. |
| `TENANT_ID` | `tenant_id` | Identical semantics. |
| `PDF_URL` | `pdf_url` | `null` if not provided. |
| `PDF_BASE64` | `pdf_base64` | `null` if not provided. |
| `PAPER_TEXT` | `paper_text` | `null` if not provided. |
| `CATALOG_ID` | `catalog_id` | `null` if not provided. |
| `ENGINE_OPTIONS` | `engine_options` | Parsed JSON object (not a string). |
| `OUTPUT_DIR` | `output_dir` | Identical semantics. |
| `CALLBACK_URL` | `callback_url` | `null` if not provided. |

**Response (Accepted):**

```json
HTTP/1.1 202 Accepted
Content-Type: application/json

{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "message": "Job accepted and queued for execution."
}
```

**Response (Rejected):**

```json
HTTP/1.1 422 Unprocessable Entity
Content-Type: application/json

{
  "error": "validation_error",
  "message": "At least one input (pdf_url, pdf_base64, paper_text) must be provided.",
  "details": {}
}
```

---

## 4. Output Contract

### 4.1 Output Directory Structure

Engines **must** write all generated artifacts to the `OUTPUT_DIR`. The directory layout is engine-defined, but the following conventions are strongly recommended:

```
{OUTPUT_DIR}/
  .any2repo_status.json    # REQUIRED: Job status file
  README.md                # Recommended: Generated README
  src/                     # Engine-specific output
  tests/                   # Engine-specific output
  requirements.txt         # Engine-specific output
  ...
```

### 4.2 Status Reporting

On completion (success or failure), engines **must** report status via **at least one** of the following mechanisms. Using multiple mechanisms simultaneously is permitted and encouraged.

#### Mechanism A: Status File (Required)

Write a JSON file to `{OUTPUT_DIR}/.any2repo_status.json`:

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "output_url": "https://github.com/acme/generated-repo",
  "error": null,
  "files_generated": 12,
  "elapsed_seconds": 142.7,
  "metadata": {
    "model_used": "gpt-4",
    "tokens_consumed": 48230,
    "input_pages": 8,
    "validation_passed": true
  }
}
```

#### Status File Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `job_id` | `string` | **Yes** | Must match the `JOB_ID` / `job_id` from the input. |
| `status` | `string` | **Yes** | Terminal status. Must be one of: `"completed"`, `"failed"`. |
| `output_url` | `string` | No | URL to the generated repository or downloadable artifact. |
| `error` | `string` | No | Human-readable error message. **Required** when `status` is `"failed"`. |
| `files_generated` | `integer` | No | Count of files produced (excluding the status file itself). Default: `0`. |
| `elapsed_seconds` | `float` | No | Wall-clock execution time in seconds. Default: `0.0`. |
| `metadata` | `object` | No | Free-form key-value metadata. Engines may include model info, token counts, validation results, etc. |

#### Mechanism B: Callback URL

If `CALLBACK_URL` / `callback_url` was provided, the engine should `POST` the same status JSON to that URL:

```
POST {CALLBACK_URL}
Content-Type: application/json

{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "output_url": "https://github.com/acme/generated-repo",
  "error": null,
  "files_generated": 12,
  "elapsed_seconds": 142.7,
  "metadata": {}
}
```

**Callback Requirements:**
- The engine must retry the callback up to **3 times** with exponential backoff (1s, 2s, 4s) on transient failures (HTTP 5xx, network errors).
- The callback must be sent within **30 seconds** of job completion.
- If the callback fails after all retries, the engine must still write the status file to `OUTPUT_DIR`.

#### Mechanism C: Cloud-Specific Status Store

For cloud backends, engines may additionally write status to provider-specific stores:

| Backend | Status Store | Key Format |
|---------|-------------|------------|
| `gcp_vertex` | Google Cloud Storage | `gs://{bucket}/jobs/{job_id}/status.json` |
| `aws_bedrock` | Amazon DynamoDB | Table: `any2repo_jobs`, PK: `job_id` |
| `azure_ml` | Azure Blob Storage | `https://{account}.blob.core.windows.net/jobs/{job_id}/status.json` |

This mechanism is **optional** and supplementary. Mechanism A (status file) is always required.

---

## 5. Health Check

Engines deployed in **on-prem HTTP mode** must expose a health check endpoint.

### Endpoint

```
GET /health
```

### Response (Healthy)

```json
HTTP/1.1 200 OK
Content-Type: application/json

{
  "status": "healthy",
  "engine_id": "research2repo",
  "version": "1.2.0",
  "uptime_seconds": 3621,
  "active_jobs": 2,
  "max_concurrent_jobs": 4
}
```

### Response (Unhealthy)

```json
HTTP/1.1 503 Service Unavailable
Content-Type: application/json

{
  "status": "unhealthy",
  "engine_id": "research2repo",
  "version": "1.2.0",
  "error": "GPU memory exhausted"
}
```

### Health Check Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `status` | `string` | **Yes** | Must be `"healthy"` or `"unhealthy"`. |
| `engine_id` | `string` | **Yes** | Must match the `engine_id` in the manifest. |
| `version` | `string` | **Yes** | Must match the `version` in the manifest. |
| `uptime_seconds` | `integer` | No | Seconds since the engine process started. |
| `active_jobs` | `integer` | No | Number of currently running jobs. |
| `max_concurrent_jobs` | `integer` | No | Maximum concurrent jobs the engine supports. |
| `error` | `string` | No | Explanation when `status` is `"unhealthy"`. |

### Gateway Behavior

- The gateway polls the health endpoint every **30 seconds** (configurable).
- An engine is considered **down** after **3 consecutive failed health checks**.
- The gateway will not dispatch new jobs to unhealthy engines.
- Health checks must respond within **5 seconds** or they are treated as failures.

---

## 6. Lifecycle

Every job progresses through a well-defined set of states.

### State Machine

```
                 +-----------+
                 |  PENDING  |
                 +-----+-----+
                       |
              (engine picks up job)
                       |
                       v
                 +-----------+
                 |  RUNNING  |
                 +-----+-----+
                       |
          +------------+-------------+
          |            |             |
          v            v             v
   +-----------+ +-----------+ +-----------+
   | COMPLETED | |  FAILED   | | CANCELLED |
   +-----------+ +-----------+ +-----------+
```

### State Descriptions

| State | Description | Who Sets It |
|-------|-------------|-------------|
| `PENDING` | Job has been accepted but execution has not started. | Gateway |
| `RUNNING` | Engine is actively processing the job. | Engine (implicitly, by beginning execution) |
| `COMPLETED` | Job finished successfully. Output is available. | Engine (via status file / callback) |
| `FAILED` | Job terminated due to an error. | Engine (via status file / callback) or Gateway (on timeout) |
| `CANCELLED` | Job was cancelled before completion. | Gateway (via cancel API or operator action) |

### Lifecycle Rules

1. **PENDING -> RUNNING:** The gateway sets the job to `PENDING` upon dispatch. The transition to `RUNNING` occurs when the engine begins execution. In cloud mode, the gateway infers `RUNNING` from the container starting. In on-prem mode, the engine returns `202 Accepted` and the gateway transitions to `RUNNING`.

2. **RUNNING -> COMPLETED:** The engine writes `"status": "completed"` to the status file and/or posts it to the callback URL. The gateway reads this and marks the job complete.

3. **RUNNING -> FAILED:** The engine writes `"status": "failed"` with an `error` message. Alternatively, the gateway detects a non-zero container exit code or a timeout expiration and marks the job as `FAILED`.

4. **RUNNING -> CANCELLED:** The gateway sends a cancellation signal:
   - **Cloud mode:** The gateway terminates the container/job via the cloud provider's API.
   - **On-prem mode:** The gateway sends `POST /api/v1/cancel/{job_id}` to the engine.

5. **Terminal States:** `COMPLETED`, `FAILED`, and `CANCELLED` are terminal. No further transitions are allowed.

6. **Idempotency:** The gateway may read the status file or poll the status endpoint multiple times. Engines must ensure that status reporting is idempotent — repeated reads return the same result.

---

## 7. On-Prem HTTP API

Engines deployed as persistent HTTP services (on-premise or self-hosted) must implement the following API endpoints.

### Base URL

All endpoints are relative to the engine's base URL (e.g., `http://engine-host:8080`).

### 7.1 Submit Job

**`POST /api/v1/run`**

Accepts a new job for processing. The engine should validate the input, enqueue the job, and return immediately.

**Request Headers:**

| Header | Required | Description |
|--------|----------|-------------|
| `Content-Type` | **Yes** | Must be `application/json`. |
| `X-Request-ID` | No | Trace ID for distributed tracing. |
| `Authorization` | No | Bearer token if the engine requires authentication. |

**Request Body:**

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "tenant_id": "tenant_acme_corp",
  "pdf_url": "https://storage.example.com/papers/attention.pdf",
  "pdf_base64": null,
  "paper_text": null,
  "catalog_id": null,
  "engine_options": {
    "model": "gpt-4",
    "temperature": 0.2
  },
  "output_dir": "/jobs/550e8400/output",
  "callback_url": "https://gateway.example.com/api/v1/callbacks/550e8400"
}
```

**Success Response:**

```json
HTTP/1.1 202 Accepted
Content-Type: application/json

{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "message": "Job accepted and queued for execution."
}
```

**Error Responses:**

| Status Code | Condition | Body |
|-------------|-----------|------|
| `400 Bad Request` | Malformed JSON or missing required fields. | `{"error": "bad_request", "message": "...", "details": {}}` |
| `409 Conflict` | A job with the same `job_id` already exists. | `{"error": "conflict", "message": "Job already exists.", "job_id": "..."}` |
| `422 Unprocessable Entity` | Input validation failed (e.g., no input provided). | `{"error": "validation_error", "message": "...", "details": {}}` |
| `429 Too Many Requests` | Engine is at capacity. | `{"error": "rate_limited", "message": "...", "retry_after_seconds": 30}` |
| `503 Service Unavailable` | Engine is unhealthy or shutting down. | `{"error": "unavailable", "message": "..."}` |

---

### 7.2 Get Job Status

**`GET /api/v1/status/{job_id}`**

Returns the current status of a job.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `job_id` | `string` | The unique job identifier. |

**Success Response (Running):**

```json
HTTP/1.1 200 OK
Content-Type: application/json

{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "running",
  "progress": {
    "phase": "generating_code",
    "percent_complete": 45,
    "current_step": "Generating src/model.py",
    "steps_completed": 5,
    "steps_total": 12
  },
  "started_at": "2025-01-15T10:30:00Z",
  "elapsed_seconds": 67.3
}
```

**Success Response (Completed):**

```json
HTTP/1.1 200 OK
Content-Type: application/json

{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "output_url": "https://github.com/acme/generated-repo",
  "files_generated": 12,
  "elapsed_seconds": 142.7,
  "started_at": "2025-01-15T10:30:00Z",
  "completed_at": "2025-01-15T10:32:22Z",
  "metadata": {
    "model_used": "gpt-4",
    "tokens_consumed": 48230
  }
}
```

**Success Response (Failed):**

```json
HTTP/1.1 200 OK
Content-Type: application/json

{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "failed",
  "error": "PDF parsing failed: encrypted document not supported.",
  "elapsed_seconds": 3.1,
  "started_at": "2025-01-15T10:30:00Z",
  "failed_at": "2025-01-15T10:30:03Z"
}
```

**Error Responses:**

| Status Code | Condition | Body |
|-------------|-----------|------|
| `404 Not Found` | No job with the given `job_id` exists. | `{"error": "not_found", "message": "Job not found.", "job_id": "..."}` |

---

### 7.3 Cancel Job

**`POST /api/v1/cancel/{job_id}`**

Requests cancellation of a running or pending job.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `job_id` | `string` | The unique job identifier. |

**Success Response:**

```json
HTTP/1.1 200 OK
Content-Type: application/json

{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "cancelled",
  "message": "Job cancellation initiated."
}
```

**Error Responses:**

| Status Code | Condition | Body |
|-------------|-----------|------|
| `404 Not Found` | No job with the given `job_id` exists. | `{"error": "not_found", "message": "Job not found.", "job_id": "..."}` |
| `409 Conflict` | Job is already in a terminal state. | `{"error": "conflict", "message": "Job already completed.", "status": "completed"}` |

**Cancellation Semantics:**
- Cancellation is **best-effort**. The engine should stop processing as soon as possible but is not required to halt instantaneously.
- After cancellation, the engine must **not** write a `"completed"` status. It should write `"status": "cancelled"` to the status file if partial output exists.
- If the engine cannot cancel a job (e.g., it has already entered a non-interruptible phase), it should return `200 OK` and set the status to `cancelled` once the current phase completes.

---

### 7.4 Health Check

**`GET /health`**

See [Section 5: Health Check](#5-health-check) for the full specification.

---

## 8. Error Handling

### 8.1 Engine-Reported Errors

When an engine encounters an error during processing, it **must**:

1. Set `status` to `"failed"` in the status file (`.any2repo_status.json`).
2. Include a descriptive `error` message explaining the failure.
3. If a `CALLBACK_URL` was provided, POST the failure status to the callback.
4. Exit with a **non-zero exit code** (container mode only).

**Example status file on failure:**

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "failed",
  "output_url": null,
  "error": "LLM API rate limit exceeded after 3 retries. Last error: HTTP 429 Too Many Requests.",
  "files_generated": 3,
  "elapsed_seconds": 45.2,
  "metadata": {
    "last_successful_step": "generating_tests",
    "retries_attempted": 3
  }
}
```

### 8.2 Exit Codes (Container Mode)

| Exit Code | Meaning |
|-----------|---------|
| `0` | Success. Gateway reads the status file for details. |
| `1` | General failure. Gateway reads the status file for error details. |
| `2` | Input validation error (bad input data). |
| `3` | Dependency error (e.g., LLM API unreachable). |
| `137` | OOM killed (out of memory). Set by the container runtime. |
| `143` | SIGTERM received (graceful shutdown / cancellation). |

**Rules:**
- A non-zero exit code **always** indicates failure, even if the status file says `"completed"`. The gateway treats exit code as authoritative.
- If the container exits with code `0` but no status file is found, the gateway marks the job as `FAILED` with error `"Engine exited successfully but produced no status file."`.

### 8.3 Timeout Handling

- Each engine declares a `timeout_seconds` in its manifest.
- The gateway enforces this timeout. If the engine does not report a terminal status within the allotted time:
  1. **Cloud mode:** The gateway terminates the container via the cloud provider's API.
  2. **On-prem mode:** The gateway sends `POST /api/v1/cancel/{job_id}` and, if the engine does not respond within 30 seconds, marks the job as `FAILED` with error `"Job timed out after {timeout_seconds} seconds."`.
- Engines should implement internal timeout awareness and attempt a graceful shutdown before the hard limit.

### 8.4 Transient vs. Permanent Errors

Engines are encouraged to distinguish between transient and permanent errors in their `metadata`:

```json
{
  "metadata": {
    "error_type": "transient",
    "retryable": true,
    "suggested_retry_after_seconds": 60
  }
}
```

The gateway may use this information to automatically retry transient failures (up to a configurable limit).

### 8.5 Partial Output

If an engine fails after producing partial output, it should:
1. Leave the partial files in `OUTPUT_DIR`.
2. Set `files_generated` to the count of successfully generated files.
3. Note the failure point in `metadata.last_successful_step`.
4. The gateway will decide whether to surface partial output to the user based on its own policies.

---

## 9. Conformance Checklist

An engine is **protocol-compliant** if and only if it satisfies all of the following requirements.

### Manifest

- [ ] Engine publishes a valid `engine-manifest.json` at the image/repo root.
- [ ] `engine_id` matches the pattern `^[a-z][a-z0-9_-]{2,63}$`.
- [ ] `version` is a valid semantic version string.
- [ ] `protocol_version` is set to `"1.0"`.
- [ ] `capabilities` contains at least one valid capability token.
- [ ] `accepted_inputs` contains at least one valid input token.
- [ ] `container_image` is a valid OCI image URI (container mode) or is omitted (on-prem only).
- [ ] `supported_backends` contains at least one valid backend identifier.

### Input Handling

- [ ] Engine reads input from environment variables (cloud mode) or HTTP JSON (on-prem mode).
- [ ] Engine accepts at least one of: `PDF_URL`, `PDF_BASE64`, `PAPER_TEXT`.
- [ ] Engine gracefully handles missing optional fields (`CATALOG_ID`, `ENGINE_OPTIONS`, `CALLBACK_URL`).
- [ ] Engine parses `ENGINE_OPTIONS` as JSON and ignores unknown keys without error.
- [ ] Engine writes all output exclusively within `OUTPUT_DIR`.

### Output & Status

- [ ] Engine writes `.any2repo_status.json` to `OUTPUT_DIR` upon completion (success or failure).
- [ ] Status file contains valid JSON matching the status schema.
- [ ] `status` field is set to `"completed"` on success and `"failed"` on error.
- [ ] `error` field is populated when `status` is `"failed"`.
- [ ] `job_id` in the status file matches the input `JOB_ID` / `job_id`.
- [ ] If `CALLBACK_URL` is provided, engine POSTs the status JSON to it with retries.

### Health (On-Prem Only)

- [ ] Engine exposes `GET /health` returning a valid health check response.
- [ ] Health endpoint responds within 5 seconds.
- [ ] Health response includes `status`, `engine_id`, and `version`.

### HTTP API (On-Prem Only)

- [ ] `POST /api/v1/run` accepts jobs and returns `202 Accepted`.
- [ ] `GET /api/v1/status/{job_id}` returns current job status.
- [ ] `POST /api/v1/cancel/{job_id}` initiates job cancellation.
- [ ] All error responses use structured JSON with `error` and `message` fields.

### Error Handling

- [ ] Engine exits with non-zero code on failure (container mode).
- [ ] Engine handles `SIGTERM` gracefully and writes a status file before exiting.
- [ ] Engine does not exceed `timeout_seconds` under normal operation.

### Security

- [ ] Engine does not write files outside `OUTPUT_DIR`.
- [ ] Engine does not log or expose secrets from environment variables.
- [ ] Engine validates URLs before fetching (no SSRF via `PDF_URL`).

---

## 10. Examples

### 10.1 Research2Repo Engine Manifest

```json
{
  "engine_id": "research2repo",
  "version": "1.2.0",
  "display_name": "Research2Repo",
  "description": "Transforms academic research papers (PDF or text) into fully functional, documented Python repositories with tests, CI configuration, and README.",
  "protocol_version": "1.0",
  "capabilities": [
    "pdf_input",
    "text_input",
    "github_output",
    "local_output",
    "streaming_logs",
    "incremental_validation"
  ],
  "accepted_inputs": [
    "pdf_url",
    "pdf_base64",
    "paper_text"
  ],
  "container_image": "gcr.io/any2repo/research2repo:1.2.0",
  "entrypoint": ["python", "-m", "research2repo.main"],
  "env_defaults": {
    "LOG_LEVEL": "INFO",
    "MAX_RETRIES": "3",
    "DEFAULT_MODEL": "gpt-4",
    "ENABLE_VALIDATION": "true"
  },
  "supported_backends": ["gcp_vertex", "aws_bedrock", "on_prem"],
  "health_endpoint": "/health",
  "cpu_request": "4",
  "memory_request": "16Gi",
  "gpu_required": false,
  "timeout_seconds": 3600
}
```

### 10.2 Quant2Repo Engine Manifest

```json
{
  "engine_id": "quant2repo",
  "version": "0.9.1",
  "display_name": "Quant2Repo",
  "description": "Converts quantitative finance papers and strategy descriptions into backtestable Python repositories with data pipelines, strategy implementations, and performance analytics.",
  "protocol_version": "1.0",
  "capabilities": [
    "pdf_input",
    "text_input",
    "catalog_input",
    "github_output",
    "local_output",
    "streaming_logs"
  ],
  "accepted_inputs": [
    "pdf_url",
    "pdf_base64",
    "paper_text"
  ],
  "container_image": "gcr.io/any2repo/quant2repo:0.9.1",
  "entrypoint": ["python", "-m", "quant2repo.main"],
  "env_defaults": {
    "LOG_LEVEL": "INFO",
    "MAX_RETRIES": "5",
    "DEFAULT_MODEL": "gpt-4",
    "BACKTEST_YEARS": "5",
    "DATA_SOURCE": "yahoo_finance"
  },
  "supported_backends": ["gcp_vertex", "on_prem"],
  "health_endpoint": "/health",
  "cpu_request": "2",
  "memory_request": "8Gi",
  "gpu_required": false,
  "timeout_seconds": 7200
}
```

### 10.3 Example: Full Job Flow (On-Prem)

Below is a complete example of a job lifecycle using the on-prem HTTP API.

**Step 1: Submit a Job**

```bash
curl -X POST http://engine:8080/api/v1/run \
  -H "Content-Type: application/json" \
  -d '{
    "job_id": "job-20250115-001",
    "tenant_id": "tenant_demo",
    "pdf_url": "https://arxiv.org/pdf/1706.03762v5.pdf",
    "pdf_base64": null,
    "paper_text": null,
    "catalog_id": null,
    "engine_options": {
      "model": "gpt-4",
      "repo_structure": "standard",
      "include_tests": true
    },
    "output_dir": "/data/jobs/job-20250115-001/output",
    "callback_url": "https://gateway.example.com/api/v1/callbacks/job-20250115-001"
  }'

# Response: 202 Accepted
# {"job_id": "job-20250115-001", "status": "pending", "message": "Job accepted and queued for execution."}
```

**Step 2: Poll for Status**

```bash
curl http://engine:8080/api/v1/status/job-20250115-001

# Response: 200 OK
# {
#   "job_id": "job-20250115-001",
#   "status": "running",
#   "progress": {
#     "phase": "generating_code",
#     "percent_complete": 60,
#     "current_step": "Generating src/attention.py",
#     "steps_completed": 7,
#     "steps_total": 12
#   },
#   "started_at": "2025-01-15T10:30:00Z",
#   "elapsed_seconds": 89.4
# }
```

**Step 3: Job Completes**

```bash
curl http://engine:8080/api/v1/status/job-20250115-001

# Response: 200 OK
# {
#   "job_id": "job-20250115-001",
#   "status": "completed",
#   "output_url": "https://github.com/demo/attention-is-all-you-need",
#   "files_generated": 14,
#   "elapsed_seconds": 152.3,
#   "started_at": "2025-01-15T10:30:00Z",
#   "completed_at": "2025-01-15T10:32:32Z",
#   "metadata": {
#     "model_used": "gpt-4",
#     "tokens_consumed": 52140,
#     "input_pages": 15,
#     "validation_passed": true
#   }
# }
```

**Step 4: Verify Status File**

```bash
cat /data/jobs/job-20250115-001/output/.any2repo_status.json

# {
#   "job_id": "job-20250115-001",
#   "status": "completed",
#   "output_url": "https://github.com/demo/attention-is-all-you-need",
#   "error": null,
#   "files_generated": 14,
#   "elapsed_seconds": 152.3,
#   "metadata": {
#     "model_used": "gpt-4",
#     "tokens_consumed": 52140,
#     "input_pages": 15,
#     "validation_passed": true
#   }
# }
```

### 10.4 Example: Cancelling a Job

```bash
curl -X POST http://engine:8080/api/v1/cancel/job-20250115-001

# Response: 200 OK
# {"job_id": "job-20250115-001", "status": "cancelled", "message": "Job cancellation initiated."}
```

---

## Appendix A: JSON Schemas

For programmatic validation, OpenAPI and JSON Schema definitions are available in the gateway repository:

- `schemas/engine-manifest.schema.json` — Validates `engine-manifest.json` files.
- `schemas/status-report.schema.json` — Validates `.any2repo_status.json` files.
- `schemas/run-request.schema.json` — Validates `POST /api/v1/run` request bodies.
- `openapi/engine-api.yaml` — Full OpenAPI 3.1 specification for on-prem engine HTTP API.

## Appendix B: Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2025-01-15 | Initial release of the Engine Protocol specification. |

---

*This specification is maintained by Vijayakumar Ramdoss (nellaivijay@gmail.com). For questions, issues, or proposals, open an issue in the `Any2Repo-Gateway` repository.*
