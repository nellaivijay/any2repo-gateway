"""On-premise execution backend — runs engines locally via Docker
containers or direct HTTP calls to an already-running engine service.

Two execution modes are supported:

  - **HTTP mode** (when ``on_prem_endpoint`` is configured): all
    interactions go through the engine's REST API.
  - **Docker mode** (fallback): the gateway spawns a ``docker run``
    process for each job and tracks containers by ID.
"""

from __future__ import annotations

import asyncio
import json
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


class OnPremBackend(BaseBackend):
    """Submit and monitor jobs on local / on-premise infrastructure.

    When an ``on_prem_endpoint`` is present in the engine config, jobs
    are submitted via HTTP to the engine's REST API.  Otherwise the
    backend falls back to running Docker containers directly via
    ``subprocess``.
    """

    # Class-level registry of running Docker containers: job_id -> container_id
    _containers: dict[str, str] = {}

    def __init__(self, config: EngineConfig) -> None:
        super().__init__(config)
        self._endpoint = config.on_prem_endpoint.rstrip("/") if config.on_prem_endpoint else ""
        self._docker_network = config.on_prem_docker_network or "any2repo"

    # ── Helpers ──────────────────────────────────────────────────────

    @property
    def _is_http_mode(self) -> bool:
        """Return ``True`` when an HTTP endpoint is configured."""
        return bool(self._endpoint)

    def _container_image(self, engine_name: str) -> str:
        """Resolve the OCI image to use for a Docker-mode run."""
        if self.config.manifest and self.config.manifest.container_image:
            return self.config.manifest.container_image
        return f"any2repo/{engine_name}:latest"

    @staticmethod
    async def _run_subprocess(cmd: list[str]) -> tuple[int, str, str]:
        """Run *cmd* asynchronously and return ``(returncode, stdout, stderr)``."""
        import subprocess  # lazy import

        loop = asyncio.get_running_loop()
        proc = await loop.run_in_executor(
            None,
            lambda: subprocess.run(cmd, capture_output=True, text=True),
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()

    def _get_http_client(self):
        """Return a shared ``httpx.AsyncClient`` (lazy import)."""
        import httpx  # lazy import

        return httpx.AsyncClient(timeout=30.0)

    # ── BaseBackend interface ────────────────────────────────────────

    async def submit_job(
        self,
        job_id: str,
        tenant_id: str,
        payload: dict[str, Any],
    ) -> JobResponse:
        """Submit a conversion job.

        - **HTTP mode**: POST payload to ``{endpoint}/api/v1/run``.
        - **Docker mode**: spawn a detached container with the engine
          image and record the container ID.
        """
        engine = payload.get("engine", "research2repo")

        if self._is_http_mode:
            return await self._submit_http(job_id, tenant_id, engine, payload)
        return await self._submit_docker(job_id, tenant_id, engine, payload)

    async def get_job_status(self, job_id: str) -> JobStatusResponse:
        """Return the current status of a previously submitted job."""
        if self._is_http_mode:
            return await self._status_http(job_id)
        return await self._status_docker(job_id)

    async def cancel_job(self, job_id: str) -> bool:
        """Attempt to cancel a running job.  Returns ``True`` on success."""
        if self._is_http_mode:
            return await self._cancel_http(job_id)
        return await self._cancel_docker(job_id)

    # ── HTTP mode ────────────────────────────────────────────────────

    async def _submit_http(
        self,
        job_id: str,
        tenant_id: str,
        engine: str,
        payload: dict[str, Any],
    ) -> JobResponse:
        try:
            async with self._get_http_client() as client:
                body = {
                    "job_id": job_id,
                    "tenant_id": tenant_id,
                    **payload,
                }
                resp = await client.post(
                    f"{self._endpoint}/api/v1/run",
                    json=body,
                )
                resp.raise_for_status()

            logger.info("On-prem HTTP job submitted: %s", job_id)
            return JobResponse(
                job_id=job_id,
                tenant_id=tenant_id,
                engine=engine,
                cloud_backend=CloudBackend.ON_PREM,
                status=JobStatus.RUNNING,
                message="Job submitted to on-prem engine via HTTP",
            )

        except Exception as exc:
            logger.error("On-prem HTTP submit failed for %s: %s", job_id, exc)
            return JobResponse(
                job_id=job_id,
                tenant_id=tenant_id,
                cloud_backend=CloudBackend.ON_PREM,
                status=JobStatus.FAILED,
                message=f"Submit failed: {exc}",
            )

    async def _status_http(self, job_id: str) -> JobStatusResponse:
        try:
            async with self._get_http_client() as client:
                resp = await client.get(
                    f"{self._endpoint}/api/v1/status/{job_id}",
                )
                resp.raise_for_status()
                data = resp.json()

            return JobStatusResponse(
                job_id=job_id,
                cloud_backend=CloudBackend.ON_PREM,
                status=JobStatus(data.get("status", "running")),
                output_url=data.get("output_url"),
                error=data.get("error"),
                metadata=data.get("metadata", {}),
            )

        except Exception as exc:
            logger.error("On-prem HTTP status check failed for %s: %s", job_id, exc)
            return JobStatusResponse(
                job_id=job_id,
                cloud_backend=CloudBackend.ON_PREM,
                status=JobStatus.FAILED,
                error=str(exc),
            )

    async def _cancel_http(self, job_id: str) -> bool:
        try:
            async with self._get_http_client() as client:
                resp = await client.post(
                    f"{self._endpoint}/api/v1/cancel/{job_id}",
                )
                resp.raise_for_status()

            logger.info("On-prem HTTP job cancelled: %s", job_id)
            return True

        except Exception as exc:
            logger.error("On-prem HTTP cancel failed for %s: %s", job_id, exc)
            return False

    # ── Docker mode ──────────────────────────────────────────────────

    async def _submit_docker(
        self,
        job_id: str,
        tenant_id: str,
        engine: str,
        payload: dict[str, Any],
    ) -> JobResponse:
        try:
            image = self._container_image(engine)

            env_vars: dict[str, str] = {
                "JOB_ID": job_id,
                "TENANT_ID": tenant_id,
                "PDF_URL": payload.get("pdf_url", ""),
                "ENGINE_OPTIONS": json.dumps(payload.get("options", {})),
            }
            if payload.get("catalog_id"):
                env_vars["CATALOG_ID"] = payload["catalog_id"]

            cmd: list[str] = [
                "docker", "run", "-d",
                "--network", self._docker_network,
                "--name", f"any2repo-{job_id[:12]}",
            ]
            for key, value in env_vars.items():
                cmd.extend(["-e", f"{key}={value}"])
            cmd.append(image)

            rc, stdout, stderr = await self._run_subprocess(cmd)

            if rc != 0:
                logger.error("Docker run failed (rc=%d): %s", rc, stderr)
                return JobResponse(
                    job_id=job_id,
                    tenant_id=tenant_id,
                    cloud_backend=CloudBackend.ON_PREM,
                    status=JobStatus.FAILED,
                    message=f"docker run failed: {stderr}",
                )

            container_id = stdout[:12]  # short ID
            self._containers[job_id] = container_id
            logger.info(
                "On-prem Docker container started: %s (container=%s)",
                job_id,
                container_id,
            )

            return JobResponse(
                job_id=job_id,
                tenant_id=tenant_id,
                engine=engine,
                cloud_backend=CloudBackend.ON_PREM,
                status=JobStatus.RUNNING,
                message=f"Docker container started: {container_id}",
            )

        except Exception as exc:
            logger.error("On-prem Docker submit failed for %s: %s", job_id, exc)
            return JobResponse(
                job_id=job_id,
                tenant_id=tenant_id,
                cloud_backend=CloudBackend.ON_PREM,
                status=JobStatus.FAILED,
                message=f"Submit failed: {exc}",
            )

    async def _status_docker(self, job_id: str) -> JobStatusResponse:
        container_id = self._containers.get(job_id)
        if not container_id:
            return JobStatusResponse(
                job_id=job_id,
                cloud_backend=CloudBackend.ON_PREM,
                status=JobStatus.PENDING,
                error="No container found for this job",
            )

        try:
            # Get container status
            rc, stdout, stderr = await self._run_subprocess(
                ["docker", "inspect", "--format={{.State.Status}}", container_id],
            )
            if rc != 0:
                return JobStatusResponse(
                    job_id=job_id,
                    cloud_backend=CloudBackend.ON_PREM,
                    status=JobStatus.FAILED,
                    error=f"docker inspect failed: {stderr}",
                )

            state = stdout.lower()

            if state == "running":
                return JobStatusResponse(
                    job_id=job_id,
                    cloud_backend=CloudBackend.ON_PREM,
                    status=JobStatus.RUNNING,
                    metadata={"container_id": container_id, "docker_state": state},
                )

            if state == "exited":
                # Determine exit code
                rc2, exit_code_str, _ = await self._run_subprocess(
                    ["docker", "inspect", "--format={{.State.ExitCode}}", container_id],
                )
                exit_code = int(exit_code_str) if rc2 == 0 and exit_code_str.isdigit() else -1

                if exit_code == 0:
                    return JobStatusResponse(
                        job_id=job_id,
                        cloud_backend=CloudBackend.ON_PREM,
                        status=JobStatus.COMPLETED,
                        metadata={"container_id": container_id, "exit_code": exit_code},
                    )
                else:
                    return JobStatusResponse(
                        job_id=job_id,
                        cloud_backend=CloudBackend.ON_PREM,
                        status=JobStatus.FAILED,
                        error=f"Container exited with code {exit_code}",
                        metadata={"container_id": container_id, "exit_code": exit_code},
                    )

            # Any other state (created, paused, restarting, dead, …)
            return JobStatusResponse(
                job_id=job_id,
                cloud_backend=CloudBackend.ON_PREM,
                status=JobStatus.RUNNING,
                metadata={"container_id": container_id, "docker_state": state},
            )

        except Exception as exc:
            logger.error("On-prem Docker status check failed for %s: %s", job_id, exc)
            return JobStatusResponse(
                job_id=job_id,
                cloud_backend=CloudBackend.ON_PREM,
                status=JobStatus.FAILED,
                error=str(exc),
            )

    async def _cancel_docker(self, job_id: str) -> bool:
        container_id = self._containers.get(job_id)
        if not container_id:
            logger.warning("Cancel requested but no container found for job %s", job_id)
            return False

        try:
            rc, _, stderr = await self._run_subprocess(
                ["docker", "stop", container_id],
            )
            if rc != 0:
                logger.error("docker stop failed for %s: %s", container_id, stderr)
                return False

            logger.info("On-prem Docker container stopped: %s (job=%s)", container_id, job_id)
            return True

        except Exception as exc:
            logger.error("On-prem Docker cancel failed for %s: %s", job_id, exc)
            return False
