"""Any2Repo-Gateway — FastAPI application entry point.

A lightweight HTTP control plane that routes paper-to-repo conversion
requests to the appropriate execution engine (Research2Repo, Quant2Repo,
or any pluggable engine) running on GCP Vertex AI, AWS Bedrock, Azure ML,
or on-premise infrastructure.

No LangChain.  No LangGraph.  Just FastAPI + cloud SDKs.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.engine_manifest import init_manifests, list_manifests
from app.engine_registry import list_supported_backends
from app.middleware.auth import TenantAuthMiddleware, seed_default_tenant
from app.routers import engines, jobs, tenants, webhooks
from app.store import BaseStore, create_store

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Singleton state store ────────────────────────────────────────────────

_store: Optional[BaseStore] = None


def get_store() -> BaseStore:
    """Return the global state store (lazy-initialised on first call).

    This is the canonical way to obtain the store instance from routers,
    middleware, and other modules.  All of them use a deferred import:
        ``from app.main import get_store``
    """
    global _store
    if _store is None:
        _store = create_store(settings.store_backend)
        logger.info("Initialised state store: %s", settings.store_backend)
    return _store


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    logger.info("Starting %s (%s)", settings.app_name, settings.environment)

    # Ensure the store is created before anything else touches it
    get_store()
    seed_default_tenant()

    # Load engine manifests (built-in + plugin directory)
    init_manifests(settings.engine_manifests_dir)
    manifests = list_manifests()
    logger.info("Loaded %d engine manifest(s)", len(manifests))

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
    version="2.0.0",
    description=(
        "Control-plane gateway for Research2Repo, Quant2Repo, and pluggable "
        "engines. Routes conversion jobs to GCP Vertex AI, AWS Bedrock, "
        "Azure ML, or on-premise infrastructure."
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
app.include_router(engines.router)
app.include_router(webhooks.router)


# ── Root / Health ────────────────────────────────────────────────────────

@app.get("/", tags=["health"])
async def root():
    """Root endpoint — basic service info."""
    manifests = list_manifests()
    return {
        "service": settings.app_name,
        "version": "2.0.0",
        "environment": settings.environment,
        "engines": [m.engine_id for m in manifests],
        "backends": list_supported_backends(),
    }


@app.get("/health", tags=["health"])
async def health():
    """Health check for load balancers and orchestration."""
    return {"status": "healthy"}
