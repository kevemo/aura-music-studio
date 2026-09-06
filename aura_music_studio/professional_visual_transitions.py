from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .plans import AUTOMATION
from .professional_editor import ProfessionalEditorStore
from .professional_editor_api import _actor, _execute, _member, _state, _store


router = APIRouter(prefix="/creative", tags=["Professional Visual Transitions"])

VISUAL_TRANSITIONS_KEY = "visual_transitions_v1"
VisualTransitionKind = Literal["fade_in", "fade_out", "cross_dissolve"]
VisualTransitionEasing = Literal["linear"]
_VISUAL_ITEM_KINDS = {"video_clip", "image_layer"}


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EditorVisualTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: f"transition_{uuid4().hex}", min_length=1, max_length=200)
    kind: VisualTransitionKind
    from_item_id: str | None = Field(default=None, max_length=200)
    to_item_id: str | None = Field(default=None, max_length=200)
    duration: float = Field(default=0.5, gt=0.0, le=10.0)
    easing: VisualTransitionEasing = "linear"
    enabled: bool = True
    provenance: Literal["canonical_chat3_rewrite"] = "canonical_chat3_rewrite"


class CreateVisualTransitionRequest(StrictRequest):
    kind: VisualTransitionKind
    from_item_id: str | None = Field(default=None, max_length=200)
    to_item_id: str | None = Field(default=None, max_length=200)
    duration: float = Field(default=0.5, gt=0.0, le=10.0)
    easing: VisualTransitionEasing = "linear"
    enabled: bool = True


class PatchVisualTransitionRequest(StrictRequest):
    duration: float | None = Field(default=None, gt=0.0, le=10.0)
    easing: VisualTransitionEasing | None = None
    enabled: bool | None = None


def _require_pro(member) -> None:
    if not member.plan.has(AUTOMATION):
        raise HTTPException(403, "Professional visual transitions require Pro")


def transitions_from_sequence(sequence: dict[str, Any]) -> list[EditorVisualTransition]:
    metadata = sequence.get("metadata") or {}
    raw = metadata.get(VISUAL_TRANSITIONS_KEY, [])
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("Visual transition state is invalid")
    return [EditorVisualTransition.model_validate(value) for value in raw]


def _sequence_maps(store: ProfessionalEditorStore, sequence_id: str):
    state = store.public_state()
    branch = state.get("branch") or {}
    sequences = {row.get("id"): row for row in branch.get("sequences", [])}
    tracks = {row.get("id"): row for row in branch.get("tracks", [])}
    items = {row.get("id"): row for row in branch.get("items", [])}
    sequence = sequences.get(sequence_id)
    if sequence is None:
        raise KeyError(sequence_id)
    if sequence.get("kind") != "video":
        raise ValueError("Visual transitions require a video sequence")

    item_tracks: dict[str, dict[str, Any]] = {}
    for track_id in sequence.get("track_ids", []):
        track = tracks.get(track_id)
        if not track:
            continue
        for item_id in track.get("item_ids", []):
            if item_id in items:
                item_tracks[item_id] = track
    return sequence, tracks, items, item_tracks


