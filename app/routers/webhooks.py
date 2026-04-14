"""Webhook endpoint for engine completion callbacks.

The engine (Research2Repo, Quant2Repo, or any plugin) POSTs here when it
finishes — either successfully or with an error.  The payload carries
lightweight metadata + a pre-signed URL to the heavy artifact; the gateway
never buffers the full repo zip in memory.

Flow:
  1. Engine finishes → POSTs to ``/api/v1/webhooks/engine-complete``
  2. Gateway validates the payload and optional HMAC signature
  3. Gateway updates the job record in the state store
  4. If the tenant has a BYOC tunnel, the gateway initiates delivery
  5. Gateway returns a ``WebhookAck``
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.models.schemas import (
    DeliveryMethod,
    EngineWebhookPayload,
    JobStatus,
    WebhookAck,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


def _verify_signature(body: bytes, signature: str, secret: str) -> bool:
    """Verify HMAC-SHA256 signature from the engine."""
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/engine-complete", response_model=WebhookAck)
async def engine_complete(payload: EngineWebhookPayload, request: Request) -> WebhookAck:
    """Receive completion callback from an execution engine.

    This endpoint is called by the engine (not by end-users), so it
    bypasses tenant auth middleware.  When ``WEBHOOK_SECRET`` is set,
    the engine must include a valid ``X-Webhook-Signature`` header.
    """
    # ── Signature verification (if configured) ───────────────────────
    if settings.webhook_secret:
        sig = request.headers.get("X-Webhook-Signature", "")
        if not sig:
            raise HTTPException(status_code=401, detail="Missing X-Webhook-Signature header")
        raw_body = await request.body()
        if not _verify_signature(raw_body, sig, settings.webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # ── Look up the job ──────────────────────────────────────────────
    from app.main import get_store  # deferred to avoid circular import

    store = get_store()
    job = store.get_job(payload.job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail=f"Job '{payload.job_id}' not found",
        )

    # ── Map engine status to JobStatus ───────────────────────────────
    status_str = payload.status.lower()
    if status_str == "completed":
        if not payload.artifact_url:
            logger.warning(
                "Webhook for job %s reports completed but no artifact_url provided",
                payload.job_id,
            )
        new_status = JobStatus.COMPLETED
    elif status_str == "failed":
        new_status = JobStatus.FAILED
    else:
        new_status = JobStatus.FAILED
        logger.warning("Unknown engine status '%s' for job %s", payload.status, payload.job_id)

    # ── Update job record ────────────────────────────────────────────
    update_fields: dict = {
        "status": new_status,
        "completed_at": datetime.now(timezone.utc),
        "elapsed_seconds": payload.elapsed_seconds,
        "artifact_url": payload.artifact_url,
        "artifact_size_bytes": payload.artifact_size_bytes,
        "output_url": payload.output_url,
        "error": payload.error,
        "metadata": {
            **(job.metadata or {}),
            "engine_id": payload.engine_id,
            "files_generated": payload.files_generated,
            **(payload.metadata or {}),
        },
    }
    store.update_job(payload.job_id, **update_fields)

    logger.info(
        "Webhook received: job=%s status=%s engine=%s files=%d elapsed=%.1fs",
        payload.job_id,
        new_status.value,
        payload.engine_id,
        payload.files_generated,
        payload.elapsed_seconds,
    )

    # ── Initiate BYOC delivery if tenant has a tunnel ────────────────
    delivery_initiated = False
    if new_status == JobStatus.COMPLETED and payload.artifact_url:
        tenant = store.get_tenant(job.tenant_id)
        if tenant and tenant.delivery_method == DeliveryMethod.BYOC_TUNNEL and tenant.tunnel_url:
            delivery_initiated = await _deliver_via_tunnel(
                job_id=payload.job_id,
                artifact_url=payload.artifact_url,
                tunnel_url=tenant.tunnel_url,
                store=store,
            )

    return WebhookAck(
        job_id=payload.job_id,
        status=new_status,
        delivery_initiated=delivery_initiated,
        message=f"Job {new_status.value}",
    )


async def _deliver_via_tunnel(
    job_id: str,
    artifact_url: str,
    tunnel_url: str,
    store,
) -> bool:
    """Push the artifact URL to the tenant's BYOC infrastructure via tunnel.

    The gateway does NOT download the artifact itself — it sends the
    pre-signed URL to the customer's receiver, which pulls directly
    from GCS/S3.  This keeps the gateway stateless and memory-lean.
    """
    store.update_job(job_id, status=JobStatus.DELIVERING)

    try:
        import httpx

        delivery_payload = {
            "job_id": job_id,
            "artifact_url": artifact_url,
            "action": "pull_artifact",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{tunnel_url.rstrip('/')}/api/v1/receive",
                json=delivery_payload,
            )
            resp.raise_for_status()

        store.update_job(job_id, status=JobStatus.DELIVERED)
        logger.info("Artifact delivered via tunnel for job %s", job_id)
        return True

    except Exception as exc:
        logger.error("Tunnel delivery failed for job %s: %s", job_id, exc)
        # Don't fail the job — the artifact_url is still valid for direct download
        store.update_job(
            job_id,
            status=JobStatus.COMPLETED,
            metadata={"delivery_error": str(exc)},
        )
        return False
