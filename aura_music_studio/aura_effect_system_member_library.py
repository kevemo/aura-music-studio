from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .aura_effect_system_creator import EffectNodeSpec, EffectSystemSpec, compile_effect_system, make_effect_system
from .aura_effect_system_project import load_effect_system, save_effect_system
from .tenant_storage import projects_root

_LIBRARY_FILE = ".aura_effect_system_library.json"
_LIBRARY_VERSION = 1
_MAX_LIBRARY_ITEMS = 1000
_SAFE_ITEM_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_SAFE_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,39}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _library_path(root: Path | None = None) -> Path:
    base = (root or projects_root()).resolve()
    base.mkdir(parents=True, exist_ok=True)
    target = (base / _LIBRARY_FILE).resolve()
    if target.parent != base:
        raise ValueError("Effect-system library path escapes tenant storage")
    return target


def _clean_item_id(value: str) -> str:
    item_id = str(value or "").strip()
    if not _SAFE_ITEM_ID.fullmatch(item_id) or item_id in {".", ".."}:
        raise ValueError("Invalid reusable effect-system library item id")
    return item_id


def _clean_tags(values: list[str] | tuple[str, ...] | None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        value = " ".join(str(raw or "").split()).strip()
        if not value:
            continue
        if not _SAFE_TAG.fullmatch(value):
            raise ValueError(f"Invalid reusable effect-system tag: {value}")
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            cleaned.append(value)
        if len(cleaned) > 20:
            raise ValueError("Reusable effect-system library supports at most 20 tags per item")
    return cleaned


def _blank_document() -> dict[str, Any]:
    return {"schema_version": _LIBRARY_VERSION, "items": {}}


def _read_document(root: Path | None = None) -> dict[str, Any]:
    path = _library_path(root)
    if not path.is_file():
        return _blank_document()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError("Reusable effect-system library is not valid JSON") from exc
    if not isinstance(payload, dict) or int(payload.get("schema_version") or 0) != _LIBRARY_VERSION:
        raise ValueError("Unsupported reusable effect-system library schema")
    items = payload.get("items")
    if not isinstance(items, dict):
        raise ValueError("Reusable effect-system library items are invalid")
    if len(items) > _MAX_LIBRARY_ITEMS:
        raise ValueError("Reusable effect-system library item limit exceeded")
    return {"schema_version": _LIBRARY_VERSION, "items": dict(items)}


def _write_document(payload: dict[str, Any], root: Path | None = None) -> None:
    path = _library_path(root)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _spec_from_public(payload: dict[str, Any]) -> EffectSystemSpec:
    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, list):
        raise ValueError("Reusable effect-system nodes must be a list")
    nodes: list[EffectNodeSpec] = []
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            raise ValueError("Reusable effect-system node is invalid")
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


def _validated_entry(item_id: str, raw: Any) -> tuple[dict[str, Any], EffectSystemSpec]:
    item_id = _clean_item_id(item_id)
    if not isinstance(raw, dict):
        raise ValueError("Reusable effect-system library entry is invalid")
    if str(raw.get("item_id") or "") != item_id:
        raise ValueError("Reusable effect-system library item id mismatch")
    if raw.get("visibility") != "private":
        raise ValueError("Only private reusable effect-system entries are supported by this backend")
    system = raw.get("system")
    if not isinstance(system, dict):
        raise ValueError("Reusable effect-system definition is missing")
    spec = _spec_from_public(system)
    compiled = compile_effect_system(spec)
    if str(raw.get("fingerprint") or "") != compiled.fingerprint:
        raise ValueError("Reusable effect-system library integrity check failed")
    tags = _clean_tags(raw.get("tags") if isinstance(raw.get("tags"), list) else [])
    normalized = {
        "item_id": item_id,
        "visibility": "private",
        "system": spec.public(),
        "fingerprint": compiled.fingerprint,
        "runtime": "ffmpeg_audio",
        "backend_executable": True,
        "tags": tags,
        "source_project": str(raw.get("source_project") or "")[:120],
        "published_at": str(raw.get("published_at") or ""),
        "updated_at": str(raw.get("updated_at") or ""),
        "marketplace_published": False,
        "sale_enabled": False,
        "source_media_mutated": False,
    }
    return normalized, spec


