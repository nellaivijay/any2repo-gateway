"""Application settings loaded from environment variables.

No secrets are hardcoded. All credentials flow through Workload Identity
Federation or environment-injected service account keys.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Gateway configuration — all values sourced from env vars."""

    # ── General ──────────────────────────────────────────────────────
    app_name: str = "Any2Repo-Gateway"
    environment: str = Field("development", alias="ENVIRONMENT")
    debug: bool = False
    log_level: str = "INFO"

    # ── Tenant auth ──────────────────────────────────────────────────
    # Comma-separated list of valid API keys (simple shared-secret auth).
    # In production, replace with a proper identity provider lookup.
    api_keys: str = Field("", alias="API_KEYS")

    # ── State store ──────────────────────────────────────────────────
    # Backend for persistent job and tenant state.
    # Options: "memory" (dev/test), "firestore" (GCP prod)
    store_backend: str = Field("memory", alias="STORE_BACKEND")

    # ── GCP / Vertex AI ──────────────────────────────────────────────
    gcp_project_id: str = Field("", alias="GCP_PROJECT_ID")
    gcp_region: str = Field("us-central1", alias="GCP_REGION")
    # Path to a service-account JSON key.  Left empty when using
    # Workload Identity Federation (preferred).
    gcp_sa_key_path: str = Field("", alias="GOOGLE_APPLICATION_CREDENTIALS")

    # ── GCS Artifact Bucket ──────────────────────────────────────────
    # Engines upload zipped repos to this bucket.  The gateway generates
    # pre-signed URLs pointing here for customer download.
    gcs_artifact_bucket: str = Field("", alias="GCS_ARTIFACT_BUCKET")
    # Pre-signed URL lifetime in seconds (default 15 minutes).
    presigned_url_ttl: int = Field(900, alias="PRESIGNED_URL_TTL")

    # ── AWS / Bedrock ────────────────────────────────────────────────
    aws_region: str = Field("us-east-1", alias="AWS_REGION")
    aws_role_arn: str = Field("", alias="AWS_ROLE_ARN")
    # When using Workload Identity Federation, the gateway exchanges a
    # GCP token for temporary AWS credentials via STS AssumeRoleWithWebIdentity.
    aws_web_identity_token_file: str = Field(
        "", alias="AWS_WEB_IDENTITY_TOKEN_FILE"
    )

    # ── Azure / Azure ML ─────────────────────────────────────────────
    azure_subscription_id: str = Field("", alias="AZURE_SUBSCRIPTION_ID")
    azure_resource_group: str = Field("", alias="AZURE_RESOURCE_GROUP")
    azure_workspace_name: str = Field("", alias="AZURE_WORKSPACE_NAME")
    azure_region: str = Field("eastus", alias="AZURE_REGION")

    # ── On-prem ──────────────────────────────────────────────────────
    on_prem_endpoint: str = Field("", alias="ON_PREM_ENDPOINT")
    on_prem_docker_network: str = Field("any2repo", alias="ON_PREM_DOCKER_NETWORK")

    # ── Engine defaults ──────────────────────────────────────────────
    default_engine: str = Field("research2repo", alias="DEFAULT_ENGINE")
    job_ttl_hours: int = Field(72, alias="JOB_TTL_HOURS")
    # Directory containing engine manifest JSON files for plugin discovery
    engine_manifests_dir: str = Field("", alias="ENGINE_MANIFESTS_DIR")

    # ── Webhook security ─────────────────────────────────────────────
    # HMAC secret used to verify engine webhook signatures.
    # When set, the gateway rejects any webhook missing a valid
    # X-Webhook-Signature header.
    webhook_secret: str = Field("", alias="WEBHOOK_SECRET")

    model_config = {"env_prefix": "", "case_sensitive": False}

    # ── Derived helpers ──────────────────────────────────────────────

    @property
    def valid_api_keys(self) -> set[str]:
        """Parse the comma-separated API_KEYS string into a set."""
        if not self.api_keys:
            return set()
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}


settings = Settings()
