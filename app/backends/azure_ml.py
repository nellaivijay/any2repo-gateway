"""Azure ML backend – submits conversion jobs to Azure Machine Learning."""

from __future__ import annotations

import logging
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

# ---------------------------------------------------------------------------
# Azure ML job‑status → internal JobStatus mapping
# ---------------------------------------------------------------------------
_AZURE_STATUS_MAP: dict[str, JobStatus] = {
    "NotStarted": JobStatus.PENDING,
    "Starting": JobStatus.PENDING,
    "Provisioning": JobStatus.PENDING,
    "Preparing": JobStatus.PENDING,
    "Queued": JobStatus.PENDING,
    "Running": JobStatus.RUNNING,
    "Finalizing": JobStatus.RUNNING,
    "Completed": JobStatus.COMPLETED,
    "Failed": JobStatus.FAILED,
    "Canceled": JobStatus.CANCELLED,
    "CancelRequested": JobStatus.CANCELLED,
    "NotResponding": JobStatus.FAILED,
}


class AzureMLBackend(BaseBackend):
    """Execute conversion jobs on Azure Machine Learning compute."""

    def __init__(self, config: EngineConfig) -> None:
        super().__init__(config)
        self._client: Any | None = None

    # ------------------------------------------------------------------
    # Lazy client initialisation (deferred import)
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """Return a cached :class:`MLClient`, creating it on first call."""
        if self._client is not None:
            return self._client

        try:
            from azure.ai.ml import MLClient
            from azure.identity import DefaultAzureCredential
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "azure-ai-ml and azure-identity packages are required for "
                "the Azure ML backend.  Install them with:\n"
                "  pip install azure-ai-ml azure-identity"
            ) from exc

        credential = DefaultAzureCredential()
        self._client = MLClient(
            credential=credential,
            subscription_id=self.config.azure_subscription_id,
            resource_group_name=self.config.azure_resource_group,
            workspace_name=self.config.azure_workspace_name,
        )
        logger.info(
            "Initialised Azure MLClient for workspace %s/%s/%s",
            self.config.azure_subscription_id,
            self.config.azure_resource_group,
            self.config.azure_workspace_name,
        )
        return self._client

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def submit_job(
        self,
        job_id: str,
        tenant_id: str,
        payload: dict[str, Any],
    ) -> JobResponse:
        """Submit a conversion job to Azure ML and return an acknowledgement."""
        try:
            from azure.ai.ml import command
            from azure.ai.ml.entities import Environment
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "azure-ai-ml is required for the Azure ML backend"
            ) from exc

        client = self._get_client()

        engine: str = payload.get("engine", "default")
        registry: str = payload.get("registry", "any2repo")
        image_uri = f"{registry}.azurecr.io/any2repo/{engine}:latest"

        env_vars: dict[str, str] = {
            "JOB_ID": job_id,
            "TENANT_ID": tenant_id,
            "PDF_URL": payload.get("pdf_url", ""),
            "ENGINE": engine,
        }

        logger.info(
            "Submitting Azure ML job %s (engine=%s, tenant=%s)",
            job_id,
            engine,
            tenant_id,
        )

        try:
            azure_env = Environment(
                image=image_uri,
                name=f"any2repo-{engine}",
            )

            job = command(
                display_name=f"any2repo-{job_id}",
                environment=azure_env,
                environment_variables=env_vars,
                command="python -m any2repo.run",
                compute=payload.get("compute", "cpu-cluster"),
            )

            returned_job = client.jobs.create_or_update(job)
            azure_job_name: str = returned_job.name

            logger.info(
                "Azure ML job created: %s (Azure name: %s)",
                job_id,
                azure_job_name,
            )

            return JobResponse(
                job_id=job_id,
                tenant_id=tenant_id,
                engine=engine,
                cloud_backend=CloudBackend.AZURE_ML,
                status=JobStatus.PENDING,
                message=f"Job submitted to Azure ML as {azure_job_name}",
            )

        except Exception:
            logger.exception("Failed to submit Azure ML job %s", job_id)
            return JobResponse(
                job_id=job_id,
                tenant_id=tenant_id,
                cloud_backend=CloudBackend.AZURE_ML,
                status=JobStatus.FAILED,
                message="Failed to submit job to Azure ML",
            )

    async def get_job_status(self, job_id: str) -> JobStatusResponse:
        """Poll Azure ML for the current status of *job_id*."""
        client = self._get_client()

        try:
            job = client.jobs.get(job_id)
            azure_status: str = job.status or "NotStarted"
            mapped = _AZURE_STATUS_MAP.get(azure_status, JobStatus.PENDING)

            logger.debug(
                "Azure ML job %s status: %s -> %s",
                job_id,
                azure_status,
                mapped,
            )

            return JobStatusResponse(
                job_id=job_id,
                cloud_backend=CloudBackend.AZURE_ML,
                status=mapped,
                metadata={"azure_status": azure_status},
            )

        except Exception:
            logger.exception(
                "Failed to fetch status for Azure ML job %s", job_id
            )
            return JobStatusResponse(
                job_id=job_id,
                cloud_backend=CloudBackend.AZURE_ML,
                status=JobStatus.FAILED,
                error="Unable to retrieve job status from Azure ML",
            )

    async def cancel_job(self, job_id: str) -> bool:
        """Request cancellation of an Azure ML job.  Returns *True* on success."""
        client = self._get_client()

        try:
            client.jobs.cancel(job_id)
            logger.info("Cancellation requested for Azure ML job %s", job_id)
            return True

        except Exception:
            logger.exception(
                "Failed to cancel Azure ML job %s", job_id
            )
            return False