def publish_project_effect_system(
    project: Path,
    system_id: str,
    *,
    item_id: str | None = None,
    tags: list[str] | tuple[str, ...] | None = None,
    library_root: Path | None = None,
) -> dict[str, Any]:
    """Publish one already-saved project system into the current member's private reusable library.

    This intentionally does not expose a public marketplace or payment path. It closes the reusable
    member-library primitive while keeping public sale/revenue policy behind a separate explicit gate.
    """
    spec = load_effect_system(project, system_id)
    compiled = compile_effect_system(spec)
    item_key = _clean_item_id(f"effect-system.{spec.id}" if item_id is None else item_id)
    normalized_tags = _clean_tags(tags)

    document = _read_document(library_root)
    items = document["items"]
    existing = items.get(item_key)
    if existing is not None:
        current, _ = _validated_entry(item_key, existing)
        current_version = int(current["system"]["version"])
        if current_version > spec.version:
            raise ValueError("Cannot publish an older reusable effect-system version over a newer one")
        if current_version == spec.version and current["fingerprint"] != compiled.fingerprint:
            raise ValueError("Changed reusable effect-system content requires a version increment")
        published_at = current.get("published_at") or _now()
        if current["fingerprint"] == compiled.fingerprint and current["tags"] == normalized_tags:
            return {**current, "published": False, "already_current": True}
    else:
        if len(items) >= _MAX_LIBRARY_ITEMS:
            raise ValueError("Reusable effect-system library item limit exceeded")
        published_at = _now()

    now = _now()
    entry = {
        "item_id": item_key,
        "visibility": "private",
        "system": spec.public(),
        "fingerprint": compiled.fingerprint,
        "runtime": "ffmpeg_audio",
        "backend_executable": True,
        "tags": normalized_tags,
        "source_project": project.name[:120],
        "published_at": published_at,
        "updated_at": now,
        "marketplace_published": False,
        "sale_enabled": False,
        "source_media_mutated": False,
    }
    items[item_key] = entry
    _write_document(document, library_root)
    return {**entry, "published": True, "already_current": False}


def list_reusable_effect_systems(*, library_root: Path | None = None) -> list[dict[str, Any]]:
    document = _read_document(library_root)
    rows: list[dict[str, Any]] = []
    for item_id in sorted(document["items"], key=str.casefold):
        try:
            entry, _ = _validated_entry(item_id, document["items"][item_id])
        except Exception:
            continue
        rows.append(entry)
    return rows


def load_reusable_effect_system(item_id: str, *, library_root: Path | None = None) -> EffectSystemSpec:
    item_key = _clean_item_id(item_id)
    document = _read_document(library_root)
    if item_key not in document["items"]:
        raise FileNotFoundError(item_key)
    _, spec = _validated_entry(item_key, document["items"][item_key])
    return spec


def import_reusable_effect_system(
    target_project: Path,
    item_id: str,
    *,
    library_root: Path | None = None,
) -> dict[str, Any]:
    """Copy a private reusable definition into another project using the normal project save gate."""
    spec = load_reusable_effect_system(item_id, library_root=library_root)
    result = save_effect_system(target_project, spec)
    return {
        **result,
        "imported_from_reusable_library": True,
        "library_item_id": _clean_item_id(item_id),
        "source_media_mutated": False,
    }


def remove_reusable_effect_system(item_id: str, *, library_root: Path | None = None) -> dict[str, Any]:
    item_key = _clean_item_id(item_id)
    document = _read_document(library_root)
    if item_key not in document["items"]:
        return {"removed": False, "already_absent": True, "item_id": item_key}
    _validated_entry(item_key, document["items"][item_key])
    del document["items"][item_key]
    _write_document(document, library_root)
    return {"removed": True, "already_absent": False, "item_id": item_key}


__all__ = [
    "import_reusable_effect_system",
    "list_reusable_effect_systems",
    "load_reusable_effect_system",
    "publish_project_effect_system",
    "remove_reusable_effect_system",
]
