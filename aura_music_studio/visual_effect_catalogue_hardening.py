from __future__ import annotations

import json
from dataclasses import replace
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from . import visual_effect_catalogue as base
from .professional_editor import EditorEffect

router = APIRouter(prefix="/creative", tags=["Visual Effects Catalogue Security"])

_MEDIA_KEYWORDS: dict[str, dict[str, str]] = {
    "blur": {"image": "image.blur.gaussian"},
    "gaussian": {"image": "image.blur.gaussian"},
    "sharpen": {"image": "image.sharpen.unsharp"},
    "unsharp": {"image": "image.sharpen.unsharp"},
    "black and white": {"image": "image.filter.grayscale"},
    "grayscale": {"image": "image.filter.grayscale"},
    "greyscale": {"image": "image.filter.grayscale"},
    "invert": {"image": "image.filter.invert"},
    "sepia": {"image": "image.filter.sepia"},
    "vignette": {"image": "image.light.vignette"},
    "pixelate": {"image": "image.stylise.pixelate"},
    "pixel": {"image": "image.stylise.pixelate"},
    "cinematic": {"image": "image.filter.cinematic"},
    "duotone": {"image": "image.filter.duotone"},
    "temperature": {"video": "video.color.temperature"},
    "tint": {"video": "video.color.tint"},
    "gamma": {"video": "video.color.gamma"},
    "exposure": {"video": "video.color.exposure"},
    "brightness": {"image": "image.color.brightness", "video": "video.color.brightness"},
    "contrast": {"image": "image.color.contrast", "video": "video.color.contrast"},
    "saturation": {"image": "image.color.saturation", "video": "video.color.saturation"},
}
_VIDEO_COLOUR_IDS = frozenset({
    "video.color.exposure",
    "video.color.brightness",
    "video.color.contrast",
    "video.color.saturation",
    "video.color.gamma",
    "video.color.temperature",
    "video.color.tint",
})
_ITEM_KIND_BY_MEDIA = {"image": "image_layer", "video": "video_clip"}


def install_visual_effect_catalogue_hardening() -> None:
    """Harden media-specific compilation and advertised automation truth in-place."""
    for effect_id in _VIDEO_COLOUR_IDS:
        spec = base.EFFECTS[effect_id]
        if spec.supports_keyframes:
            base.EFFECTS[effect_id] = replace(spec, supports_keyframes=False)


def _validate_target_media(state: dict, target_type: str, target_id: str, spec: base.EffectSpec) -> dict:
    """Require the selected editor resource to match the catalogue effect's media domain."""
    branch = state.get("branch") or {}
    if len(spec.media) != 1:
        raise ValueError("Visual effect media contract is ambiguous")
    media_kind = spec.media[0]
    if target_type == "item":
        target = next((row for row in branch.get("items", []) if row.get("id") == target_id), None)
        if target is None:
            raise KeyError(target_id)
        expected_kind = _ITEM_KIND_BY_MEDIA[media_kind]
        if target.get("kind") != expected_kind:
            raise ValueError(f"{spec.name} requires a {expected_kind} editor item")
        return target
    if target_type == "track":
        target = next((row for row in branch.get("tracks", []) if row.get("id") == target_id), None)
        if target is None:
            raise KeyError(target_id)
        if target.get("kind") != media_kind:
            raise ValueError(f"{spec.name} requires a {media_kind} editor track")
        return target
    raise ValueError("Unsupported visual effect target type")


def compile_effect_graph_hardened(
    prompt: str,
    media_kind: Literal["image", "video"],
    *,
    parameters: dict | None = None,
    mix: float = 1.0,
    keyframes: dict | None = None,
) -> dict:
    install_visual_effect_catalogue_hardening()
    text = " ".join(str(prompt or "").strip().lower().split())
    candidates = [
        media_map[media_kind]
        for keyword, media_map in _MEDIA_KEYWORDS.items()
        if keyword in text and media_kind in media_map
    ]
    candidates = list(dict.fromkeys(candidates))
    if not candidates:
        raise ValueError("No executable visual effect could be resolved from that prompt")
    if len(candidates) > 1:
        raise ValueError(f"Prompt is ambiguous across executable effects: {', '.join(candidates)}")
    effect_id = candidates[0]
    spec = base.EFFECTS[effect_id]
    normalized_parameters = base.normalize_effect_parameters(effect_id, parameters)
    normalized_keyframes = base.normalize_keyframes(spec, keyframes)
    mix_value = base._finite(mix, field="mix")
    if mix_value < 0.0 or mix_value > 1.0:
        raise ValueError("mix must be between 0 and 1")
    if spec.runtime == "item_color" and abs(mix_value - 1.0) > 1e-9:
        raise ValueError("Video colour controls execute directly and do not support effect mix")
    return {
        "schema_version": 1,
        "graph_type": "visual_effect",
        "media_kind": media_kind,
        "nodes": [{
            "id": f"fx_{uuid4().hex}",
            "effect_id": effect_id,
            "runtime": spec.runtime,
            "runtime_type": spec.runtime_type,
            "parameters": normalized_parameters,
            "mix": mix_value,
            "keyframes": normalized_keyframes,
        }],
        "validated": True,
        "executable": True,
        "arbitrary_code": False,
        "arbitrary_ffmpeg_arguments": False,
        "shell_execution": False,
    }


