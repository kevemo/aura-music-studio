from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .aura_effect_system_creator import EffectNodeSpec, EffectSystemSpec, compile_effect_system, make_effect_system
from .aura_effect_system_project import (
    _catalogue_provenance_fingerprint,
    _catalogue_provenance_snapshot,
    _prompt_fingerprint,
    _spec_from_payload,
    _validated_saved_provenance,
    load_effect_system,
)

_AUTOSAVE_SCHEMA_VERSION = 1
_MAX_AUTOSAVE_BYTES = 512 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _autosave_root(project: Path, *, create: bool) -> Path:
    project_root = project.resolve()
    candidate = project_root / "work" / "effect_system_autosaves"
    if create:
        candidate.mkdir(parents=True, exist_ok=True)
    root = candidate.resolve()
    if root != project_root and project_root not in root.parents:
        raise ValueError("Effect-system autosave root escapes project storage")
    return root


def _validated_system_id(system_id: str) -> str:
    return make_effect_system(
        system_id,
        "Validation",
        [EffectNodeSpec(id="n", catalogue_item_id="music.fx.gain")],
    ).id


def _autosave_path(project: Path, system_id: str, *, create_root: bool) -> Path:
    validated = _validated_system_id(system_id)
    root = _autosave_root(project, create=create_root)
    target = (root / f"{validated}.json").resolve()
    if root not in target.parents:
        raise ValueError("Effect-system autosave path escapes project storage")
    return target


def _canonical_state(project: Path, system_id: str) -> dict[str, Any]:
    try:
        spec = load_effect_system(project, system_id)
    except FileNotFoundError:
        return {"version": None, "fingerprint": None}
    compiled = compile_effect_system(spec)
    return {"version": spec.version, "fingerprint": compiled.fingerprint}


def _decode_autosave(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path.stem)
    if path.stat().st_size > _MAX_AUTOSAVE_BYTES:
        raise ValueError("Effect-system autosave exceeds the allowed size")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Effect-system autosave is invalid") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("system"), dict):
        raise ValueError("Effect-system autosave is invalid")
    if int(payload.get("autosave_schema_version") or 0) != _AUTOSAVE_SCHEMA_VERSION:
        raise ValueError("Effect-system autosave schema is unsupported")
    if payload.get("recovery_draft") is not True:
        raise ValueError("Effect-system autosave recovery marker is invalid")
    if not isinstance(payload.get("autosaved_at"), str) or not payload["autosaved_at"].strip():
        raise ValueError("Effect-system autosave timestamp is invalid")
    return payload


def save_effect_system_autosave(
    project: Path,
    spec: EffectSystemSpec,
    *,
    source_prompt_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Persist a non-authorizing authoring draft without changing the canonical saved system."""
    prompt_fingerprint = _prompt_fingerprint(source_prompt_fingerprint)
    compiled = compile_effect_system(spec)
    catalogue_provenance = _catalogue_provenance_snapshot(spec)
    catalogue_provenance_fingerprint = _catalogue_provenance_fingerprint(catalogue_provenance)
    baseline = _canonical_state(project, spec.id)
    path = _autosave_path(project, spec.id, create_root=True)

    payload = {
        # Reuse the canonical record schema for provenance validation while keeping the
        # autosave lifecycle explicitly separate from canonical reusable-system saves.
        "record_schema_version": 2,
        "autosave_schema_version": _AUTOSAVE_SCHEMA_VERSION,
        "recovery_draft": True,
        "system": spec.public(),
        "fingerprint": compiled.fingerprint,
        "source_prompt_fingerprint": prompt_fingerprint or None,
        "catalogue_provenance": catalogue_provenance,
        "catalogue_provenance_fingerprint": catalogue_provenance_fingerprint,
        "baseline_saved_version": baseline["version"],
        "baseline_saved_fingerprint": baseline["fingerprint"],
        "runtime": "ffmpeg_audio",
        "autosaved_at": _now(),
        "entitlement_granted": False,
        "execution_authorized": False,
        "canonical_saved_system_mutated": False,
        "project_session_mutated": False,
        "source_media_mutated": False,
    }
    encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    if len(encoded) > _MAX_AUTOSAVE_BYTES:
        raise ValueError("Effect-system autosave exceeds the allowed size")

    temporary = path.with_suffix(".json.tmp")
    temporary.write_bytes(encoded)
    temporary.replace(path)
    return {
        **payload,
        "autosaved": True,
        "project_relative_path": f"work/effect_system_autosaves/{path.name}",
    }


def load_effect_system_autosave(project: Path, system_id: str) -> dict[str, Any]:
    path = _autosave_path(project, system_id, create_root=False)
    payload = _decode_autosave(path)
    _prompt_fingerprint(payload.get("source_prompt_fingerprint"))
    spec = _spec_from_payload(payload["system"])
    if spec.id != _validated_system_id(system_id):
        raise ValueError("Effect-system autosave id does not match its recovery key")
    compiled = compile_effect_system(spec)
    if str(payload.get("fingerprint") or "") != compiled.fingerprint:
        raise ValueError("Effect-system autosave integrity check failed")
    catalogue_provenance, catalogue_provenance_fingerprint = _validated_saved_provenance(payload, spec)

    baseline_version = payload.get("baseline_saved_version")
    if baseline_version is not None:
        baseline_version = int(baseline_version)
    baseline_fingerprint = payload.get("baseline_saved_fingerprint")
    if baseline_fingerprint is not None:
        baseline_fingerprint = str(baseline_fingerprint)
    current = _canonical_state(project, spec.id)
    conflict = baseline_version != current["version"] or baseline_fingerprint != current["fingerprint"]

    return {
        "system": spec.public(),
        "fingerprint": compiled.fingerprint,
        "source_prompt_fingerprint": _prompt_fingerprint(payload.get("source_prompt_fingerprint")) or None,
        "catalogue_provenance": catalogue_provenance,
        "catalogue_provenance_fingerprint": catalogue_provenance_fingerprint,
        "autosaved_at": payload["autosaved_at"],
        "baseline_saved_version": baseline_version,
        "baseline_saved_fingerprint": baseline_fingerprint,
        "current_saved_version": current["version"],
        "current_saved_fingerprint": current["fingerprint"],
        "canonical_conflict": conflict,
        "recovery_available": True,
        "canonical_save_requires_explicit_action": True,
        "safe_to_replace_canonical_without_reconciliation": not conflict,
        "entitlement_granted": False,
        "execution_authorized": False,
        "canonical_saved_system_mutated": False,
        "project_session_mutated": False,
        "source_media_mutated": False,
    }


def discard_effect_system_autosave(project: Path, system_id: str) -> dict[str, Any]:
    path = _autosave_path(project, system_id, create_root=False)
    if not path.is_file():
        raise FileNotFoundError(system_id)
    path.unlink()
    return {
        "system_id": _validated_system_id(system_id),
        "discarded": True,
        "canonical_saved_system_mutated": False,
        "project_session_mutated": False,
        "source_media_mutated": False,
        "entitlement_granted": False,
        "execution_authorized": False,
    }


__all__ = [
    "discard_effect_system_autosave",
    "load_effect_system_autosave",
    "save_effect_system_autosave",
]
