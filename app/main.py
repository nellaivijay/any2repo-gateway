"""Any2Repo-Gateway — FastAPI application entry point.

A lightweight HTTP control plane that routes paper-to-repo conversion
requests to the appropriate execution engine (Research2Repo or
Quant2Repo) running on GCP Vertex AI or AWS Bedrock.

No LangChain.  No LangGraph.  Just FastAPI + boto3 + Vertex AI SDK.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.middleware.auth import TenantAuthMiddleware, seed_default_tenant
from app.routers import jobs, tenants

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    logger.info("Starting %s (%s)", settings.app_name, settings.environment)
    seed_default_tenant()

    # Pre-warm Workload Identity Federation if AWS role is configured
    if settings.aws_role_arn:
        try:
            from app.iam import write_web_identity_token_file
            write_web_identity_token_file()
        except Exception as exc:
            logger.warning("WIF pre-warm failed (non-fatal): %s", exc)

    yield
    logger.info("Shutting down %s", settings.app_name)


app = FastAPI(
    title="Any2Repo Gateway",
    version="1.0.0",
    description=(
        "Control-plane gateway for Research2Repo and Quant2Repo engines. "
        "Routes conversion jobs to GCP Vertex AI or AWS Bedrock."
    ),
    lifespan=lifespan,
)

# ── Middleware ────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(TenantAuthMiddleware)

# ── Routers ──────────────────────────────────────────────────────────────

app.include_router(jobs.router)
app.include_router(tenants.router)


# ── Root / Health ────────────────────────────────────────────────────────

@app.get("/", tags=["health"])
async def root():
    """Root endpoint — basic service info."""
    return {
        "service": settings.app_name,
        "version": "1.0.0",
        "environment": settings.environment,
        "engines": ["research2repo", "quant2repo"],
        "backends": ["gcp_vertex", "aws_bedrock"],
    }


@app.get("/health", tags=["health"])
async def health():
    """Health check for load balancers and orchestration."""
    return {"status": "healthy"}
