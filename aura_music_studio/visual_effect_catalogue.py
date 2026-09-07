from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .plans import AI_FX_DESIGNER, AUTOMATION, BASIC_TIMELINE
from .professional_editor import EditorEffect, ProfessionalEditorStore
from .tenant_storage import project_path

router = APIRouter(prefix="/creative", tags=["Visual Effects Catalogue"])


@dataclass(frozen=True)
class ParameterSpec:
    kind: Literal["float", "int", "color"]
    default: float | int | str
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True)
class EffectSpec:
    effect_id: str
    name: str
    category: str
    media: tuple[Literal["image", "video"], ...]
    runtime: Literal["editor_effect", "item_color"]
    runtime_type: str
    parameters: dict[str, ParameterSpec]
    supports_keyframes: bool = True
    scopes: tuple[Literal["item", "track"], ...] = ("item",)
    premium: bool = True


def _p_float(default: float, minimum: float, maximum: float) -> ParameterSpec:
    return ParameterSpec("float", default, minimum, maximum)


def _p_int(default: int, minimum: int, maximum: int) -> ParameterSpec:
    return ParameterSpec("int", default, float(minimum), float(maximum))


def _p_color(default: str) -> ParameterSpec:
    return ParameterSpec("color", default)


EFFECTS: dict[str, EffectSpec] = {
    "image.blur.gaussian": EffectSpec(
        "image.blur.gaussian", "Gaussian Blur", "blur", ("image",), "editor_effect", "gaussian_blur",
        {"radius": _p_float(4.0, 0.0, 250.0)}, scopes=("item", "track"),
    ),
    "image.sharpen.unsharp": EffectSpec(
        "image.sharpen.unsharp", "Unsharp Mask", "sharpen", ("image",), "editor_effect", "unsharp_mask",
        {"radius": _p_float(2.0, 0.1, 50.0), "percent": _p_int(150, 0, 500), "threshold": _p_int(3, 0, 255)},
        scopes=("item", "track"),
    ),
    "image.filter.grayscale": EffectSpec(
        "image.filter.grayscale", "Grayscale", "stylise", ("image",), "editor_effect", "grayscale", {},
        scopes=("item", "track"),
    ),
    "image.filter.invert": EffectSpec(
        "image.filter.invert", "Invert", "stylise", ("image",), "editor_effect", "invert", {},
        scopes=("item", "track"),
    ),
    "image.filter.sepia": EffectSpec(
        "image.filter.sepia", "Sepia", "stylise", ("image",), "editor_effect", "sepia", {},
        scopes=("item", "track"),
    ),
    "image.color.brightness": EffectSpec(
        "image.color.brightness", "Brightness", "colour", ("image",), "editor_effect", "brightness",
        {"factor": _p_float(1.15, 0.0, 4.0)}, scopes=("item", "track"),
    ),
    "image.color.contrast": EffectSpec(
        "image.color.contrast", "Contrast", "colour", ("image",), "editor_effect", "contrast",
        {"factor": _p_float(1.15, 0.0, 4.0)}, scopes=("item", "track"),
    ),
    "image.color.saturation": EffectSpec(
        "image.color.saturation", "Saturation", "colour", ("image",), "editor_effect", "saturation",
        {"factor": _p_float(1.15, 0.0, 4.0)}, scopes=("item", "track"),
    ),
    "image.light.vignette": EffectSpec(
        "image.light.vignette", "Vignette", "light", ("image",), "editor_effect", "vignette",
        {"strength": _p_float(0.45, 0.0, 1.0)}, scopes=("item", "track"),
    ),
    "image.stylise.pixelate": EffectSpec(
        "image.stylise.pixelate", "Pixelate", "stylise", ("image",), "editor_effect", "pixelate",
        {"block": _p_int(12, 1, 256)}, scopes=("item", "track"),
    ),
    "image.filter.cinematic": EffectSpec(
        "image.filter.cinematic", "Cinematic Grade", "colour", ("image",), "editor_effect", "image.filter.cinematic",
        {"strength": _p_float(0.7, 0.0, 1.0)}, scopes=("item", "track"),
    ),
    "image.filter.duotone": EffectSpec(
        "image.filter.duotone", "Duotone", "colour", ("image",), "editor_effect", "image.filter.duotone",
        {"shadow": _p_color("#111111"), "highlight": _p_color("#f2c86f")}, supports_keyframes=False,
        scopes=("item", "track"),
    ),
    "video.color.exposure": EffectSpec(
        "video.color.exposure", "Exposure", "colour", ("video",), "item_color", "exposure",
        {"value": _p_float(0.0, -3.0, 3.0)}, scopes=("item",),
    ),
    "video.color.brightness": EffectSpec(
        "video.color.brightness", "Brightness", "colour", ("video",), "item_color", "brightness",
        {"value": _p_float(0.0, -1.0, 1.0)}, scopes=("item",),
    ),
    "video.color.contrast": EffectSpec(
        "video.color.contrast", "Contrast", "colour", ("video",), "item_color", "contrast",
        {"value": _p_float(1.0, 0.0, 3.0)}, scopes=("item",),
    ),
    "video.color.saturation": EffectSpec(
        "video.color.saturation", "Saturation", "colour", ("video",), "item_color", "saturation",
        {"value": _p_float(1.0, 0.0, 3.0)}, scopes=("item",),
    ),
    "video.color.gamma": EffectSpec(
        "video.color.gamma", "Gamma", "colour", ("video",), "item_color", "gamma",
        {"value": _p_float(1.0, 0.1, 4.0)}, scopes=("item",),
    ),
    "video.color.temperature": EffectSpec(
        "video.color.temperature", "Temperature", "colour", ("video",), "item_color", "temperature",
        {"value": _p_float(0.0, -1.0, 1.0)}, scopes=("item",),
    ),
    "video.color.tint": EffectSpec(
        "video.color.tint", "Tint", "colour", ("video",), "item_color", "tint",
        {"value": _p_float(0.0, -1.0, 1.0)}, scopes=("item",),
    ),
}

