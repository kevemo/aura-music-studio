from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_DEFAULT_MAX_AGE_SECONDS = 300
_MAX_EVIDENCE_BYTES = 64 * 1024
_MAX_FUTURE_SKEW_SECONDS = 60


class RuntimeReadinessError(RuntimeError):
    """Raised when a self-hosted creative runtime cannot prove workload readiness."""


@dataclass(frozen=True)
class CreativeRuntimeEvidence:
    engine: str
    model_id: str
    model_digest: str
    runtime_id: str
    runtime_digest: str
    checked_at: datetime
    healthy: bool
    inference_verified: bool
    model_loaded: bool
    storage_ready: bool
    capacity_ready: bool
    recovery_ready: bool
    gpu_required: bool
    gpu_available: bool
    cuda_available: bool

    def provenance_metadata(self) -> dict[str, Any]:
        return {
            "runtime_engine": self.engine,
            "runtime_model_id": self.model_id,
            "runtime_model_digest": self.model_digest,
            "runtime_id": self.runtime_id,
            "runtime_digest": self.runtime_digest,
            "runtime_checked_at": self.checked_at.isoformat(),
            "runtime_inference_verified": self.inference_verified,
            "runtime_gpu_required": self.gpu_required,
            "runtime_gpu_available": self.gpu_available,
            "runtime_cuda_available": self.cuda_available,
            "runtime_workload_ready": True,
            # Deployment/signing/capacity evidence outside this local attestation remains a
            # separate production-readiness concern; this flag is intentionally workload-only.
            "runtime_production_evidenced": False,
        }


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeReadinessError(f"Creative runtime evidence is missing required field: {field}.")
    return value.strip()


def _required_bool(payload: dict[str, Any], field: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        raise RuntimeReadinessError(f"Creative runtime evidence field {field} must be boolean.")
    return value


def _parse_checked_at(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        checked_at = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise RuntimeReadinessError("Creative runtime evidence checked_at is not valid ISO-8601.") from exc
    if checked_at.tzinfo is None:
        raise RuntimeReadinessError("Creative runtime evidence checked_at must include a timezone.")
    return checked_at.astimezone(timezone.utc)


def _max_age_seconds() -> int:
    raw = os.getenv("AURA_CREATIVE_RUNTIME_MAX_EVIDENCE_AGE_SECONDS", str(_DEFAULT_MAX_AGE_SECONDS))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeReadinessError("AURA_CREATIVE_RUNTIME_MAX_EVIDENCE_AGE_SECONDS must be an integer.") from exc
    if value < 30 or value > 3600:
        raise RuntimeReadinessError("Creative runtime evidence maximum age must be between 30 and 3600 seconds.")
    return value


def load_creative_runtime_evidence(
    path: Path | str,
    *,
    expected_engine: str,
    now: datetime | None = None,
) -> CreativeRuntimeEvidence:
    evidence_path = Path(path)
    try:
        stat = evidence_path.stat()
    except OSError as exc:
        raise RuntimeReadinessError("Creative runtime readiness evidence file is unavailable.") from exc
    if not evidence_path.is_file():
        raise RuntimeReadinessError("Creative runtime readiness evidence path is not a file.")
    if stat.st_size <= 0 or stat.st_size > _MAX_EVIDENCE_BYTES:
        raise RuntimeReadinessError("Creative runtime readiness evidence file has an invalid size.")

    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeReadinessError("Creative runtime readiness evidence is not valid UTF-8 JSON.") from exc
    if not isinstance(payload, dict):
        raise RuntimeReadinessError("Creative runtime readiness evidence must be a JSON object.")
    if payload.get("schema_version") != 1:
        raise RuntimeReadinessError("Unsupported creative runtime readiness evidence schema version.")

    engine = _required_text(payload, "engine")
    if engine != expected_engine:
        raise RuntimeReadinessError(
            f"Creative runtime readiness evidence engine mismatch: expected {expected_engine}, got {engine}."
        )

    model_id = _required_text(payload, "model_id")
    model_digest = _required_text(payload, "model_digest").lower()
    runtime_id = _required_text(payload, "runtime_id")
    runtime_digest = _required_text(payload, "runtime_digest").lower()
    if not _SHA256_RE.fullmatch(model_digest):
        raise RuntimeReadinessError("Creative runtime model_digest must be an immutable sha256 digest.")
    if not _SHA256_RE.fullmatch(runtime_digest):
        raise RuntimeReadinessError("Creative runtime runtime_digest must be an immutable sha256 digest.")

    checked_at = _parse_checked_at(_required_text(payload, "checked_at"))
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if checked_at > current + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS):
        raise RuntimeReadinessError("Creative runtime readiness evidence is dated too far in the future.")
    if current - checked_at > timedelta(seconds=_max_age_seconds()):
        raise RuntimeReadinessError("Creative runtime readiness evidence is stale.")

    healthy = _required_bool(payload, "healthy")
    inference_verified = _required_bool(payload, "inference_verified")
    model_loaded = _required_bool(payload, "model_loaded")
    storage_ready = _required_bool(payload, "storage_ready")
    capacity_ready = _required_bool(payload, "capacity_ready")
    recovery_ready = _required_bool(payload, "recovery_ready")
    gpu_required = _required_bool(payload, "gpu_required")
    gpu_available = _required_bool(payload, "gpu_available")
    cuda_available = _required_bool(payload, "cuda_available")

    failed = [
        name
        for name, value in (
            ("healthy", healthy),
            ("inference_verified", inference_verified),
            ("model_loaded", model_loaded),
            ("storage_ready", storage_ready),
            ("capacity_ready", capacity_ready),
            ("recovery_ready", recovery_ready),
        )
        if not value
    ]
    if failed:
        raise RuntimeReadinessError(
            "Creative runtime is configured but not workload-ready: " + ", ".join(failed) + "."
        )
    if gpu_required and (not gpu_available or not cuda_available):
        raise RuntimeReadinessError(
            "Creative runtime requires GPU/CUDA capability but current evidence does not prove it available."
        )

    return CreativeRuntimeEvidence(
        engine=engine,
        model_id=model_id,
        model_digest=model_digest,
        runtime_id=runtime_id,
        runtime_digest=runtime_digest,
        checked_at=checked_at,
        healthy=healthy,
        inference_verified=inference_verified,
        model_loaded=model_loaded,
        storage_ready=storage_ready,
        capacity_ready=capacity_ready,
        recovery_ready=recovery_ready,
        gpu_required=gpu_required,
        gpu_available=gpu_available,
        cuda_available=cuda_available,
    )


def creative_runtime_workload_ready(path: Path | str, *, expected_engine: str) -> bool:
    try:
        load_creative_runtime_evidence(path, expected_engine=expected_engine)
    except RuntimeReadinessError:
        return False
    return True
