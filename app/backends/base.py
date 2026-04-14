"""Abstract base for cloud execution backends."""

from __future__ import annotations

import abc
from typing import Any

from app.models.schemas import EngineConfig, JobResponse, JobStatusResponse


class BaseBackend(abc.ABC):
    """Interface that every cloud backend must implement.

    A backend is responsible for:
    1. Submitting an async job to the cloud platform.
    2. Polling / retrieving the job status.
    3. Cancelling a running job.
    """

    def __init__(self, config: EngineConfig) -> None:
        self.config = config

    @abc.abstractmethod
    async def submit_job(
        self,
        job_id: str,
        tenant_id: str,
        payload: dict[str, Any],
    ) -> JobResponse:
        """Submit a conversion job and return an acknowledgement."""

    @abc.abstractmethod
    async def get_job_status(self, job_id: str) -> JobStatusResponse:
        """Return the current status of a previously submitted job."""

    @abc.abstractmethod
    async def cancel_job(self, job_id: str) -> bool:
        """Attempt to cancel a running job.  Returns True on success."""