@router.post("/visual-effects/compile")
def compile_visual_effect_hardened(body: base.EffectCompileRequest, request: Request):
    member = base._member(request)
    base._require_designer(member)
    try:
        graph = compile_effect_graph_hardened(
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
def apply_visual_effect_hardened(
    project_name: str,
    target_type: Literal["item", "track"],
    target_id: str,
    body: base.EffectApplyRequest,
    request: Request,
):
    install_visual_effect_catalogue_hardening()
    member = base._member(request)
    base._require_advanced(member)
    spec = base.EFFECTS.get(body.effect_id)
    if spec is None:
        raise HTTPException(400, "Unknown or non-executable visual effect")
    if target_type not in spec.scopes:
        raise HTTPException(400, f"{spec.name} cannot execute at {target_type} scope")
    try:
        parameters = base.normalize_effect_parameters(spec.effect_id, body.parameters)
        keyframes = base.normalize_keyframes(spec, body.keyframes)
        store = base._store(project_name)
        state = store.public_state()
        target = _validate_target_media(state, target_type, target_id, spec)
        if spec.runtime == "item_color":
            if abs(float(body.mix) - 1.0) > 1e-9:
                raise ValueError("Video colour controls execute directly and do not support effect mix")
            if keyframes:
                raise ValueError("Video colour controls do not advertise unexecuted keyframes")
            if target_type != "item":
                raise ValueError("Video colour controls execute at item scope")
            color = dict(target.get("color") or {})
            color[spec.runtime_type] = parameters["value"]
            updated = store.patch_item(target_id, {"color": color}, actor=base._actor(member))
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
            created = store.add_effect(target_type, target_id, effect, actor=base._actor(member))
            result = {"effect": created.model_dump(mode="json")}
    except KeyError as exc:
        raise HTTPException(404, f"Editor resource not found: {exc.args[0]}") from exc
    except (PermissionError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    result["editor"] = store.public_state()
    result["executable"] = True
    result["runtime"] = spec.runtime
    return result


@router.post("/projects/{project_name}/visual-effect-systems")
def save_visual_effect_system_hardened(
    project_name: str,
    body: base.SaveEffectSystemRequest,
    request: Request,
):
    install_visual_effect_catalogue_hardening()
    member = base._member(request)
    base._require_designer(member)
    project = base._project(project_name)
    graph = dict(body.graph or {})
    nodes = graph.get("nodes")
    if (
        graph.get("schema_version") != 1
        or graph.get("graph_type") != "visual_effect"
        or not isinstance(nodes, list)
        or len(nodes) != 1
        or not isinstance(nodes[0], dict)
    ):
        raise HTTPException(400, "Only validated single-effect visual graphs can be saved")
    node = dict(nodes[0])
    effect_id = str(node.get("effect_id") or "")
    spec = base.EFFECTS.get(effect_id)
    if spec is None:
        raise HTTPException(400, "Visual graph references a non-executable effect")
    try:
        media_kind = str(graph.get("media_kind") or "").strip()
        if media_kind not in spec.media:
            raise ValueError("Visual graph media kind does not match the executable effect")
        parameters = base.normalize_effect_parameters(effect_id, node.get("parameters"))
        keyframes = base.normalize_keyframes(spec, node.get("keyframes"))
        mix = base._finite(node.get("mix", 1.0), field="mix")
        if mix < 0.0 or mix > 1.0:
            raise ValueError("mix must be between 0 and 1")
        if spec.runtime == "item_color" and abs(mix - 1.0) > 1e-9:
            raise ValueError("Video colour controls execute directly and do not support effect mix")
        safe_node = {
            "id": f"fx_{uuid4().hex}",
            "effect_id": effect_id,
            "runtime": spec.runtime,
            "runtime_type": spec.runtime_type,
            "parameters": parameters,
            "mix": mix,
            "keyframes": keyframes,
        }
        safe_graph = {
            "schema_version": 1,
            "graph_type": "visual_effect",
            "media_kind": media_kind,
            "nodes": [safe_node],
            "validated": True,
            "executable": True,
            "arbitrary_code": False,
            "arbitrary_ffmpeg_arguments": False,
            "shell_execution": False,
        }
        systems = base._load_systems(project)
        record = {
            "id": f"vfxsys_{uuid4().hex}",
            "name": body.name,
            "graph": safe_graph,
            "private": True,
            "project_scoped": True,
        }
        systems.append(record)
        base._save_systems(project, systems[-200:])
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"system": record, "private": True, "project_scoped": True}


install_visual_effect_catalogue_hardening()


__all__ = [
    "compile_effect_graph_hardened",
    "install_visual_effect_catalogue_hardening",
    "router",
]
