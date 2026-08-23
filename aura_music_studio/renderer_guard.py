from __future__ import annotations

import os

from .renderer_runtime import AceStepRuntime, validate_real_audio


def resolve_ace_model(requested: str | None, *, task_type: str) -> str:
    """Resolve legacy/configured ACE model names against the live worker inventory."""
    runtime = AceStepRuntime()
    requested = requested or (
        os.getenv("AURA_ACESTEP_TRACK_MODEL", "acestep-v15-base")
        if task_type in {"lego", "extract", "complete"}
        else os.getenv("AURA_ACESTEP_FULL_MODEL", "acestep-v15-turbo")
    )
    return runtime.resolve_model(requested, task_type=task_type)


__all__ = ["resolve_ace_model", "validate_real_audio"]
