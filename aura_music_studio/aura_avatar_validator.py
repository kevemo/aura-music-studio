from __future__ import annotations

import base64
import binascii
import json
import struct
from pathlib import Path
from typing import Any

_GLB_MAGIC = 0x46546C67
_JSON_CHUNK = 0x4E4F534A
_MAX_EMBEDDED_IMAGE_URI_BYTES = 8 * 1024 * 1024
_ALLOWED_EMBEDDED_IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/ktx2",
    "image/png",
    "image/webp",
}
_REQUIRED_STATE_ALIASES = {
    "idle": ("idle", "breathing", "stand"),
    "welcoming": ("welcome", "welcoming", "greet", "greeting"),
    "listening": ("listen", "listening", "attentive"),
    "thinking": ("think", "thinking", "ponder"),
    "tool_running": ("tool_running", "working", "work", "typing"),
    "speaking": ("speak", "speaking", "talk", "talking"),
    "celebrating": ("celebrate", "celebrating", "happy", "cheer"),
    "warning": ("warn", "warning", "concerned"),
    "recording_coach": ("recording_coach", "recording-coach", "coach"),
    "studio_engineer": ("studio_engineer", "studio-engineer", "engineer"),
}

# Oculus/OVR-style viseme channels are widely used by realtime-avatar pipelines. The
# production Aura rig may use other naming conventions; each channel therefore has a
# bounded alias set rather than one hard-coded spelling. These are animation-clip aliases,
# not a claim that <model-viewer> exposes raw morph-target mutation. The browser runtime
# layers matched clips through the renderer's public appendAnimation/detachAnimation API.
VISEME_ANIMATION_ALIASES: dict[str, tuple[str, ...]] = {
    "sil": ("viseme_sil", "viseme_silence", "silence", "mouth_closed"),
    "pp": ("viseme_pp", "viseme_p", "viseme_b", "viseme_m", "mouth_pp"),
    "ff": ("viseme_ff", "viseme_f", "viseme_v", "mouth_ff"),
    "th": ("viseme_th", "mouth_th"),
    "dd": ("viseme_dd", "viseme_d", "viseme_t", "mouth_dd"),
    "kk": ("viseme_kk", "viseme_k", "viseme_g", "mouth_kk"),
    "ch": ("viseme_ch", "viseme_j", "viseme_sh", "mouth_ch"),
    "ss": ("viseme_ss", "viseme_s", "viseme_z", "mouth_ss"),
    "nn": ("viseme_nn", "viseme_n", "viseme_l", "mouth_nn"),
    "rr": ("viseme_rr", "viseme_r", "mouth_rr"),
    "aa": ("viseme_aa", "viseme_a", "mouth_aa"),
    "e": ("viseme_e", "viseme_ee", "mouth_e"),
    "i": ("viseme_i", "viseme_ih", "mouth_i"),
    "o": ("viseme_o", "viseme_oh", "mouth_o"),
    "u": ("viseme_u", "viseme_ou", "mouth_u"),
}

GAZE_ANIMATION_ALIASES: dict[str, tuple[str, ...]] = {
    "center": ("gaze_center", "look_center", "eyes_center"),
    "left": ("gaze_left", "look_left", "eyes_left"),
    "right": ("gaze_right", "look_right", "eyes_right"),
    "up": ("gaze_up", "look_up", "eyes_up"),
    "down": ("gaze_down", "look_down", "eyes_down"),
}

GESTURE_ANIMATION_ALIASES: dict[str, tuple[str, ...]] = {
    "blink": ("blink", "eye_blink", "blink_both"),
    "nod": ("gesture_nod", "nod", "head_nod"),
    "shake": ("gesture_shake", "head_shake", "shake_head"),
    "wave": ("gesture_wave", "wave", "hand_wave"),
    "open_hands": ("gesture_open_hands", "open_hands", "present"),
    "point": ("gesture_point", "point", "pointing"),
}


def _match_animation(names: list[str], aliases: tuple[str, ...]) -> str | None:
    lowered = [(name, name.lower()) for name in names]
    for alias in aliases:
        alias = alias.lower()
        for original, lower in lowered:
            if lower == alias:
                return original
        for original, lower in lowered:
            if alias in lower:
                return original
    return None


def _match_animation_map(
    names: list[str],
    contract: dict[str, tuple[str, ...]],
) -> dict[str, str | None]:
    return {channel: _match_animation(names, aliases) for channel, aliases in contract.items()}


