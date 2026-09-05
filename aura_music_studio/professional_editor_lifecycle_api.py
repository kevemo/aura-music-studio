from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .plans import AUTOMATION, BASIC_TIMELINE
from .professional_editor import EditorEffect, EditorMask, ProfessionalEditorStore
from .tenant_storage import project_path

router = APIRouter(prefix="/creative", tags=["Professional Editor Lifecycle"])


class PatchRequest(BaseModel):
    changes: dict[str, Any] = Field(default_factory=dict)


class ReorderRequest(BaseModel):
    index: int = Field(ge=0, le=4096)


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    if not member.plan.has(BASIC_TIMELINE):
        raise HTTPException(403, "Professional editor lifecycle controls require Basic")
    if not member.plan.has(AUTOMATION):
        raise HTTPException(403, "Mask, effect and keyframe lifecycle controls require Pro")
    return member


def _actor(member) -> str:
    user = getattr(member, "user", {}) or {}
    return str(user.get("display_name") or user.get("email") or "Studio Member")[:160]


def _store(project_name: str) -> ProfessionalEditorStore:
    try:
        project = project_path(project_name, must_exist=True)
    except ValueError as exc:
        raise HTTPException(400, "Invalid project path") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Project not found") from exc
    store = ProfessionalEditorStore(project)
    if not store.exists():
        raise HTTPException(404, "Professional editor is not initialized for this project")
    return store


def _execute(callable_):
    try:
        return callable_()
    except KeyError as exc:
        raise HTTPException(404, f"Editor resource not found: {exc.args[0]}") from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _target(store: ProfessionalEditorStore, branch, target_type: Literal["item", "track"], target_id: str):
    if target_type == "item":
        return store._item(branch, target_id)
    return store._track(branch, target_id)


def patch_mask_graph(
    store: ProfessionalEditorStore,
    item_id: str,
    mask_id: str,
    changes: dict[str, Any],
    *,
    actor: str = "Member",
) -> EditorMask:
    allowed = {"name", "mode", "enabled", "inverted", "opacity", "feather", "expansion", "shape", "points", "tracking", "keyframes"}
    unknown = sorted(set(changes) - allowed)
    if unknown:
        raise ValueError(f"Unsupported mask field(s): {', '.join(unknown)}")
    project = store.load()
    branch = store._branch(project)
    item = store._item(branch, item_id)
    if store._locked(branch, "item", item.id):
        raise PermissionError("Item or parent track is locked")
    index = next((i for i, value in enumerate(item.masks) if value.id == mask_id), None)
    if index is None:
        raise KeyError(mask_id)
    before = store._capture(branch, [("item", item.id)])
    payload = item.masks[index].model_dump(mode="json")
    for key, value in changes.items():
        if key in {"tracking", "keyframes"} and isinstance(value, dict):
            merged = deepcopy(payload.get(key) or {})
            merged.update(value)
            payload[key] = merged
        else:
            payload[key] = value
    updated = EditorMask.model_validate(payload)
    item.masks[index] = updated
    store._touch(branch, item)
    after = store._capture(branch, [("item", item.id)])
    store._record(
        branch,
        operation="patch_mask",
        label=f"Edit mask {updated.name}",
        before=before,
        after=after,
        actor=actor,
        target_type="item",
        target_id=item.id,
        metadata={"mask_id": mask_id, "fields": sorted(changes)},
    )
    store.save(project)
    return updated


def delete_mask_graph(store: ProfessionalEditorStore, item_id: str, mask_id: str, *, actor: str = "Member") -> EditorMask:
    project = store.load()
    branch = store._branch(project)
    item = store._item(branch, item_id)
    if store._locked(branch, "item", item.id):
        raise PermissionError("Item or parent track is locked")
    index = next((i for i, value in enumerate(item.masks) if value.id == mask_id), None)
    if index is None:
        raise KeyError(mask_id)
    before = store._capture(branch, [("item", item.id)])
    removed = item.masks.pop(index)
    store._touch(branch, item)
    after = store._capture(branch, [("item", item.id)])
    store._record(
        branch,
        operation="delete_mask",
        label=f"Delete mask {removed.name}",
        before=before,
        after=after,
        actor=actor,
        target_type="item",
        target_id=item.id,
        metadata={"mask_id": mask_id},
    )
    store.save(project)
    return removed