_KEYWORDS = {
    "blur": "image.blur.gaussian",
    "gaussian": "image.blur.gaussian",
    "sharpen": "image.sharpen.unsharp",
    "unsharp": "image.sharpen.unsharp",
    "black and white": "image.filter.grayscale",
    "grayscale": "image.filter.grayscale",
    "greyscale": "image.filter.grayscale",
    "invert": "image.filter.invert",
    "sepia": "image.filter.sepia",
    "vignette": "image.light.vignette",
    "pixelate": "image.stylise.pixelate",
    "pixel": "image.stylise.pixelate",
    "cinematic": "image.filter.cinematic",
    "duotone": "image.filter.duotone",
    "temperature": "video.color.temperature",
    "tint": "video.color.tint",
    "gamma": "video.color.gamma",
    "exposure": "video.color.exposure",
    "brightness": "image.color.brightness",
    "contrast": "image.color.contrast",
    "saturation": "image.color.saturation",
}

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_SYSTEMS_FILENAME = "visual_effect_systems.json"


class EffectCompileRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    media_kind: Literal["image", "video"]
    parameters: dict[str, Any] = Field(default_factory=dict)
    mix: float = Field(default=1.0, ge=0.0, le=1.0)
    keyframes: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class EffectApplyRequest(BaseModel):
    effect_id: str = Field(min_length=1, max_length=160)
    parameters: dict[str, Any] = Field(default_factory=dict)
    mix: float = Field(default=1.0, ge=0.0, le=1.0)
    keyframes: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class SaveEffectSystemRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    graph: dict[str, Any]


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    if not member.plan.has(BASIC_TIMELINE):
        raise HTTPException(403, "Professional visual effects unlock on the Basic membership tier")
    return member


def _require_advanced(member) -> None:
    if not member.plan.has(AUTOMATION):
        raise HTTPException(403, "Executable visual effects require Pro")