def _collect_uri_fields(value: Any, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Any]]:
    references: list[tuple[tuple[str, ...], Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = (*path, str(key))
            if str(key) == "uri":
                references.append((child_path, child))
            references.extend(_collect_uri_fields(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            references.extend(_collect_uri_fields(child, (*path, str(index))))
    return references


def _embedded_image_data_uri_error(uri: str) -> str | None:
    if len(uri.encode("utf-8")) > _MAX_EMBEDDED_IMAGE_URI_BYTES:
        return "embedded image data URI exceeds the 8 MiB validation limit"
    header, separator, payload = uri.partition(",")
    if not separator or not header.lower().startswith("data:"):
        return "image URI must be an embedded data URI"
    metadata = [part.strip().lower() for part in header[5:].split(";")]
    mime_type = metadata[0] if metadata else ""
    if mime_type not in _ALLOWED_EMBEDDED_IMAGE_MIME_TYPES:
        return f"embedded image MIME type {mime_type or '<missing>'} is not allowlisted"
    if "base64" not in metadata[1:]:
        return "embedded image data URI must use base64 encoding"
    try:
        decoded = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return "embedded image data URI contains invalid base64 data"
    if not decoded:
        return "embedded image data URI is empty"
    if mime_type == "image/png" and not decoded.startswith(b"\x89PNG\r\n\x1a\n"):
        return "embedded image data does not match its PNG MIME type"
    if mime_type == "image/jpeg" and not decoded.startswith(b"\xff\xd8\xff"):
        return "embedded image data does not match its JPEG MIME type"
    if mime_type == "image/webp" and not (
        len(decoded) >= 12 and decoded.startswith(b"RIFF") and decoded[8:12] == b"WEBP"
    ):
        return "embedded image data does not match its WebP MIME type"
    if mime_type == "image/ktx2" and not decoded.startswith(b"\xabKTX 20\xbb\r\n\x1a\n"):
        return "embedded image data does not match its KTX2 MIME type"
    return None


def _assess_resource_isolation(document: dict[str, Any]) -> dict[str, Any]:
    references = _collect_uri_fields(document)
    errors: list[str] = []
    embedded_image_data_uri_count = 0
    external_resource_count = 0

    for path, value in references:
        location = ".".join(path)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{location} must be a non-empty string")
            continue
        uri = value.strip()
        is_buffer_uri = len(path) == 3 and path[0] == "buffers" and path[2] == "uri"
        is_image_uri = len(path) == 3 and path[0] == "images" and path[2] == "uri"

        if is_buffer_uri:
            external_resource_count += 1
            errors.append(
                f"{location} is not permitted; production GLB buffers must be embedded in the GLB BIN chunk"
            )
            continue
        if is_image_uri and uri.lower().startswith("data:"):
            embedded_image_data_uri_count += 1
            data_error = _embedded_image_data_uri_error(uri)
            if data_error:
                errors.append(f"{location}: {data_error}")
            continue
        if is_image_uri:
            external_resource_count += 1
            errors.append(
                f"{location} is external; production GLB images must use bufferView storage or an allowlisted embedded data URI"
            )
            continue

        external_resource_count += 1
        errors.append(
            f"{location} is an unsupported URI-bearing field; extension and custom resource references fail closed"
        )

    return {
        "resource_uri_count": len(references),
        "embedded_image_data_uri_count": embedded_image_data_uri_count,
        "external_resource_count": external_resource_count,
        "blocked_resource_count": len(errors),
        "import_safe": not errors,
        "import_policy": "self_contained_glb_v1",
        "errors": errors,
    }


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
    return document, {
        "file_bytes": size,
        "glb_version": version,
        "declared_length": declared_length,
        "json_chunk_bytes": chunk_length,
    }


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
        "viseme_animation_matches": {},
        "viseme_animation_coverage": 0,
        "full_viseme_animation_set": False,
        "gaze_animation_matches": {},
        "gaze_animation_ready": False,
        "gesture_animation_matches": {},
        "gesture_animation_ready": False,
        "base_state_animation_ready": False,
        "layered_performance_clips_ready": False,
        "root_bone_signal": False,
        "lod_signal": False,
        "production_rig_ready": False,
        "resource_uri_count": 0,
        "embedded_image_data_uri_count": 0,
        "external_resource_count": 0,
        "blocked_resource_count": 0,
        "import_safe": False,
        "import_policy": "self_contained_glb_v1",
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

    result.update(meta)
    resource_isolation = _assess_resource_isolation(document)
    for field in (
        "resource_uri_count",
        "embedded_image_data_uri_count",
        "external_resource_count",
        "blocked_resource_count",
        "import_safe",
        "import_policy",
    ):
        result[field] = resource_isolation[field]
    if not resource_isolation["import_safe"]:
        details = "; ".join(resource_isolation["errors"][:8])
        result["error"] = f"Unsafe GLB resource references: {details}"
        return result

    animations = [
        str(row.get("name") or f"animation_{index}")
        for index, row in enumerate(document.get("animations") or [])
    ]
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

    state_matches = _match_animation_map(animations, _REQUIRED_STATE_ALIASES)
    viseme_matches = _match_animation_map(animations, VISEME_ANIMATION_ALIASES)
    gaze_matches = _match_animation_map(animations, GAZE_ANIMATION_ALIASES)
    gesture_matches = _match_animation_map(animations, GESTURE_ANIMATION_ALIASES)

    lower_morphs = [name.lower() for name in morph_names]
    facial_terms = (
        "viseme",
        "mouth",
        "jaw",
        "blink",
        "eye",
        "brow",
        "smile",
        "frown",
        "aa",
        "ih",
        "oh",
    )
    facial_signal = any(any(term in name for term in facial_terms) for name in lower_morphs)
    node_names = [str(node.get("name") or "").strip() for node in nodes]
    lowered_nodes = [name.lower() for name in node_names if name]
    root_terms = ("auraroot", "root", "armature", "hips", "pelvis")
    root_bone_signal = any(any(term == name or term in name for term in root_terms) for name in lowered_nodes)
    extensions_used = {str(value) for value in document.get("extensionsUsed") or []}
    lower_mesh_names = [str(mesh.get("name") or "").lower() for mesh in meshes]
    lod_signal = "MSFT_lod" in extensions_used or any("lod0" in name or "lod1" in name for name in lower_mesh_names)

    viseme_coverage = sum(1 for value in viseme_matches.values() if value)
    full_viseme_set = viseme_coverage == len(VISEME_ANIMATION_ALIASES)
    base_state_ready = all(state_matches.values())
    gaze_ready = all(gaze_matches.get(name) for name in ("center", "left", "right", "up", "down"))
    # Blink plus at least two expressive/body gesture clips are required for the layered
    # performance profile. A rig may contain more gestures without changing this contract.
    gesture_non_blink = sum(
        1 for name, value in gesture_matches.items() if name != "blink" and value
    )
    gesture_ready = bool(gesture_matches.get("blink")) and gesture_non_blink >= 2
    layered_performance_ready = bool(full_viseme_set and gaze_ready and gesture_ready)
    production_rig_ready = bool(
        skins
        and animations
        and (morph_count or morph_names)
        and facial_signal
        and base_state_ready
        and layered_performance_ready
    )

    result.update(
        {
            "valid_glb": True,
            "skins": len(skins),
            "nodes": len(nodes),
            "meshes": len(meshes),
            "animations": animations[:240],
            "animation_state_matches": state_matches,
            "morph_target_names": morph_names[:300],
            "morph_target_count": max(morph_count, len(morph_names)),
            "has_skin": bool(skins),
            "has_animations": bool(animations),
            "has_morph_targets": bool(morph_count or morph_names),
            "facial_rig_signal": facial_signal,
            "viseme_animation_matches": viseme_matches,
            "viseme_animation_coverage": viseme_coverage,
            "full_viseme_animation_set": full_viseme_set,
            "gaze_animation_matches": gaze_matches,
            "gaze_animation_ready": gaze_ready,
            "gesture_animation_matches": gesture_matches,
            "gesture_animation_ready": gesture_ready,
            "base_state_animation_ready": base_state_ready,
            "layered_performance_clips_ready": layered_performance_ready,
            "root_bone_signal": root_bone_signal,
            "lod_signal": lod_signal,
            "production_rig_ready": production_rig_ready,
        }
    )

    warnings: list[str] = []
    if not skins:
        warnings.append("No glTF skin was detected; a humanoid production Aura rig is expected to be skinned")
    if not animations:
        warnings.append("No animation clips were detected")
    missing_states = [state for state, value in state_matches.items() if not value]
    if missing_states:
        warnings.append("Animation clips were not matched for: " + ", ".join(missing_states))
    if not (morph_count or morph_names):
        warnings.append("No morph targets were detected; production facial/viseme animation is expected")
    elif not facial_signal:
        warnings.append("Morph targets exist but no obvious facial/viseme naming signal was detected")
    missing_visemes = [name for name, value in viseme_matches.items() if not value]
    if missing_visemes:
        warnings.append("Layered viseme animation clips were not matched for: " + ", ".join(missing_visemes))
    missing_gaze = [name for name, value in gaze_matches.items() if not value]
    if missing_gaze:
        warnings.append("Layered gaze animation clips were not matched for: " + ", ".join(missing_gaze))
    if not gesture_matches.get("blink"):
        warnings.append("A layered blink animation clip was not matched")
    if gesture_non_blink < 2:
        warnings.append("At least two layered gesture animation clips are required for production performance control")
    if not root_bone_signal:
        warnings.append("No obvious root/armature/hips naming signal was detected; document the rig root before production approval")
    if not lod_signal:
        warnings.append("No explicit GLB LOD signal was detected; browser/mobile optimization still requires deployment evidence")
    result["warnings"] = warnings
    return result


__all__ = [
    "validate_aura_glb",
    "VISEME_ANIMATION_ALIASES",
    "GAZE_ANIMATION_ALIASES",
    "GESTURE_ANIMATION_ALIASES",
]
