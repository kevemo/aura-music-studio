from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .creative_project import CreativeProjectStore
from .plans import AUTOMATION, BASIC_TIMELINE
from .professional_editor import (
    EditorEffect,
    EditorItem,
    EditorItemKind,
    EditorMask,
    EditorMediaKind,
    EditorTrackKind,
    ProfessionalEditorStore,
)
from .professional_editor_source_security import (
    normalize_project_source_ref,
    normalized_manifest_for_editor,
)
from .professional_track_keyframe_authoring import set_track_keyframes as persist_track_keyframes
from .tenant_storage import project_path

router = APIRouter(prefix="/creative", tags=["Professional Creative Editor"])


class InitializeEditorRequest(BaseModel):
    sync_creative_manifest: bool = True


class SequenceRequest(BaseModel):
    kind: EditorMediaKind
    name: str = Field(min_length=1, max_length=180)
    width: int = Field(default=1920, ge=64, le=16384)
    height: int = Field(default=1080, ge=64, le=16384)
    fps: float = Field(default=24.0, ge=1.0, le=240.0)
    duration: float = Field(default=30.0, gt=0.0, le=86400.0)


class TrackRequest(BaseModel):
    kind: EditorTrackKind
    name: str = Field(min_length=1, max_length=160)
    role: str = Field(default="", max_length=120)