def patch_effect_graph(
    store: ProfessionalEditorStore,
    target_type: Literal["item", "track"],
    target_id: str,
    effect_id: str,
    changes: dict[str, Any],
    *,
    actor: str = "Member",
) -> EditorEffect:
    allowed = {"type", "enabled", "mix", "parameters", "keyframes", "metadata"}
    unknown = sorted(set(changes) - allowed)
    if unknown:
        raise ValueError(f"Unsupported effect field(s): {', '.join(unknown)}")
    project = store.load()
    branch = store._branch(project)
    if store._locked(branch, target_type, target_id):
        raise PermissionError(f"{target_type.title()} is locked")
    target = _target(store, branch, target_type, target_id)
    index = next((i for i, value in enumerate(target.effects) if value.id == effect_id), None)
    if index is None:
        raise KeyError(effect_id)
    before = store._capture(branch, [(target_type, target_id)])
    payload = target.effects[index].model_dump(mode="json")
    for key, value in changes.items():
        if key in {"parameters", "keyframes", "metadata"} and isinstance(value, dict):
            merged = deepcopy(payload.get(key) or {})
            merged.update(value)
            payload[key] = merged
        else:
            payload[key] = value
    updated = EditorEffect.model_validate(payload)
    target.effects[index] = updated
    store._touch(branch, target)
    after = store._capture(branch, [(target_type, target_id)])
    store._record(
        branch,
        operation="patch_effect",
        label=f"Edit {updated.type} effect",
        before=before,
        after=after,
        actor=actor,
        target_type=target_type,
        target_id=target_id,
        metadata={"effect_id": effect_id, "fields": sorted(changes)},
    )
    store.save(project)
    return updated


def delete_effect_graph(
    store: ProfessionalEditorStore,
    target_type: Literal["item", "track"],
    target_id: str,
    effect_id: str,
    *,
    actor: str = "Member",
) -> EditorEffect:
    project = store.load()
    branch = store._branch(project)
    if store._locked(branch, target_type, target_id):
        raise PermissionError(f"{target_type.title()} is locked")
    target = _target(store, branch, target_type, target_id)
    index = next((i for i, value in enumerate(target.effects) if value.id == effect_id), None)
    if index is None:
        raise KeyError(effect_id)
    before = store._capture(branch, [(target_type, target_id)])
    removed = target.effects.pop(index)
    store._touch(branch, target)
    after = store._capture(branch, [(target_type, target_id)])
    store._record(
        branch,
        operation="delete_effect",
        label=f"Delete {removed.type} effect",
        before=before,
        after=after,
        actor=actor,
        target_type=target_type,
        target_id=target_id,
        metadata={"effect_id": effect_id},
    )
    store.save(project)
    return removed


def reorder_effect_graph(
    store: ProfessionalEditorStore,
    target_type: Literal["item", "track"],
    target_id: str,
    effect_id: str,
    index: int,
    *,
    actor: str = "Member",
) -> list[EditorEffect]:
    project = store.load()
    branch = store._branch(project)
    if store._locked(branch, target_type, target_id):
        raise PermissionError(f"{target_type.title()} is locked")
    target = _target(store, branch, target_type, target_id)
    current = next((i for i, value in enumerate(target.effects) if value.id == effect_id), None)
    if current is None:
        raise KeyError(effect_id)
    before = store._capture(branch, [(target_type, target_id)])
    effect = target.effects.pop(current)
    destination = max(0, min(int(index), len(target.effects)))
    target.effects.insert(destination, effect)
    store._touch(branch, target)
    after = store._capture(branch, [(target_type, target_id)])
    store._record(
        branch,
        operation="reorder_effect",
        label=f"Reorder {effect.type} effect",
        before=before,
        after=after,
        actor=actor,
        target_type=target_type,
        target_id=target_id,
        metadata={"effect_id": effect_id, "from": current, "to": destination},
    )
    store.save(project)
    return list(target.effects)