def _visual_item(
    item_id: str | None,
    *,
    role: str,
    items: dict[str, dict[str, Any]],
    item_tracks: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not item_id:
        raise ValueError(f"{role} item is required")
    item = items.get(item_id)
    track = item_tracks.get(item_id)
    if item is None or track is None:
        raise ValueError(f"{role} item must belong to the target sequence")
    if item.get("kind") not in _VISUAL_ITEM_KINDS:
        raise ValueError(f"{role} item must be a video_clip or image_layer")
    if track.get("kind") != "video":
        raise ValueError(f"{role} item must be on a video track")
    if not item.get("enabled", True) or not item.get("visible", True):
        raise ValueError(f"{role} item must be enabled and visible")
    return item, track


def validate_visual_transition(
    store: ProfessionalEditorStore,
    sequence_id: str,
    transition: EditorVisualTransition,
    *,
    existing: list[EditorVisualTransition] | None = None,
) -> None:
    sequence, _tracks, items, item_tracks = _sequence_maps(store, sequence_id)
    duration = float(transition.duration)
    active_existing = [
        row for row in (existing if existing is not None else transitions_from_sequence(sequence))
        if row.id != transition.id and row.enabled
    ]

    incoming_id: str | None = None
    outgoing_id: str | None = None

    if transition.kind == "fade_in":
        if transition.from_item_id is not None:
            raise ValueError("fade_in does not accept from_item_id")
        item, _track = _visual_item(
            transition.to_item_id, role="Fade-in", items=items, item_tracks=item_tracks
        )
        if duration > float(item.get("duration") or 0.0):
            raise ValueError("Fade-in duration cannot exceed the item duration")
        incoming_id = item["id"]

    elif transition.kind == "fade_out":
        if transition.to_item_id is not None:
            raise ValueError("fade_out does not accept to_item_id")
        item, _track = _visual_item(
            transition.from_item_id, role="Fade-out", items=items, item_tracks=item_tracks
        )
        if duration > float(item.get("duration") or 0.0):
            raise ValueError("Fade-out duration cannot exceed the item duration")
        outgoing_id = item["id"]

    else:
        if not transition.from_item_id or not transition.to_item_id:
            raise ValueError("cross_dissolve requires from_item_id and to_item_id")
        if transition.from_item_id == transition.to_item_id:
            raise ValueError("cross_dissolve requires two different visual items")
        left, left_track = _visual_item(
            transition.from_item_id, role="Outgoing", items=items, item_tracks=item_tracks
        )
        right, right_track = _visual_item(
            transition.to_item_id, role="Incoming", items=items, item_tracks=item_tracks
        )
        if left_track.get("id") != right_track.get("id"):
            raise ValueError("cross_dissolve requires both items on the same video track")
        if duration > min(float(left.get("duration") or 0.0), float(right.get("duration") or 0.0)):
            raise ValueError("Cross-dissolve duration cannot exceed either item duration")

        ordered = list(left_track.get("item_ids") or [])
        left_index = ordered.index(left["id"])
        right_index = ordered.index(right["id"])
        if right_index != left_index + 1:
            raise ValueError("cross_dissolve requires adjacent outgoing and incoming items in track order")

        left_end = float(left.get("start") or 0.0) + float(left.get("duration") or 0.0)
        expected_right_start = left_end - duration
        right_start = float(right.get("start") or 0.0)
        fps = max(1.0, float(sequence.get("fps") or 24.0))
        tolerance = 0.51 / fps
        if abs(right_start - expected_right_start) > tolerance:
            raise ValueError(
                "Cross-dissolve requires clip overlap equal to the transition duration; overlap the incoming clip on the timeline first"
            )
        incoming_id = right["id"]
        outgoing_id = left["id"]

    for row in active_existing:
        row_incoming = row.to_item_id if row.kind in {"fade_in", "cross_dissolve"} else None
        row_outgoing = row.from_item_id if row.kind in {"fade_out", "cross_dissolve"} else None
        if incoming_id and row_incoming == incoming_id:
            raise ValueError("A visual item may have only one active incoming transition")
        if outgoing_id and row_outgoing == outgoing_id:
            raise ValueError("A visual item may have only one active outgoing transition")


def _persist(
    store: ProfessionalEditorStore,
    sequence_id: str,
    transitions: list[EditorVisualTransition],
    *,
    actor: str,
):
    return store.patch_sequence(
        sequence_id,
        {
            "metadata": {
                VISUAL_TRANSITIONS_KEY: [row.model_dump(mode="json") for row in transitions]
            }
        },
        actor=actor,
    )


def create_visual_transition(
    store: ProfessionalEditorStore,
    sequence_id: str,
    body: CreateVisualTransitionRequest,
    *,
    actor: str,
) -> EditorVisualTransition:
    sequence, _tracks, _items, _item_tracks = _sequence_maps(store, sequence_id)
    existing = transitions_from_sequence(sequence)
    transition = EditorVisualTransition.model_validate(body.model_dump())
    validate_visual_transition(store, sequence_id, transition, existing=existing)
    _persist(store, sequence_id, [*existing, transition], actor=actor)
    return transition


def patch_visual_transition(
    store: ProfessionalEditorStore,
    sequence_id: str,
    transition_id: str,
    body: PatchVisualTransitionRequest,
    *,
    actor: str,
) -> EditorVisualTransition:
    sequence, _tracks, _items, _item_tracks = _sequence_maps(store, sequence_id)
    existing = transitions_from_sequence(sequence)
    current = next((row for row in existing if row.id == transition_id), None)
    if current is None:
        raise KeyError(transition_id)
    changes = body.model_dump(exclude_none=True)
    updated = EditorVisualTransition.model_validate({**current.model_dump(mode="json"), **changes})
    validate_visual_transition(store, sequence_id, updated, existing=existing)
    rows = [updated if row.id == transition_id else row for row in existing]
    _persist(store, sequence_id, rows, actor=actor)
    return updated


def delete_visual_transition(
    store: ProfessionalEditorStore,
    sequence_id: str,
    transition_id: str,
    *,
    actor: str,
) -> EditorVisualTransition:
    sequence, _tracks, _items, _item_tracks = _sequence_maps(store, sequence_id)
    existing = transitions_from_sequence(sequence)
    current = next((row for row in existing if row.id == transition_id), None)
    if current is None:
        raise KeyError(transition_id)
    _persist(store, sequence_id, [row for row in existing if row.id != transition_id], actor=actor)
    return current


@router.get("/projects/{project_name}/editor/sequences/{sequence_id}/visual-transitions")
def list_visual_transitions(project_name: str, sequence_id: str, request: Request):
    member = _member(request)
    _require_pro(member)
    store = _store(project_name)
    sequence, _tracks, _items, _item_tracks = _execute(lambda: _sequence_maps(store, sequence_id))
    rows = _execute(lambda: transitions_from_sequence(sequence))
    for row in rows:
        _execute(lambda row=row: validate_visual_transition(store, sequence_id, row, existing=rows))
    return {
        "transitions": [row.model_dump(mode="json") for row in rows],
        "supported": ["fade_in", "fade_out", "cross_dissolve"],
        "easing": ["linear"],
        "audio_crossfade": False,
        "arbitrary_filter_strings": False,
    }


@router.post("/projects/{project_name}/editor/sequences/{sequence_id}/visual-transitions")
def add_visual_transition(
    project_name: str,
    sequence_id: str,
    body: CreateVisualTransitionRequest,
    request: Request,
):
    member = _member(request)
    _require_pro(member)
    store = _store(project_name)
    transition = _execute(
        lambda: create_visual_transition(store, sequence_id, body, actor=_actor(member))
    )
    return {"transition": transition.model_dump(mode="json"), "editor": _state(store)}


@router.patch("/projects/{project_name}/editor/sequences/{sequence_id}/visual-transitions/{transition_id}")
def update_visual_transition(
    project_name: str,
    sequence_id: str,
    transition_id: str,
    body: PatchVisualTransitionRequest,
    request: Request,
):
    member = _member(request)
    _require_pro(member)
    store = _store(project_name)
    transition = _execute(
        lambda: patch_visual_transition(
            store, sequence_id, transition_id, body, actor=_actor(member)
        )
    )
    return {"transition": transition.model_dump(mode="json"), "editor": _state(store)}


@router.delete("/projects/{project_name}/editor/sequences/{sequence_id}/visual-transitions/{transition_id}")
def remove_visual_transition(
    project_name: str,
    sequence_id: str,
    transition_id: str,
    request: Request,
):
    member = _member(request)
    _require_pro(member)
    store = _store(project_name)
    transition = _execute(
        lambda: delete_visual_transition(
            store, sequence_id, transition_id, actor=_actor(member)
        )
    )
    return {"removed": transition.model_dump(mode="json"), "editor": _state(store)}


__all__ = [
    "EditorVisualTransition",
    "VISUAL_TRANSITIONS_KEY",
    "create_visual_transition",
    "delete_visual_transition",
    "patch_visual_transition",
    "router",
    "transitions_from_sequence",
    "validate_visual_transition",
]
