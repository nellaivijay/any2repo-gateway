"""GCP Vertex AI backend — triggers Research2Repo / Quant2Repo as Vertex
AI custom jobs or prediction endpoints.

Authentication uses Application Default Credentials (ADC) which
transparently supports:
  - Workload Identity Federation (preferred in cross-cloud setups)
  - Service-account JSON key (via GOOGLE_APPLICATION_CREDENTIALS)
  - GCE metadata server (when running on GCP)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.backends.base import BaseBackend
from app.models.schemas import (
    CloudBackend,
    EngineConfig,
    JobResponse,
    JobStatus,
    JobStatusResponse,
)

logger = logging.getLogger(__name__)


class VertexAIBackend(BaseBackend):
    """Submit and monitor jobs on Google Cloud Vertex AI.

    Uses the ``google-cloud-aiplatform`` SDK.  Credentials are resolved
    via ADC — no keys are hardcoded.
    """

    def __init__(self, config: EngineConfig) -> None:
        super().__init__(config)
        self._project = config.gcp_project_id
        self._region = config.gcp_region
        self._client = None  # lazy-initialized

    def _get_client(self):
        """Lazy-init the Vertex AI client (import is deferred)."""
        if self._client is None:
            from google.cloud import aiplatform

            aiplatform.init(
                project=self._project,
                location=self._region,
            )
            self._client = aiplatform
        return self._client

    # ── BaseBackend interface ────────────────────────────────────────

    async def submit_job(
        self,
        job_id: str,
        tenant_id: str,
        payload: dict[str, Any],
    ) -> JobResponse:
        """Submit a Vertex AI custom training job.

        The engine container image is expected to be pre-built and
        pushed to Artifact Registry.  The payload is passed as
        environment variables / arguments.
        """
        try:
            aip = self._get_client()

            engine = payload.get("engine", "research2repo")
            image_uri = (
                f"{self._region}-docker.pkg.dev/{self._project}"
                f"/any2repo/{engine}:latest"
            )

            env_vars = {
                "JOB_ID": job_id,
                "TENANT_ID": tenant_id,
                "PDF_URL": payload.get("pdf_url", ""),
                "ENGINE_OPTIONS": str(payload.get("options", {})),
            }
            if payload.get("catalog_id"):
                env_vars["CATALOG_ID"] = payload["catalog_id"]

            job = aip.CustomJob(
                display_name=f"any2repo-{engine}-{job_id[:8]}",
                worker_pool_specs=[{
                    "machine_spec": {"machine_type": "n1-standard-4"},
                    "replica_count": 1,
                    "container_spec": {
                        "image_uri": image_uri,
                        "env": [
                            {"name": k, "value": v}
                            for k, v in env_vars.items()
                        ],
                    },
                }],
            )
            job.submit()

            logger.info("Vertex AI job submitted: %s (%s)", job_id, job.resource_name)

            return JobResponse(
                job_id=job_id,
                tenant_id=tenant_id,
                engine=payload.get("engine", "research2repo"),
                cloud_backend=CloudBackend.GCP_VERTEX,
                status=JobStatus.RUNNING,
                message=f"Vertex AI job submitted: {job.resource_name}",
            )

        except Exception as exc:
            logger.error("Vertex AI submit failed: %s", exc)
            return JobResponse(
                job_id=job_id,
                tenant_id=tenant_id,
                cloud_backend=CloudBackend.GCP_VERTEX,
                status=JobStatus.FAILED,
                message=f"Submit failed: {exc}",
            )

    async def get_job_status(self, job_id: str) -> JobStatusResponse:
        """Poll Vertex AI for job status."""
        try:
            aip = self._get_client()

            # List jobs matching the display name prefix
            jobs = aip.CustomJob.list(
                filter=f'display_name="any2repo-*-{job_id[:8]}"',
                project=self._project,
                location=self._region,
            )

            if not jobs:
                return JobStatusResponse(
                    job_id=job_id,
                    status=JobStatus.PENDING,
                    error="Job not found in Vertex AI",
                )

            job = jobs[0]
            state = str(job.state).upper()

            status_map = {
                "PIPELINE_STATE_SUCCEEDED": JobStatus.COMPLETED,
                "PIPELINE_STATE_FAILED": JobStatus.FAILED,
                "PIPELINE_STATE_CANCELLED": JobStatus.CANCELLED,
                "PIPELINE_STATE_RUNNING": JobStatus.RUNNING,
                "JOB_STATE_SUCCEEDED": JobStatus.COMPLETED,
                "JOB_STATE_FAILED": JobStatus.FAILED,
                "JOB_STATE_CANCELLED": JobStatus.CANCELLED,
                "JOB_STATE_RUNNING": JobStatus.RUNNING,
            }
            mapped_status = status_map.get(state, JobStatus.RUNNING)

            return JobStatusResponse(
                job_id=job_id,
                cloud_backend=CloudBackend.GCP_VERTEX,
                status=mapped_status,
                metadata={"vertex_state": state, "resource_name": job.resource_name},
            )

        except Exception as exc:
            logger.error("Vertex AI status check failed: %s", exc)
            return JobStatusResponse(
                job_id=job_id,
                status=JobStatus.FAILED,
                error=str(exc),
            )

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a running Vertex AI job."""
        try:
            aip = self._get_client()
            jobs = aip.CustomJob.list(
                filter=f'display_name="any2repo-*-{job_id[:8]}"',
                project=self._project,
                location=self._region,
            )
            if jobs:
                jobs[0].cancel()
                return True
        except Exception as exc:
            logger.error("Vertex AI cancel failed: %s", exc)
        return False
