"""Protocol conformance test utilities.

Provides helpers that engine developers can use to validate their
engine manifest and on-prem HTTP API against the Any2Repo Engine
Protocol v1.0 specification (see ``docs/engine_protocol.md``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from app.models.schemas import EngineCapability, EngineManifest, CloudBackend


# ── Manifest validation ──────────────────────────────────────────────────

_REQUIRED_MANIFEST_FIELDS = {"engine_id", "protocol_version"}

_VALID_CAPABILITIES = {e.value for e in EngineCapability}
_VALID_BACKENDS = {e.value for e in CloudBackend}
_VALID_INPUTS = {"pdf_url", "pdf_base64", "paper_text", "catalog_id"}


def validate_manifest(data: dict[str, Any]) -> list[str]:
    """Validate a raw manifest dict against the protocol spec.

    Returns a list of error strings.  An empty list means the manifest
    is conformant.
    """
    errors: list[str] = []

    # Required fields
    for field in _REQUIRED_MANIFEST_FIELDS:
        if field not in data or not data[field]:
            errors.append(f"Missing required field: {field}")

    # Protocol version
    pv = data.get("protocol_version", "")
    if pv and pv not in ("1.0",):
        errors.append(f"Unsupported protocol_version: {pv} (expected '1.0')")

    # engine_id format
    eid = data.get("engine_id", "")
    if eid and not eid.replace("-", "").replace("_", "").isalnum():
        errors.append(
            f"engine_id '{eid}' should be alphanumeric with hyphens/underscores"
        )

    # Capabilities
    for cap in data.get("capabilities", []):
        if cap not in _VALID_CAPABILITIES:
            errors.append(f"Unknown capability: {cap}")

    # Accepted inputs
    for inp in data.get("accepted_inputs", []):
        if inp not in _VALID_INPUTS:
            errors.append(f"Unknown accepted_input: {inp}")

    # Supported backends
    for backend in data.get("supported_backends", []):
        if backend not in _VALID_BACKENDS:
            errors.append(f"Unknown backend: {backend}")

    # At least one input and one backend
    if not data.get("accepted_inputs"):
        errors.append("Engine must declare at least one accepted_input")
    if not data.get("supported_backends"):
        errors.append("Engine must declare at least one supported_backend")

    return errors


def validate_manifest_file(path: str | Path) -> list[str]:
    """Load and validate a manifest JSON file."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"Cannot read manifest file: {exc}"]
    return validate_manifest(data)


def validate_manifest_model(manifest: EngineManifest) -> list[str]:
    """Validate an already-parsed EngineManifest model."""
    return validate_manifest(manifest.model_dump())


# ── Status file validation ───────────────────────────────────────────────

_REQUIRED_STATUS_FIELDS = {"job_id", "status"}
_VALID_STATUSES = {"completed", "failed"}


def validate_status_file(data: dict[str, Any]) -> list[str]:
    """Validate a ``.any2repo_status.json`` file against the protocol.

    Returns a list of error strings.
    """
    errors: list[str] = []

    for field in _REQUIRED_STATUS_FIELDS:
        if field not in data or not data[field]:
            errors.append(f"Missing required field: {field}")

    status = data.get("status", "")
    if status and status not in _VALID_STATUSES:
        errors.append(
            f"Invalid status '{status}': must be 'completed' or 'failed'"
        )

    if status == "failed" and not data.get("error"):
        errors.append("Failed status must include an 'error' field")

    if "elapsed_seconds" in data:
        try:
            float(data["elapsed_seconds"])
        except (TypeError, ValueError):
            errors.append("elapsed_seconds must be numeric")

    if "files_generated" in data:
        try:
            int(data["files_generated"])
        except (TypeError, ValueError):
            errors.append("files_generated must be an integer")

    return errors


# ── On-prem health check validation ─────────────────────────────────────


def validate_health_response(data: dict[str, Any]) -> list[str]:
    """Validate a health-check response from an on-prem engine."""
    errors: list[str] = []

    if data.get("status") != "healthy":
        errors.append(f"Expected status='healthy', got '{data.get('status')}'")

    if not data.get("engine_id"):
        errors.append("Health response must include engine_id")

    return errors


# ── Convenience: full conformance check ──────────────────────────────────


def check_conformance(
    manifest: dict[str, Any],
    status_file: Optional[dict[str, Any]] = None,
    health_response: Optional[dict[str, Any]] = None,
) -> dict[str, list[str]]:
    """Run all applicable conformance checks.

    Returns a dict mapping check name to list of errors.
    """
    results: dict[str, list[str]] = {
        "manifest": validate_manifest(manifest),
    }
    if status_file is not None:
        results["status_file"] = validate_status_file(status_file)
    if health_response is not None:
        results["health"] = validate_health_response(health_response)
    return results
