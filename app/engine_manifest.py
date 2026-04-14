"""Engine manifest loader — discovers and registers plugin engines at startup.

Scans a directory of JSON manifest files (``ENGINE_MANIFESTS_DIR``) and
registers each valid engine with the engine registry.  Also provides
built-in manifests for Research2Repo and Quant2Repo.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from app.models.schemas import (
    CloudBackend,
    EngineCapability,
    EngineManifest,
)

logger = logging.getLogger(__name__)

# ── Built-in manifests ───────────────────────────────────────────────────

BUILTIN_MANIFESTS: dict[str, EngineManifest] = {
    "research2repo": EngineManifest(
        engine_id="research2repo",
        version="2.0.0",
        display_name="Research2Repo",
        description="Convert ML/AI research papers into fully functional repositories",
        capabilities=[
            EngineCapability.PDF_INPUT,
            EngineCapability.TEXT_INPUT,
            EngineCapability.GITHUB_OUTPUT,
            EngineCapability.LOCAL_OUTPUT,
            EngineCapability.STREAMING_LOGS,
            EngineCapability.INCREMENTAL_VALIDATION,
        ],
        accepted_inputs=["pdf_url", "pdf_base64", "paper_text"],
        container_image="any2repo/research2repo:latest",
        entrypoint=["python", "-m", "main"],
        supported_backends=[
            CloudBackend.GCP_VERTEX,
            CloudBackend.AWS_BEDROCK,
            CloudBackend.AZURE_ML,
            CloudBackend.ON_PREM,
        ],
        cpu_request="4",
        memory_request="16Gi",
        timeout_seconds=3600,
    ),
    "quant2repo": EngineManifest(
        engine_id="quant2repo",
        version="2.0.0",
        display_name="Quant2Repo",
        description="Convert quantitative finance papers into trading strategy repositories",
        capabilities=[
            EngineCapability.PDF_INPUT,
            EngineCapability.TEXT_INPUT,
            EngineCapability.CATALOG_INPUT,
            EngineCapability.GITHUB_OUTPUT,
            EngineCapability.LOCAL_OUTPUT,
            EngineCapability.STREAMING_LOGS,
            EngineCapability.INCREMENTAL_VALIDATION,
        ],
        accepted_inputs=["pdf_url", "pdf_base64", "paper_text", "catalog_id"],
        container_image="any2repo/quant2repo:latest",
        entrypoint=["python", "-m", "main"],
        supported_backends=[
            CloudBackend.GCP_VERTEX,
            CloudBackend.AWS_BEDROCK,
            CloudBackend.AZURE_ML,
            CloudBackend.ON_PREM,
        ],
        cpu_request="4",
        memory_request="16Gi",
        timeout_seconds=3600,
    ),
}

# ── Registry of all known manifests ──────────────────────────────────────

_MANIFESTS: dict[str, EngineManifest] = {}


def get_manifest(engine_id: str) -> Optional[EngineManifest]:
    """Look up a manifest by engine ID."""
    return _MANIFESTS.get(engine_id)


def list_manifests() -> list[EngineManifest]:
    """Return all registered engine manifests."""
    return list(_MANIFESTS.values())


def register_manifest(manifest: EngineManifest) -> None:
    """Register an engine manifest."""
    _MANIFESTS[manifest.engine_id] = manifest
    logger.info(
        "Registered engine: %s v%s (%s)",
        manifest.engine_id, manifest.version, manifest.display_name,
    )


# ── Loader ───────────────────────────────────────────────────────────────


def load_builtin_manifests() -> None:
    """Register the built-in Research2Repo and Quant2Repo manifests."""
    for manifest in BUILTIN_MANIFESTS.values():
        register_manifest(manifest)


def load_manifests_from_dir(manifests_dir: str) -> int:
    """Scan a directory for ``engine-manifest.json`` files and register them.

    Args:
        manifests_dir: Path to directory containing manifest JSON files.

    Returns:
        Number of manifests successfully loaded.
    """
    path = Path(manifests_dir)
    if not path.is_dir():
        logger.warning("Engine manifests directory does not exist: %s", manifests_dir)
        return 0

    loaded = 0
    for manifest_file in sorted(path.glob("*.json")):
        try:
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
            manifest = EngineManifest(**data)

            if manifest.engine_id in _MANIFESTS:
                logger.info(
                    "Overriding manifest for %s with %s",
                    manifest.engine_id, manifest_file,
                )

            register_manifest(manifest)
            loaded += 1

        except Exception as exc:
            logger.error("Failed to load manifest %s: %s", manifest_file, exc)

    logger.info("Loaded %d engine manifest(s) from %s", loaded, manifests_dir)
    return loaded


def init_manifests(manifests_dir: str = "") -> None:
    """Initialize all engine manifests (called at app startup).

    Loads built-in manifests first, then overrides/extends with any
    manifests found in the specified directory.
    """
    load_builtin_manifests()
    if manifests_dir:
        load_manifests_from_dir(manifests_dir)