def _require_designer(member) -> None:
    if not member.plan.has(AI_FX_DESIGNER):
        raise HTTPException(403, "Aura/Rhiannon Effect & System Creator requires Pro")


def _actor(member) -> str:
    user = getattr(member, "user", {}) or {}
    return str(user.get("display_name") or user.get("email") or "Studio Member")[:160]


def _project(project_name: str) -> Path:
    try:
        return project_path(project_name, must_exist=True)
    except ValueError as exc:
        raise HTTPException(400, "Invalid project path") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Project not found") from exc


def _store(project_name: str) -> ProfessionalEditorStore:
    project = _project(project_name)
    store = ProfessionalEditorStore(project)
    if not store.exists():
        raise HTTPException(404, "Professional editor is not initialized for this project")
    return store


def _public_parameter(name: str, spec: ParameterSpec) -> dict[str, Any]:
    return {
        "name": name,
        "kind": spec.kind,
        "default": spec.default,
        "minimum": spec.minimum,
        "maximum": spec.maximum,
    }


def _public_effect(spec: EffectSpec) -> dict[str, Any]:
    return {
        "id": spec.effect_id,
        "name": spec.name,
        "category": spec.category,
        "media": list(spec.media),
        "runtime": spec.runtime,
        "runtime_type": spec.runtime_type,
        "parameters": [_public_parameter(name, value) for name, value in spec.parameters.items()],
        "supports_keyframes": spec.supports_keyframes,
        "scopes": list(spec.scopes),
        "premium": spec.premium,
        "executable": True,
    }


