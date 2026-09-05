from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .plans import AUTOMATION, BASIC_TIMELINE
from .professional_editor_api import (
    PatchRequest,
    patch_item as base_patch_item,
    patch_track as base_patch_track,
)
from .professional_editor_render_api import (
    EditorRenderRequest,
    _renderer as base_renderer,
    render_editor_sequence as base_render_editor_sequence,
)

router = APIRouter(prefix="/creative", tags=["Professional Editor Visual Entitlements"])


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    if not member.plan.has(BASIC_TIMELINE):
        raise HTTPException(403, "Professional creative editing unlocks on the Basic membership tier")
    return member


def _normal_blend(value: Any) -> bool:
    return str(value or "normal").strip().lower() == "normal"


def _default_track_opacity(value: Any) -> bool:
    try:
        return abs(float(value) - 1.0) <= 1e-9
    except (TypeError, ValueError):
        return False


def _nonempty_advanced_field(changes: dict[str, Any], field: str) -> bool:
    """Treat authored advanced graph state as Pro while allowing empty-value downgrade resets."""
    return field in changes and bool(changes.get(field))


def item_patch_requires_pro(changes: dict[str, Any]) -> bool:
    """Protect advanced item graph state; item opacity remains a Basic transform control."""
    if "blend_mode" in changes and not _normal_blend(changes.get("blend_mode")):
        return True
    return any(_nonempty_advanced_field(changes, field) for field in ("effects", "masks", "keyframes"))


def track_patch_requires_pro(changes: dict[str, Any]) -> bool:
    """Protect advanced track/group state while allowing downgraded members to reset defaults."""
    if "blend_mode" in changes and not _normal_blend(changes.get("blend_mode")):
        return True
    if "opacity" in changes and not _default_track_opacity(changes.get("opacity")):
        return True
    return any(_nonempty_advanced_field(changes, field) for field in ("effects", "keyframes"))


def professional_visual_state_reasons(state: dict[str, Any], sequence_id: str) -> list[str]:
    """Return preserved Pro visual state that must not render on a downgraded Basic plan."""
    branch = state.get("branch") or {}
    sequences = {value.get("id"): value for value in branch.get("sequences", [])}
    tracks = {value.get("id"): value for value in branch.get("tracks", [])}
    items = {value.get("id"): value for value in branch.get("items", [])}
    sequence = sequences.get(sequence_id)
    if sequence is None:
        return []

    reasons: list[str] = []
    for track_id in sequence.get("track_ids", []):
        track = tracks.get(track_id)
        if not track:
            continue
        if not _normal_blend(track.get("blend_mode")):
            reasons.append(f"track:{track_id}:blend_mode")
        if not _default_track_opacity(track.get("opacity", 1.0)):
            reasons.append(f"track:{track_id}:opacity")
        if track.get("effects"):
            reasons.append(f"track:{track_id}:effects")
        if track.get("keyframes"):
            reasons.append(f"track:{track_id}:keyframes")
        for item_id in track.get("item_ids", []):
            item = items.get(item_id)
            if not item:
                continue
            if not _normal_blend(item.get("blend_mode")):
                reasons.append(f"item:{item_id}:blend_mode")
            if item.get("effects"):
                reasons.append(f"item:{item_id}:effects")
            if item.get("masks"):
                reasons.append(f"item:{item_id}:masks")
            if item.get("keyframes"):
                reasons.append(f"item:{item_id}:keyframes")
    return reasons


def _require_pro_visual(member, reasons: list[str] | None = None) -> None:
    if member.plan.has(AUTOMATION):
        return
    detail = "Professional masks, effects, keyframes, blend and track-group controls require Pro."
    if reasons:
        detail += " The authored Pro state is preserved; switch to Pro to render it, or reset the advanced values to their defaults."
    raise HTTPException(403, detail)


@router.patch("/projects/{project_name}/editor/items/{item_id}")
def patch_item_with_visual_entitlements(
    project_name: str,
    item_id: str,
    body: PatchRequest,
    request: Request,
):
    member = _member(request)
    if item_patch_requires_pro(body.changes):
        _require_pro_visual(member)
    return base_patch_item(project_name, item_id, body, request)


@router.patch("/projects/{project_name}/editor/tracks/{track_id}")
def patch_track_with_visual_entitlements(
    project_name: str,
    track_id: str,
    body: PatchRequest,
    request: Request,
):
    member = _member(request)
    if track_patch_requires_pro(body.changes):
        _require_pro_visual(member)
    return base_patch_track(project_name, track_id, body, request)


@router.post("/projects/{project_name}/editor/sequences/{sequence_id}/render")
def render_editor_sequence_with_visual_entitlements(
    project_name: str,
    sequence_id: str,
    body: EditorRenderRequest,
    request: Request,
):
    member = _member(request)
    if not member.plan.has(AUTOMATION):
        renderer = base_renderer(project_name)
        reasons = professional_visual_state_reasons(renderer.store.public_state(), sequence_id)
        if reasons:
            _require_pro_visual(member, reasons)
    return base_render_editor_sequence(project_name, sequence_id, body, request)


__all__ = [
    "router",
    "item_patch_requires_pro",
    "track_patch_requires_pro",
    "professional_visual_state_reasons",
    "patch_item_with_visual_entitlements",
    "patch_track_with_visual_entitlements",
    "render_editor_sequence_with_visual_entitlements",
]