class ItemRequest(BaseModel):
    kind: EditorItemKind
    name: str = Field(min_length=1, max_length=200)
    source_element_id: str | None = Field(default=None, max_length=200)
    source_ref: str | None = Field(default=None, max_length=1000)
    start: float = Field(default=0.0, ge=0.0, le=86400.0)
    duration: float = Field(default=5.0, gt=0.0, le=86400.0)
    source_in: float = Field(default=0.0, ge=0.0, le=86400.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PatchRequest(BaseModel):
    changes: dict[str, Any] = Field(default_factory=dict)


class KeyframesRequest(BaseModel):
    parameter: str = Field(min_length=1, max_length=160)
    keyframes: list[dict[str, Any]] = Field(default_factory=list, max_length=4096)


class MaskRequest(BaseModel):
    name: str = Field(default="Mask", min_length=1, max_length=120)
    mode: Literal["add", "subtract", "intersect"] = "add"
    enabled: bool = True
    inverted: bool = False
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    feather: float = Field(default=0.0, ge=0.0, le=1000.0)
    expansion: float = Field(default=0.0, ge=-1000.0, le=1000.0)
    shape: Literal["rectangle", "ellipse", "polygon", "path"] = "rectangle"
    points: list[tuple[float, float]] = Field(default_factory=list, max_length=4096)
    tracking: dict[str, Any] = Field(default_factory=dict)
    keyframes: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class EffectRequest(BaseModel):
    type: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    mix: float = Field(default=1.0, ge=0.0, le=1.0)
    parameters: dict[str, Any] = Field(default_factory=dict)
    keyframes: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SplitRequest(BaseModel):
    time: float = Field(gt=0.0, le=86400.0)


class SlipRequest(BaseModel):
    delta: float = Field(ge=-86400.0, le=86400.0)


class RollRequest(BaseModel):
    right_item_id: str = Field(min_length=1, max_length=200)
    delta: float = Field(ge=-86400.0, le=86400.0)


class DuplicateRequest(BaseModel):
    start: float | None = Field(default=None, ge=0.0, le=86400.0)


class ReorderTrackRequest(BaseModel):
    track_id: str = Field(min_length=1, max_length=200)
    index: int = Field(ge=0, le=4096)


class BranchRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Membership context unavailable")
    if not member.plan.has(BASIC_TIMELINE):
        raise HTTPException(403, "Professional creative editing unlocks on the Basic membership tier")
    return member


def _require_pro(member) -> None:
    if not member.plan.has(AUTOMATION):
        raise HTTPException(403, "Advanced keyframes, masks, effects and editor branches require Pro")


def _actor(member) -> str:
    user = getattr(member, "user", {}) or {}
    return str(user.get("display_name") or user.get("email") or "Studio Member")[:160]


def _project(project_name: str):
    try:
        return project_path(project_name, must_exist=True)
    except ValueError as exc:
        raise HTTPException(400, "Invalid project path") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Project not found") from exc


def _store(project_name: str, *, initialize: bool = False) -> ProfessionalEditorStore:
    project = _project(project_name)
    store = ProfessionalEditorStore(project)
    if initialize:
        store.initialize(project_name)
    elif not store.exists():
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


def _state(store: ProfessionalEditorStore) -> dict[str, Any]:
    value = store.public_state()
    branch = value["branch"]
    value["editor_capabilities"] = {
        "non_destructive": True,
        "source_media_mutated": False,
        "undo": bool(branch.get("undo_stack")),
        "redo": bool(branch.get("redo_stack")),
        "timeline_operations": ["split", "ripple_delete", "slip", "roll", "duplicate", "reorder"],
        "keyframe_targets": ["item", "track"],
        "video_track_keyframe_paths": ["opacity", "track.opacity"],
    }
    return value


@router.post("/projects/{project_name}/editor/initialize")
def initialize_editor(project_name: str, body: InitializeEditorRequest, request: Request):
    member = _member(request)
    project = _project(project_name)
    normalized_manifest = None
    if body.sync_creative_manifest:
        creative = CreativeProjectStore(project)
        if creative.exists():
            normalized_manifest = _execute(
                lambda: normalized_manifest_for_editor(project, creative.load())
            )
    store = ProfessionalEditorStore(project)
    store.initialize(project_name)
    sync = {"imported": 0, "imported_element_ids": []}
    if normalized_manifest is not None:
        sync = _execute(
            lambda: store.sync_creative_manifest(normalized_manifest, actor=_actor(member))
        )
    return {"editor": _state(store), "manifest_sync": sync}


@router.get("/projects/{project_name}/editor")
def editor_state(project_name: str, request: Request):
    _member(request)
    return _state(_store(project_name))


@router.post("/projects/{project_name}/editor/sync-manifest")
def sync_manifest(project_name: str, request: Request):
    member = _member(request)
    store = _store(project_name)
    project = store.project_dir
    creative = CreativeProjectStore(project)
    if not creative.exists():
        raise HTTPException(404, "Creative manifest is not initialized for this project")
    manifest = _execute(lambda: normalized_manifest_for_editor(project, creative.load()))
    result = _execute(lambda: store.sync_creative_manifest(manifest, actor=_actor(member)))
    return {"sync": result, "editor": _state(store)}


@router.post("/projects/{project_name}/editor/sequences")
def create_sequence(project_name: str, body: SequenceRequest, request: Request):
    member = _member(request)
    store = _store(project_name)
    sequence = _execute(lambda: store.create_sequence(
        kind=body.kind,
        name=body.name,
        width=body.width,
        height=body.height,
        fps=body.fps,
        duration=body.duration,
        actor=_actor(member),
    ))
    return {"sequence": sequence.model_dump(mode="json"), "editor": _state(store)}


@router.patch("/projects/{project_name}/editor/sequences/{sequence_id}")
def patch_sequence(project_name: str, sequence_id: str, body: PatchRequest, request: Request):
    member = _member(request)
    store = _store(project_name)
    sequence = _execute(lambda: store.patch_sequence(sequence_id, body.changes, actor=_actor(member)))
    return {"sequence": sequence.model_dump(mode="json"), "editor": _state(store)}


@router.post("/projects/{project_name}/editor/sequences/{sequence_id}/tracks")
def create_track(project_name: str, sequence_id: str, body: TrackRequest, request: Request):
    member = _member(request)
    store = _store(project_name)
    track = _execute(lambda: store.create_track(
        sequence_id,
        kind=body.kind,
        name=body.name,
        role=body.role,
        actor=_actor(member),
    ))
    return {"track": track.model_dump(mode="json"), "editor": _state(store)}


@router.patch("/projects/{project_name}/editor/tracks/{track_id}")
def patch_track(project_name: str, track_id: str, body: PatchRequest, request: Request):
    member = _member(request)
    store = _store(project_name)
    track = _execute(lambda: store.patch_track(track_id, body.changes, actor=_actor(member)))
    return {"track": track.model_dump(mode="json"), "editor": _state(store)}


@router.post("/projects/{project_name}/editor/tracks/{track_id}/keyframes")
def set_track_keyframes(project_name: str, track_id: str, body: KeyframesRequest, request: Request):
    member = _member(request)
    _require_pro(member)
    store = _store(project_name)
    track = _execute(
        lambda: persist_track_keyframes(
            store,
            track_id,
            body.parameter,
            body.keyframes,
            actor=_actor(member),
        )
    )
    return {"track": track.model_dump(mode="json"), "editor": _state(store)}


@router.post("/projects/{project_name}/editor/tracks/{track_id}/items")
def create_item(project_name: str, track_id: str, body: ItemRequest, request: Request):
    member = _member(request)
    store = _store(project_name)
    source_ref = _execute(lambda: normalize_project_source_ref(store.project_dir, body.source_ref))
    item = _execute(lambda: store.create_item(
        track_id,
        kind=body.kind,
        name=body.name,
        source_element_id=body.source_element_id,
        source_ref=source_ref,
        start=body.start,
        duration=body.duration,
        source_in=body.source_in,
        metadata=body.metadata,
        actor=_actor(member),
    ))
    return {"item": item.model_dump(mode="json"), "editor": _state(store)}


@router.patch("/projects/{project_name}/editor/items/{item_id}")
def patch_item(project_name: str, item_id: str, body: PatchRequest, request: Request):
    member = _member(request)
    store = _store(project_name)
    item = _execute(lambda: store.patch_item(item_id, body.changes, actor=_actor(member)))
    return {"item": item.model_dump(mode="json"), "editor": _state(store)}


@router.post("/projects/{project_name}/editor/items/{item_id}/keyframes")
def set_keyframes(project_name: str, item_id: str, body: KeyframesRequest, request: Request):
    member = _member(request)
    _require_pro(member)
    store = _store(project_name)
    item = _execute(lambda: store.set_item_keyframes(item_id, body.parameter, body.keyframes, actor=_actor(member)))
    return {"item": item.model_dump(mode="json"), "editor": _state(store)}


@router.post("/projects/{project_name}/editor/items/{item_id}/masks")
def add_mask(project_name: str, item_id: str, body: MaskRequest, request: Request):
    member = _member(request)
    _require_pro(member)
    store = _store(project_name)
    mask = _execute(lambda: store.add_mask(item_id, EditorMask.model_validate(body.model_dump()), actor=_actor(member)))
    return {"mask": mask.model_dump(mode="json"), "editor": _state(store)}


@router.post("/projects/{project_name}/editor/{target_type}/{target_id}/effects")
def add_effect(
    project_name: str,
    target_type: Literal["item", "track"],
    target_id: str,
    body: EffectRequest,
    request: Request,
):
    member = _member(request)
    _require_pro(member)
    store = _store(project_name)
    effect = _execute(lambda: store.add_effect(target_type, target_id, EditorEffect.model_validate(body.model_dump()), actor=_actor(member)))
    return {"effect": effect.model_dump(mode="json"), "editor": _state(store)}


@router.post("/projects/{project_name}/editor/items/{item_id}/split")
def split_item(project_name: str, item_id: str, body: SplitRequest, request: Request):
    member = _member(request)
    store = _store(project_name)
    left, right = _execute(lambda: store.split_item(item_id, body.time, actor=_actor(member)))
    return {"left": left.model_dump(mode="json"), "right": right.model_dump(mode="json"), "editor": _state(store)}


@router.post("/projects/{project_name}/editor/items/{item_id}/ripple-delete")
def ripple_delete(project_name: str, item_id: str, request: Request):
    member = _member(request)
    store = _store(project_name)
    shifted = _execute(lambda: store.ripple_delete(item_id, actor=_actor(member)))
    return {"shifted": [item.model_dump(mode="json") for item in shifted], "editor": _state(store)}


@router.post("/projects/{project_name}/editor/items/{item_id}/slip")
def slip_item(project_name: str, item_id: str, body: SlipRequest, request: Request):
    member = _member(request)
    store = _store(project_name)
    item = _execute(lambda: store.slip_item(item_id, body.delta, actor=_actor(member)))
    return {"item": item.model_dump(mode="json"), "editor": _state(store)}


@router.post("/projects/{project_name}/editor/items/{item_id}/roll")
def roll_item(project_name: str, item_id: str, body: RollRequest, request: Request):
    member = _member(request)
    store = _store(project_name)
    left, right = _execute(lambda: store.roll_edit(item_id, body.right_item_id, body.delta, actor=_actor(member)))
    return {"left": left.model_dump(mode="json"), "right": right.model_dump(mode="json"), "editor": _state(store)}


@router.post("/projects/{project_name}/editor/items/{item_id}/duplicate")
def duplicate_item(project_name: str, item_id: str, body: DuplicateRequest, request: Request):
    member = _member(request)
    store = _store(project_name)
    item = _execute(lambda: store.duplicate_item(item_id, start=body.start, actor=_actor(member)))
    return {"item": item.model_dump(mode="json"), "editor": _state(store)}


@router.post("/projects/{project_name}/editor/sequences/{sequence_id}/reorder-track")
def reorder_track(project_name: str, sequence_id: str, body: ReorderTrackRequest, request: Request):
    member = _member(request)
    store = _store(project_name)
    sequence = _execute(lambda: store.reorder_track(sequence_id, body.track_id, body.index, actor=_actor(member)))
    return {"sequence": sequence.model_dump(mode="json"), "editor": _state(store)}


@router.post("/projects/{project_name}/editor/undo")
def undo(project_name: str, request: Request):
    _member(request)
    store = _store(project_name)
    operation = _execute(store.undo)
    return {"operation": operation.model_dump(mode="json"), "editor": _state(store)}


@router.post("/projects/{project_name}/editor/redo")
def redo(project_name: str, request: Request):
    _member(request)
    store = _store(project_name)
    operation = _execute(store.redo)
    return {"operation": operation.model_dump(mode="json"), "editor": _state(store)}


@router.post("/projects/{project_name}/editor/branches")
def create_branch(project_name: str, body: BranchRequest, request: Request):
    member = _member(request)
    _require_pro(member)
    store = _store(project_name)
    branch = _execute(lambda: store.create_branch(body.name, actor=_actor(member)))
    return {"branch": branch.model_dump(mode="json"), "editor": _state(store)}


@router.post("/projects/{project_name}/editor/branches/{branch_id}/checkout")
def checkout_branch(project_name: str, branch_id: str, request: Request):
    member = _member(request)
    _require_pro(member)
    store = _store(project_name)
    branch = _execute(lambda: store.checkout_branch(branch_id))
    return {"branch": branch.model_dump(mode="json"), "editor": _state(store)}


@router.get("/projects/{project_name}/editor/branches/compare")
def compare_branches(
    project_name: str,
    request: Request,
    left: str = Query(min_length=1, max_length=200),
    right: str = Query(min_length=1, max_length=200),
):
    member = _member(request)
    _require_pro(member)
    store = _store(project_name)
    return _execute(lambda: store.compare_branches(left, right))


__all__ = ["router"]
