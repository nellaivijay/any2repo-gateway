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

    # ── GCP / Vertex AI ──────────────────────────────────────────────
    gcp_project_id: str = Field("", alias="GCP_PROJECT_ID")
    gcp_region: str = Field("us-central1", alias="GCP_REGION")
    # Path to a service-account JSON key.  Left empty when using
    # Workload Identity Federation (preferred).
    gcp_sa_key_path: str = Field("", alias="GOOGLE_APPLICATION_CREDENTIALS")

    # ── AWS / Bedrock ────────────────────────────────────────────────
    aws_region: str = Field("us-east-1", alias="AWS_REGION")
    aws_role_arn: str = Field("", alias="AWS_ROLE_ARN")
    # When using Workload Identity Federation, the gateway exchanges a
    # GCP token for temporary AWS credentials via STS AssumeRoleWithWebIdentity.
    aws_web_identity_token_file: str = Field(
        "", alias="AWS_WEB_IDENTITY_TOKEN_FILE"
    )

    # ── Engine defaults ──────────────────────────────────────────────
    default_engine: str = Field("research2repo", alias="DEFAULT_ENGINE")
    job_ttl_hours: int = Field(72, alias="JOB_TTL_HOURS")

    model_config = {"env_prefix": "", "case_sensitive": False}

    # ── Derived helpers ──────────────────────────────────────────────

    @property
    def valid_api_keys(self) -> set[str]:
        """Parse the comma-separated API_KEYS string into a set."""
        if not self.api_keys:
            return set()
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}


settings = Settings()