def delete_keyframe_lane_graph(
    store: ProfessionalEditorStore,
    item_id: str,
    parameter: str,
    *,
    actor: str = "Member",
) -> int:
    clean = str(parameter or "").strip()
    if not clean:
        raise ValueError("A keyframe parameter path is required")
    project = store.load()
    branch = store._branch(project)
    item = store._item(branch, item_id)
    if store._locked(branch, "item", item.id):
        raise PermissionError("Item or parent track is locked")
    if clean not in item.keyframes:
        raise KeyError(clean)
    before = store._capture(branch, [("item", item.id)])
    removed = len(item.keyframes.pop(clean))
    store._touch(branch, item)
    after = store._capture(branch, [("item", item.id)])
    store._record(
        branch,
        operation="delete_keyframe_lane",
        label=f"Delete keyframes · {clean}",
        before=before,
        after=after,
        actor=actor,
        target_type="item",
        target_id=item.id,
        metadata={"parameter": clean, "removed_keyframes": removed},
    )
    store.save(project)
    return removed


@router.patch("/projects/{project_name}/editor/items/{item_id}/masks/{mask_id}")
def patch_mask(project_name: str, item_id: str, mask_id: str, body: PatchRequest, request: Request):
    member = _member(request)
    store = _store(project_name)
    mask = _execute(lambda: patch_mask_graph(store, item_id, mask_id, body.changes, actor=_actor(member)))
    return {"mask": mask.model_dump(mode="json"), "editor": store.public_state(), "source_media_mutated": False}


@router.delete("/projects/{project_name}/editor/items/{item_id}/masks/{mask_id}")
def delete_mask(project_name: str, item_id: str, mask_id: str, request: Request):
    member = _member(request)
    store = _store(project_name)
    removed = _execute(lambda: delete_mask_graph(store, item_id, mask_id, actor=_actor(member)))
    return {"deleted": True, "mask": removed.model_dump(mode="json"), "editor": store.public_state(), "source_media_mutated": False}


@router.patch("/projects/{project_name}/editor/{target_type}/{target_id}/effects/{effect_id}")
def patch_effect(
    project_name: str,
    target_type: Literal["item", "track"],
    target_id: str,
    effect_id: str,
    body: PatchRequest,
    request: Request,
):
    member = _member(request)
    store = _store(project_name)
    effect = _execute(lambda: patch_effect_graph(store, target_type, target_id, effect_id, body.changes, actor=_actor(member)))
    return {"effect": effect.model_dump(mode="json"), "editor": store.public_state(), "source_media_mutated": False}


@router.delete("/projects/{project_name}/editor/{target_type}/{target_id}/effects/{effect_id}")
def delete_effect(
    project_name: str,
    target_type: Literal["item", "track"],
    target_id: str,
    effect_id: str,
    request: Request,
):
    member = _member(request)
    store = _store(project_name)
    removed = _execute(lambda: delete_effect_graph(store, target_type, target_id, effect_id, actor=_actor(member)))
    return {"deleted": True, "effect": removed.model_dump(mode="json"), "editor": store.public_state(), "source_media_mutated": False}


@router.post("/projects/{project_name}/editor/{target_type}/{target_id}/effects/{effect_id}/reorder")
def reorder_effect(
    project_name: str,
    target_type: Literal["item", "track"],
    target_id: str,
    effect_id: str,
    body: ReorderRequest,
    request: Request,
):
    member = _member(request)
    store = _store(project_name)
    effects = _execute(lambda: reorder_effect_graph(store, target_type, target_id, effect_id, body.index, actor=_actor(member)))
    return {"effects": [effect.model_dump(mode="json") for effect in effects], "editor": store.public_state(), "source_media_mutated": False}


@router.delete("/projects/{project_name}/editor/items/{item_id}/keyframes/{parameter}")
def delete_keyframe_lane(project_name: str, item_id: str, parameter: str, request: Request):
    member = _member(request)
    store = _store(project_name)
    removed = _execute(lambda: delete_keyframe_lane_graph(store, item_id, parameter, actor=_actor(member)))
    return {"deleted": True, "parameter": parameter, "removed_keyframes": removed, "editor": store.public_state(), "source_media_mutated": False}


__all__ = [
    "router",
    "patch_mask_graph",
    "delete_mask_graph",
    "patch_effect_graph",
    "delete_effect_graph",
    "reorder_effect_graph",
    "delete_keyframe_lane_graph",
]
