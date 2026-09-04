from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .aura_effect_system_creator import EffectNodeSpec, EffectSystemSpec, compile_effect_system, make_effect_system
from .creative_effect_entitlements import CreativeEffectEntitlementStore, store as default_entitlement_store
from .daw import load_session, save_session
from .revisions import create_revision, restore_revision


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _system_root(project: Path) -> Path:
    root = project.resolve() / "work" / "effect_systems"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _spec_from_payload(payload: dict[str, Any]) -> EffectSystemSpec:
    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, list):
        raise ValueError("Effect system nodes must be a list")
    nodes: list[EffectNodeSpec] = []
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            raise ValueError("Each effect node must be an object")
        nodes.append(
            EffectNodeSpec(
                id=str(raw.get("id") or ""),
                catalogue_item_id=str(raw.get("catalogue_item_id") or ""),
                parameters=dict(raw.get("parameters") or {}),
                enabled=bool(raw.get("enabled", True)),
                mix=float(raw.get("mix", 1.0)),
            )
        )
    return make_effect_system(
        str(payload.get("id") or ""),
        str(payload.get("name") or ""),
        nodes,
        description=str(payload.get("description") or ""),
        version=int(payload.get("version") or 1),
    )


def _system_path(project: Path, system_id: str) -> Path:
    # make_effect_system uses the same strict identifier alphabet; validate again before disk use.
    validated = make_effect_system(system_id, "Validation", [EffectNodeSpec(id="n", catalogue_item_id="music.fx.gain")]).id
    root = _system_root(project).resolve()
    target = (root / f"{validated}.json").resolve()
    if root not in target.parents:
        raise ValueError("Effect system path escapes project storage")
    return target


def save_effect_system(project: Path, spec: EffectSystemSpec) -> dict[str, Any]:
    """Persist one reusable, validated effect-system definition inside the member project."""
    compiled = compile_effect_system(spec)
    path = _system_path(project, spec.id)
    existing: dict[str, Any] | None = None
    if path.is_file():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            existing = value if isinstance(value, dict) else None
        except Exception:
            existing = None
    if existing:
        old_version = int(existing.get("system", {}).get("version") or 1)
        old_fingerprint = str(existing.get("fingerprint") or "")
        if old_version > spec.version:
            raise ValueError("Cannot overwrite a newer saved effect-system version")
        if old_version == spec.version and old_fingerprint and old_fingerprint != compiled.fingerprint:
            raise ValueError("Changing a saved effect system requires a version increment")

    payload = {
        "system": spec.public(),
        "fingerprint": compiled.fingerprint,
        "runtime": "ffmpeg_audio",
        "saved_at": _now(),
        "backend_executable": True,
        "source_media_mutated": False,
    }
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
    return {**payload, "saved": True, "project_relative_path": f"work/effect_systems/{path.name}"}


def load_effect_system(project: Path, system_id: str) -> EffectSystemSpec:
    path = _system_path(project, system_id)
    if not path.is_file():
        raise FileNotFoundError(system_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("system"), dict):
        raise ValueError("Saved effect system is invalid")
    spec = _spec_from_payload(payload["system"])
    compiled = compile_effect_system(spec)
    if str(payload.get("fingerprint") or "") != compiled.fingerprint:
        raise ValueError("Saved effect-system integrity check failed")
    return spec


def list_saved_effect_systems(project: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(_system_root(project).glob("*.json"), key=lambda value: value.name.casefold()):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("system"), dict):
                continue
            spec = _spec_from_payload(payload["system"])
            compiled = compile_effect_system(spec)
            if compiled.fingerprint != str(payload.get("fingerprint") or ""):
                continue
            rows.append({
                "id": spec.id,
                "name": spec.name,
                "description": spec.description,
                "version": spec.version,
                "node_count": len(spec.nodes),
                "fingerprint": compiled.fingerprint,
                "runtime": "ffmpeg_audio",
                "backend_executable": True,
            })
        except Exception:
            continue
    return rows


