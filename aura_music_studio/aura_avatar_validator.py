from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

_GLB_MAGIC = 0x46546C67
_JSON_CHUNK = 0x4E4F534A
_REQUIRED_STATE_ALIASES = {
    "idle": ("idle", "breathing", "stand"),
    "welcoming": ("welcome", "welcoming", "greet", "greeting"),
    "listening": ("listen", "listening", "attentive"),
    "thinking": ("think", "thinking", "ponder"),
    "speaking": ("speak", "speaking", "talk", "talking"),
    "celebrating": ("celebrate", "celebrating", "happy", "cheer"),
    "warning": ("warn", "warning", "concerned"),
}


def _match_animation(names: list[str], aliases: tuple[str, ...]) -> str | None:
    lowered = [(name, name.lower()) for name in names]
    for alias in aliases:
        for original, lower in lowered:
            if lower == alias:
                return original
        for original, lower in lowered:
            if alias in lower:
                return original
    return None


def _read_glb_json(path: Path, max_json_bytes: int = 16 * 1024 * 1024) -> tuple[dict[str, Any], dict[str, Any]]:
    size = path.stat().st_size
    if size < 20:
        raise ValueError("GLB file is too small to contain a valid header and JSON chunk")
    with path.open("rb") as handle:
        header = handle.read(12)
        magic, version, declared_length = struct.unpack("<III", header)
        if magic != _GLB_MAGIC:
            raise ValueError("GLB magic header is invalid")
        if version != 2:
            raise ValueError(f"Aura requires glTF/GLB version 2; found {version}")
        if declared_length != size:
            raise ValueError(f"GLB declared length {declared_length} does not match file size {size}")
        chunk_header = handle.read(8)
        if len(chunk_header) != 8:
            raise ValueError("GLB JSON chunk header is missing")
        chunk_length, chunk_type = struct.unpack("<II", chunk_header)
        if chunk_type != _JSON_CHUNK:
            raise ValueError("The first GLB chunk must be JSON")
        if chunk_length <= 0 or chunk_length > max_json_bytes:
            raise ValueError("GLB JSON chunk is empty or exceeds the validation limit")
        raw = handle.read(chunk_length)
        if len(raw) != chunk_length:
            raise ValueError("GLB JSON chunk is truncated")
    try:
        document = json.loads(raw.rstrip(b"\x00 \t\r\n").decode("utf-8"))
    except Exception as exc:
        raise ValueError("GLB JSON chunk cannot be decoded") from exc
    if not isinstance(document, dict):
        raise ValueError("GLB JSON root must be an object")
    asset = document.get("asset") or {}
    if not str(asset.get("version") or "").startswith("2"):
        raise ValueError("GLB asset metadata is not glTF 2.x")
    return document, {"file_bytes": size, "glb_version": version, "declared_length": declared_length, "json_chunk_bytes": chunk_length}


def validate_aura_glb(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    result: dict[str, Any] = {
        "exists": target.is_file(),
        "valid_glb": False,
        "glb_version": None,
        "file_bytes": target.stat().st_size if target.is_file() else None,
        "skins": 0,
        "nodes": 0,
        "meshes": 0,
        "animations": [],
        "animation_state_matches": {},
        "morph_target_names": [],
        "morph_target_count": 0,
        "has_skin": False,
        "has_animations": False,
        "has_morph_targets": False,
        "facial_rig_signal": False,
        "warnings": [],
        "error": None,
    }
    if not target.is_file():
        result["error"] = "Aura GLB asset is not installed"
        return result
    if target.suffix.lower() != ".glb":
        result["error"] = "Aura production model must be a .glb file"
        return result
    try:
        document, meta = _read_glb_json(target)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    animations = [str(row.get("name") or f"animation_{index}") for index, row in enumerate(document.get("animations") or [])]
    skins = document.get("skins") or []
    nodes = document.get("nodes") or []
    meshes = document.get("meshes") or []
    morph_names: list[str] = []
    morph_count = 0
    for mesh in meshes:
        extras = mesh.get("extras") or {}
        names = extras.get("targetNames") or []
        for value in names:
            name = str(value or "").strip()
            if name and name not in morph_names:
                morph_names.append(name)
        for primitive in mesh.get("primitives") or []:
            morph_count = max(morph_count, len(primitive.get("targets") or []))

    matches = {
        state: _match_animation(animations, aliases)
        for state, aliases in _REQUIRED_STATE_ALIASES.items()
    }
    lower_morphs = [name.lower() for name in morph_names]
    facial_terms = ("viseme", "mouth", "jaw", "blink", "eye", "brow", "smile", "frown", "aa", "ih", "oh")
    facial_signal = any(any(term in name for term in facial_terms) for name in lower_morphs)

    result.update(meta)
    result.update(
        {
            "valid_glb": True,
            "skins": len(skins),
            "nodes": len(nodes),
            "meshes": len(meshes),
            "animations": animations[:120],
            "animation_state_matches": matches,
            "morph_target_names": morph_names[:200],
            "morph_target_count": max(morph_count, len(morph_names)),
            "has_skin": bool(skins),
            "has_animations": bool(animations),
            "has_morph_targets": bool(morph_count or morph_names),
            "facial_rig_signal": facial_signal,
        }
    )
    warnings: list[str] = []
    if not skins:
        warnings.append("No glTF skin was detected; a humanoid production Aura rig is expected to be skinned")
    if not animations:
        warnings.append("No animation clips were detected")
    missing_states = [state for state, value in matches.items() if not value]
    if missing_states:
        warnings.append("Animation clips were not matched for: " + ", ".join(missing_states))
    if not (morph_count or morph_names):
        warnings.append("No morph targets were detected; production facial/viseme animation is expected")
    elif not facial_signal:
        warnings.append("Morph targets exist but no obvious facial/viseme naming signal was detected")
    result["warnings"] = warnings
    return result


__all__ = ["validate_aura_glb"]
