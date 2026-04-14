"""AWS Bedrock / SageMaker backend — triggers Research2Repo / Quant2Repo
on AWS infrastructure.

Authentication supports:
  - Workload Identity Federation via STS AssumeRoleWithWebIdentity
    (cross-cloud: GCP → AWS without hardcoded keys)
  - Standard AWS credential chain (env vars, instance profile, etc.)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from app.backends.base import BaseBackend
from app.models.schemas import (
    CloudBackend,
    EngineConfig,
    JobResponse,
    JobStatus,
    JobStatusResponse,
)

logger = logging.getLogger(__name__)


class AWSBedrockBackend(BaseBackend):
    """Submit and monitor jobs on AWS (Bedrock agents / SageMaker).

    Uses ``boto3`` for all AWS interactions.  When Workload Identity
    Federation is configured, temporary credentials are obtained via
    STS ``AssumeRoleWithWebIdentity`` before each call.
    """

    def __init__(self, config: EngineConfig) -> None:
        super().__init__(config)
        self._region = config.aws_region
        self._role_arn = config.aws_role_arn
        self._session = None

    def _get_session(self):
        """Create a boto3 session, using WIF if configured."""
        if self._session is not None:
            return self._session

        import boto3

        token_file = os.environ.get("AWS_WEB_IDENTITY_TOKEN_FILE", "")

        if self._role_arn and token_file and os.path.exists(token_file):
            # Cross-cloud: exchange GCP token for AWS creds via STS
            with open(token_file) as f:
                web_identity_token = f.read().strip()

            sts = boto3.client("sts", region_name=self._region)
            assumed = sts.assume_role_with_web_identity(
                RoleArn=self._role_arn,
                RoleSessionName="any2repo-gateway",
                WebIdentityToken=web_identity_token,
                DurationSeconds=3600,
            )
            creds = assumed["Credentials"]
            self._session = boto3.Session(
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretAccessKey"],
                aws_session_token=creds["SessionToken"],
                region_name=self._region,
            )
        else:
            # Standard credential chain
            self._session = boto3.Session(region_name=self._region)

        return self._session

    # ── BaseBackend interface ────────────────────────────────────────

    async def submit_job(
        self,
        job_id: str,
        tenant_id: str,
        payload: dict[str, Any],
    ) -> JobResponse:
        """Submit a job via AWS Lambda or SageMaker Processing."""
        try:
            session = self._get_session()
            lambda_client = session.client("lambda")

            engine = payload.get("engine", "research2repo")
            function_name = f"any2repo-{engine}"

            invoke_payload = {
                "job_id": job_id,
                "tenant_id": tenant_id,
                "pdf_url": payload.get("pdf_url", ""),
                "options": payload.get("options", {}),
            }
            if payload.get("catalog_id"):
                invoke_payload["catalog_id"] = payload["catalog_id"]

            # Async invocation (fire-and-forget)
            response = lambda_client.invoke(
                FunctionName=function_name,
                InvocationType="Event",
                Payload=json.dumps(invoke_payload).encode(),
            )
            status_code = response.get("StatusCode", 0)

            if status_code == 202:
                logger.info("AWS Lambda async invocation accepted: %s", job_id)
                return JobResponse(
                    job_id=job_id,
                    tenant_id=tenant_id,
                    engine=engine,
                    cloud_backend=CloudBackend.AWS_BEDROCK,
                    status=JobStatus.RUNNING,
                    message=f"Lambda invocation accepted (HTTP {status_code})",
                )
            else:
                return JobResponse(
                    job_id=job_id,
                    tenant_id=tenant_id,
                    cloud_backend=CloudBackend.AWS_BEDROCK,
                    status=JobStatus.FAILED,
                    message=f"Lambda invocation returned HTTP {status_code}",
                )

        except Exception as exc:
            logger.error("AWS submit failed: %s", exc)
            return JobResponse(
                job_id=job_id,
                tenant_id=tenant_id,
                cloud_backend=CloudBackend.AWS_BEDROCK,
                status=JobStatus.FAILED,
                message=f"Submit failed: {exc}",
            )

    async def get_job_status(self, job_id: str) -> JobStatusResponse:
        """Check job status via DynamoDB or S3 status file.

        The engine is expected to write a status record to a known
        DynamoDB table (``any2repo-jobs``) as it progresses.
        """
        try:
            session = self._get_session()
            dynamodb = session.resource("dynamodb")
            table = dynamodb.Table("any2repo-jobs")

            response = table.get_item(Key={"job_id": job_id})
            item = response.get("Item")

            if not item:
                return JobStatusResponse(
                    job_id=job_id,
                    cloud_backend=CloudBackend.AWS_BEDROCK,
                    status=JobStatus.PENDING,
                    error="Job record not found in DynamoDB",
                )

            return JobStatusResponse(
                job_id=job_id,
                tenant_id=item.get("tenant_id", ""),
                cloud_backend=CloudBackend.AWS_BEDROCK,
                status=JobStatus(item.get("status", "pending")),
                output_url=item.get("output_url"),
                error=item.get("error"),
                metadata=item.get("metadata", {}),
            )

        except Exception as exc:
            logger.error("AWS status check failed: %s", exc)
            return JobStatusResponse(
                job_id=job_id,
                cloud_backend=CloudBackend.AWS_BEDROCK,
                status=JobStatus.FAILED,
                error=str(exc),
            )

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel is best-effort for async Lambda invocations."""
        logger.warning("Cancel not fully supported for async Lambda jobs: %s", job_id)
        return False