def _entitlement_rows(
    user_id: str,
    spec: EffectSystemSpec,
    entitlement_store: CreativeEffectEntitlementStore,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in spec.nodes:
        if node.catalogue_item_id in seen:
            continue
        seen.add(node.catalogue_item_id)
        rows.append(entitlement_store.has_entitlement(user_id, node.catalogue_item_id))
    return rows


def preview_project_effect_system(
    project: Path,
    track_id: str,
    spec: EffectSystemSpec,
    *,
    user_id: str,
    entitlement_store: CreativeEffectEntitlementStore = default_entitlement_store,
) -> dict[str, Any]:
    session = load_session(project)
    track = session.find_track(track_id)
    compiled = compile_effect_system(spec)
    entitlements = _entitlement_rows(user_id, spec, entitlement_store)
    missing = [row for row in entitlements if not bool(row.get("owned"))]
    return {
        "project": project.name,
        "track_id": track.id,
        "track_name": track.name,
        "system": spec.public(),
        "fingerprint": compiled.fingerprint,
        "ffmpeg_filter_chain": compiled.ffmpeg_filter_chain,
        "effects": [effect.model_dump(mode="json") for effect in compiled.effects],
        "entitlements": entitlements,
        "missing_entitlement_effect_ids": [str(row.get("effect_id") or "") for row in missing],
        "can_apply": not missing,
        "project_mutated": False,
        "source_media_mutated": False,
        "preview_required_before_apply": True,
        "arbitrary_command_execution": False,
    }


def apply_effect_system(
    project: Path,
    track_id: str,
    spec: EffectSystemSpec,
    *,
    user_id: str,
    actor: str = "Studio member",
    keep_revisions: int = 40,
    entitlement_store: CreativeEffectEntitlementStore = default_entitlement_store,
) -> dict[str, Any]:
    """Apply a compiled system to DAW metadata only after entitlement and revision gates pass."""
    preview = preview_project_effect_system(
        project,
        track_id,
        spec,
        user_id=user_id,
        entitlement_store=entitlement_store,
    )
    if not preview["can_apply"]:
        missing = ", ".join(preview["missing_entitlement_effect_ids"])
        raise PermissionError(f"Effect system contains effects that are not owned: {missing}")

    session = load_session(project)
    track = session.find_track(track_id)
    compiled = compile_effect_system(spec)
    history = track.metadata.get("effect_systems")
    if not isinstance(history, list):
        history = []
    for row in history:
        if isinstance(row, dict) and row.get("fingerprint") == compiled.fingerprint:
            return {
                "applied": False,
                "already_applied": True,
                "project": project.name,
                "track_id": track.id,
                "system_id": spec.id,
                "fingerprint": compiled.fingerprint,
                "revision_id": None,
                "source_media_mutated": False,
            }

    # Revision creation is mandatory for this workflow. Fail closed before changing the session.
    revision = create_revision(
        project,
        label=f"Before effect system {spec.name}"[:160],
        reason="effect_system_apply",
        actor=(actor or "Studio member")[:120],
        keep=max(1, int(keep_revisions)),
    )

    effect_ids: list[str] = []
    for node, effect in zip(spec.nodes, compiled.effects, strict=True):
        effect_id = f"system.{compiled.fingerprint[:12]}.{node.id}"
        effect_ids.append(effect_id)
        track.effects.append(effect.model_copy(update={"id": effect_id}))

    history.append({
        "system_id": spec.id,
        "name": spec.name,
        "version": spec.version,
        "fingerprint": compiled.fingerprint,
        "effect_ids": effect_ids,
        "applied_at": _now(),
        "revision_before_apply": revision["id"],
    })
    track.metadata["effect_systems"] = history
    save_session(project, session)
    return {
        "applied": True,
        "already_applied": False,
        "project": project.name,
        "track_id": track.id,
        "system_id": spec.id,
        "fingerprint": compiled.fingerprint,
        "effect_ids": effect_ids,
        "revision_id": revision["id"],
        "source_media_mutated": False,
        "project_metadata_mutated": True,
        "undo_available": True,
    }


def restore_effect_system_revision(project: Path, revision_id: str, *, keep_revisions: int = 40) -> dict[str, Any]:
    result = restore_revision(project, revision_id, create_backup=True, keep=max(1, int(keep_revisions)))
    return {
        **result,
        "effect_system_undo": True,
        "source_media_mutated": False,
    }


__all__ = [
    "apply_effect_system",
    "list_saved_effect_systems",
    "load_effect_system",
    "preview_project_effect_system",
    "restore_effect_system_revision",
    "save_effect_system",
]