def _finite(value: Any, *, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _normalize_color(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if len(text) == 4 and text.startswith("#"):
        text = "#" + "".join(ch * 2 for ch in text[1:])
    if not _HEX.fullmatch(text):
        raise ValueError(f"{field} must be a #RRGGBB colour")
    return text.lower()


def normalize_effect_parameters(effect_id: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = EFFECTS.get(str(effect_id or "").strip())
    if spec is None:
        raise ValueError("Unknown or non-executable visual effect")
    supplied = dict(parameters or {})
    unknown = sorted(set(supplied) - set(spec.parameters))
    if unknown:
        raise ValueError(f"Unsupported parameter(s) for {effect_id}: {', '.join(unknown)}")
    normalized: dict[str, Any] = {}
    for name, rule in spec.parameters.items():
        value = supplied.get(name, rule.default)
        if rule.kind == "color":
            normalized[name] = _normalize_color(value, field=name)
            continue
        number = _finite(value, field=name)
        assert rule.minimum is not None and rule.maximum is not None
        if number < rule.minimum or number > rule.maximum:
            raise ValueError(f"{name} must be between {rule.minimum:g} and {rule.maximum:g}")
        normalized[name] = int(round(number)) if rule.kind == "int" else number
    return normalized


def normalize_keyframes(spec: EffectSpec, keyframes: dict[str, list[dict[str, Any]]] | None) -> dict[str, list[dict[str, Any]]]:
    raw = dict(keyframes or {})
    if raw and not spec.supports_keyframes:
        raise ValueError(f"{spec.name} does not support keyframes")
    allowed = set(spec.parameters) | {"mix"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"Unsupported keyframe parameter(s): {', '.join(unknown)}")
    normalized: dict[str, list[dict[str, Any]]] = {}
    for parameter, points in raw.items():
        if len(points) > 4096:
            raise ValueError("A visual effect parameter may have at most 4096 keyframes")
        cleaned = []
        for point in points:
            if not isinstance(point, dict):
                raise ValueError("Keyframes must be objects")
            time = _finite(point.get("time"), field="keyframe time")
            if time < 0.0 or time > 86400.0:
                raise ValueError("Keyframe time must be between 0 and 86400 seconds")
            interpolation = str(point.get("interpolation") or "linear").strip().lower()
            if interpolation not in {"linear", "hold", "smooth", "bezier"}:
                raise ValueError("Unsupported keyframe interpolation")
            value = point.get("value")
            if parameter == "mix":
                number = _finite(value, field="mix")
                if number < 0.0 or number > 1.0:
                    raise ValueError("mix keyframes must be between 0 and 1")
                clean_value: Any = number
            else:
                rule = spec.parameters[parameter]
                if rule.kind == "color":
                    raise ValueError("Colour parameters are not keyframeable")
                number = _finite(value, field=parameter)
                assert rule.minimum is not None and rule.maximum is not None
                if number < rule.minimum or number > rule.maximum:
                    raise ValueError(f"{parameter} keyframe is outside the executable range")
                clean_value = int(round(number)) if rule.kind == "int" else number
            cleaned.append({"time": time, "value": clean_value, "interpolation": interpolation})
        cleaned.sort(key=lambda row: row["time"])
        normalized[parameter] = cleaned
    return normalized


def compile_effect_graph(
    prompt: str,
    media_kind: Literal["image", "video"],
    *,
    parameters: dict[str, Any] | None = None,
    mix: float = 1.0,
    keyframes: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    text = " ".join(str(prompt or "").strip().lower().split())
    candidates = [
        effect_id
        for keyword, effect_id in _KEYWORDS.items()
        if keyword in text and media_kind in EFFECTS[effect_id].media
    ]
    candidates = list(dict.fromkeys(candidates))
    if not candidates:
        raise ValueError("No executable visual effect could be resolved from that prompt")
    if len(candidates) > 1:
        raise ValueError(f"Prompt is ambiguous across executable effects: {', '.join(candidates)}")
    effect_id = candidates[0]
    spec = EFFECTS[effect_id]
    normalized_parameters = normalize_effect_parameters(effect_id, parameters)
    normalized_keyframes = normalize_keyframes(spec, keyframes)
    mix_value = _finite(mix, field="mix")
    if mix_value < 0.0 or mix_value > 1.0:
        raise ValueError("mix must be between 0 and 1")
    return {
        "schema_version": 1,
        "graph_type": "visual_effect",
        "media_kind": media_kind,
        "nodes": [
            {
                "id": f"fx_{uuid4().hex}",
                "effect_id": effect_id,
                "runtime": spec.runtime,
                "runtime_type": spec.runtime_type,
                "parameters": normalized_parameters,
                "mix": mix_value,
                "keyframes": normalized_keyframes,
            }
        ],
        "validated": True,
        "executable": True,
        "arbitrary_code": False,
        "arbitrary_ffmpeg_arguments": False,
        "shell_execution": False,
    }


def _system_path(project: Path) -> Path:
    return project / _SYSTEMS_FILENAME


def _load_systems(project: Path) -> list[dict[str, Any]]:
    path = _system_path(project)
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Visual effect system store is invalid")
    return payload


def _save_systems(project: Path, systems: list[dict[str, Any]]) -> None:
    path = _system_path(project)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(systems, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


@router.get("/visual-effects/catalogue")
def visual_effect_catalogue(request: Request, media_kind: Literal["image", "video"] | None = None):
    member = _member(request)
    rows = [
        _public_effect(spec)
        for spec in EFFECTS.values()
        if media_kind is None or media_kind in spec.media
    ]
    return {
        "plan": member.plan.id,
        "effects": rows,
        "count": len(rows),
        "catalogue_truth": "Only effects with a bounded executable runtime are returned.",
        "arbitrary_plugins_supported": False,
        "arbitrary_ffmpeg_arguments_supported": False,
    }


@router.post("/visual-effects/compile")
def compile_visual_effect(body: EffectCompileRequest, request: Request):
    member = _member(request)
    _require_designer(member)
    try:
        graph = compile_effect_graph(
            body.prompt,
            body.media_kind,
            parameters=body.parameters,
            mix=body.mix,
            keyframes=body.keyframes,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"graph": graph, "editable": True, "validated": True, "executable": True}


@router.post("/projects/{project_name}/editor/{target_type}/{target_id}/visual-effects")
def apply_visual_effect(
    project_name: str,
    target_type: Literal["item", "track"],
    target_id: str,
    body: EffectApplyRequest,
    request: Request,
):
    member = _member(request)
    _require_advanced(member)
    spec = EFFECTS.get(body.effect_id)
    if spec is None:
        raise HTTPException(400, "Unknown or non-executable visual effect")
    if target_type not in spec.scopes:
        raise HTTPException(400, f"{spec.name} cannot execute at {target_type} scope")
    try:
        parameters = normalize_effect_parameters(spec.effect_id, body.parameters)
        keyframes = normalize_keyframes(spec, body.keyframes)
        store = _store(project_name)
        if spec.runtime == "item_color":
            if target_type != "item":
                raise ValueError("Video colour controls execute at item scope")
            state = store.public_state()
            item = next((row for row in state["branch"].get("items", []) if row.get("id") == target_id), None)
            if item is None:
                raise KeyError(target_id)
            color = dict(item.get("color") or {})
            color[spec.runtime_type] = parameters["value"]
            updated = store.patch_item(target_id, {"color": color}, actor=_actor(member))
            result = {"item": updated.model_dump(mode="json")}
        else:
            effect = EditorEffect(
                type=spec.runtime_type,
                enabled=True,
                mix=body.mix,
                parameters=parameters,
                keyframes=keyframes,
                metadata={
                    "catalogue_effect_id": spec.effect_id,
                    "bounded_runtime": True,
                    "premium_entitlement_checked": True,
                },
            )
            created = store.add_effect(target_type, target_id, effect, actor=_actor(member))
            result = {"effect": created.model_dump(mode="json")}
    except KeyError as exc:
        raise HTTPException(404, f"Editor resource not found: {exc.args[0]}") from exc
    except (PermissionError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    result["editor"] = store.public_state()
    result["executable"] = True
    result["runtime"] = spec.runtime
    return result


@router.get("/projects/{project_name}/visual-effect-systems")
def list_visual_effect_systems(project_name: str, request: Request):
    member = _member(request)
    _require_designer(member)
    project = _project(project_name)
    try:
        systems = _load_systems(project)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(500, "Visual effect system store is unavailable") from exc
    return {"systems": systems, "private": True, "project_scoped": True}


@router.post("/projects/{project_name}/visual-effect-systems")
def save_visual_effect_system(project_name: str, body: SaveEffectSystemRequest, request: Request):
    member = _member(request)
    _require_designer(member)
    project = _project(project_name)
    graph = dict(body.graph or {})
    nodes = graph.get("nodes")
    if graph.get("schema_version") != 1 or graph.get("graph_type") != "visual_effect" or not isinstance(nodes, list) or len(nodes) != 1:
        raise HTTPException(400, "Only validated single-effect visual graphs can be saved")
    node = nodes[0]
    effect_id = str(node.get("effect_id") or "")
    spec = EFFECTS.get(effect_id)
    if spec is None:
        raise HTTPException(400, "Visual graph references a non-executable effect")
    try:
        node["parameters"] = normalize_effect_parameters(effect_id, node.get("parameters"))
        node["keyframes"] = normalize_keyframes(spec, node.get("keyframes"))
        mix = _finite(node.get("mix", 1.0), field="mix")
        if mix < 0.0 or mix > 1.0:
            raise ValueError("mix must be between 0 and 1")
        node["mix"] = mix
        graph["nodes"] = [node]
        graph["validated"] = True
        graph["executable"] = True
        graph["arbitrary_code"] = False
        graph["arbitrary_ffmpeg_arguments"] = False
        systems = _load_systems(project)
        record = {
            "id": f"vfxsys_{uuid4().hex}",
            "name": body.name,
            "graph": graph,
            "private": True,
            "project_scoped": True,
        }
        systems.append(record)
        _save_systems(project, systems[-200:])
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"system": record, "private": True, "project_scoped": True}


__all__ = [
    "EFFECTS",
    "EffectSpec",
    "ParameterSpec",
    "compile_effect_graph",
    "normalize_effect_parameters",
    "normalize_keyframes",
    "router",
]
