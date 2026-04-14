"""Workload Identity Federation helpers.

Provides cross-cloud authentication so the Gateway (running on GCP) can
securely call AWS services without hardcoding any secret keys.

Flow:
  1. Gateway obtains a GCP ID token from the metadata server (or ADC).
  2. The GCP token is exchanged for temporary AWS credentials via
     STS ``AssumeRoleWithWebIdentity``.
  3. The temporary credentials are used to create a boto3 session.

Prerequisites (one-time AWS console setup):
  - Create an IAM OIDC Identity Provider pointing to
    ``https://accounts.google.com``.
  - Create an IAM Role with a trust policy that allows
    ``accounts.google.com`` as the federated principal, scoped to the
    Gateway's GCP service account.
  - Set ``AWS_ROLE_ARN`` and ``GCP_PROJECT_ID`` in the Gateway env.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def get_gcp_id_token(target_audience: str = "sts.amazonaws.com") -> Optional[str]:
    """Obtain a GCP ID token for cross-cloud federation.

    Uses ``google.auth`` Application Default Credentials to mint an
    ID token whose audience matches the AWS STS endpoint.

    Returns:
        The encoded JWT ID token, or None on failure.
    """
    try:
        import google.auth
        import google.auth.transport.requests
        from google.oauth2 import id_token

        request = google.auth.transport.requests.Request()
        token = id_token.fetch_id_token(request, target_audience)
        return token
    except Exception as exc:
        logger.warning("Failed to obtain GCP ID token: %s", exc)
        return None


def get_aws_session_via_wif(
    role_arn: str,
    region: str = "us-east-1",
    session_name: str = "any2repo-gateway",
    duration_seconds: int = 3600,
):
    """Exchange a GCP ID token for temporary AWS credentials.

    Args:
        role_arn: The ARN of the AWS IAM role to assume.
        region: AWS region for the STS call.
        session_name: Name for the assumed role session.
        duration_seconds: Lifetime of the temporary credentials.

    Returns:
        A ``boto3.Session`` with temporary credentials, or None.
    """
    id_token_str = get_gcp_id_token()
    if not id_token_str:
        logger.error("Cannot federate to AWS: no GCP ID token available")
        return None

    try:
        import boto3

        sts = boto3.client("sts", region_name=region)
        response = sts.assume_role_with_web_identity(
            RoleArn=role_arn,
            RoleSessionName=session_name,
            WebIdentityToken=id_token_str,
            DurationSeconds=duration_seconds,
        )
        creds = response["Credentials"]

        session = boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=region,
        )
        logger.info(
            "AWS WIF session established: role=%s expires=%s",
            role_arn, creds["Expiration"],
        )
        return session

    except Exception as exc:
        logger.error("AWS WIF federation failed: %s", exc)
        return None


def write_web_identity_token_file(
    path: str = "/tmp/gcp-wif-token",
    target_audience: str = "sts.amazonaws.com",
) -> Optional[str]:
    """Write a GCP ID token to a file for ``AWS_WEB_IDENTITY_TOKEN_FILE``.

    Some AWS SDKs (e.g. boto3 with the ``web_identity_token_file``
    credential process) expect the token in a file rather than in memory.

    Args:
        path: Where to write the token file.
        target_audience: Audience claim for the ID token.

    Returns:
        The file path on success, or None on failure.
    """
    token = get_gcp_id_token(target_audience)
    if not token:
        return None
    with open(path, "w") as f:
        f.write(token)
    os.environ["AWS_WEB_IDENTITY_TOKEN_FILE"] = path
    logger.info("Wrote WIF token to %s", path)
    return path
