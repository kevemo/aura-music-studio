from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


EditorMediaKind = Literal["video", "image"]
EditorTrackKind = Literal["video", "audio", "image", "text", "adjustment", "group"]
EditorItemKind = Literal["video_clip", "audio_clip", "image_layer", "text", "adjustment", "generator"]
EditorTargetType = Literal["sequence", "track", "item"]
Interpolation = Literal["hold", "linear", "smooth", "bezier"]
MaskMode = Literal["add", "subtract", "intersect"]
BlendMode = Literal[
    "normal",
    "multiply",
    "screen",
    "overlay",
    "soft_light",
    "hard_light",
    "darken",
    "lighten",
    "difference",
]

EDITOR_FILENAME = "pro_editor.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _default_transform() -> dict[str, float]:
    return {
        "x": 0.0,
        "y": 0.0,
        "scale_x": 1.0,
        "scale_y": 1.0,
        "rotation": 0.0,
        "anchor_x": 0.5,
        "anchor_y": 0.5,
    }


def _default_crop() -> dict[str, float]:
    return {"left": 0.0, "top": 0.0, "right": 0.0, "bottom": 0.0}


def _default_color() -> dict[str, float]:
    return {
        "exposure": 0.0,
        "contrast": 1.0,
        "saturation": 1.0,
        "brightness": 0.0,
        "gamma": 1.0,
        "temperature": 0.0,
        "tint": 0.0,
        "highlights": 0.0,
        "shadows": 0.0,
    }


def _default_audio() -> dict[str, float | bool]:
    return {
        "gain_db": 0.0,
        "pan": 0.0,
        "fade_in": 0.0,
        "fade_out": 0.0,
        "muted": False,
    }


class EditorKeyframe(BaseModel):
    id: str = Field(default_factory=lambda: _id("kf"))
    time: float = Field(ge=0.0, le=86400.0)
    value: Any
    interpolation: Interpolation = "linear"
    tension: float = Field(default=0.0, ge=-1.0, le=1.0)
    in_handle: tuple[float, float] | None = None
    out_handle: tuple[float, float] | None = None


class EditorMask(BaseModel):
    id: str = Field(default_factory=lambda: _id("mask"))
    name: str = Field(default="Mask", min_length=1, max_length=120)
    mode: MaskMode = "add"
    enabled: bool = True
    inverted: bool = False
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    feather: float = Field(default=0.0, ge=0.0, le=1000.0)
    expansion: float = Field(default=0.0, ge=-1000.0, le=1000.0)
    shape: Literal["rectangle", "ellipse", "polygon", "path"] = "rectangle"
    # Points are normalized 0..1 canvas coordinates so masks survive resolution changes.
    points: list[tuple[float, float]] = Field(default_factory=list, max_length=4096)
    tracking: dict[str, Any] = Field(default_factory=dict)
    keyframes: dict[str, list[EditorKeyframe]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_points(self):
        self.points = [(_clamp(x, 0.0, 1.0), _clamp(y, 0.0, 1.0)) for x, y in self.points]
        return self


class EditorEffect(BaseModel):
    id: str = Field(default_factory=lambda: _id("fx"))
    type: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    mix: float = Field(default=1.0, ge=0.0, le=1.0)
    parameters: dict[str, Any] = Field(default_factory=dict)
    keyframes: dict[str, list[EditorKeyframe]] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EditorItem(BaseModel):
    id: str = Field(default_factory=lambda: _id("item"))
    kind: EditorItemKind
    name: str = Field(min_length=1, max_length=200)
    source_element_id: str | None = Field(default=None, max_length=200)
    source_ref: str | None = Field(default=None, max_length=1000)
    start: float = Field(default=0.0, ge=0.0, le=86400.0)
    duration: float = Field(default=5.0, gt=0.0, le=86400.0)
    source_in: float = Field(default=0.0, ge=0.0, le=86400.0)
    source_out: float | None = Field(default=None, ge=0.0, le=86400.0)
    speed: float = Field(default=1.0, ge=0.05, le=20.0)
    reverse: bool = False
    enabled: bool = True
    locked: bool = False
    visible: bool = True
    blend_mode: BlendMode = "normal"
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    transform: dict[str, float] = Field(default_factory=_default_transform)
    crop: dict[str, float] = Field(default_factory=_default_crop)
    color: dict[str, float] = Field(default_factory=_default_color)
    audio: dict[str, float | bool] = Field(default_factory=_default_audio)
    text: dict[str, Any] = Field(default_factory=dict)
    keyframes: dict[str, list[EditorKeyframe]] = Field(default_factory=dict)
    masks: list[EditorMask] = Field(default_factory=list)
    effects: list[EditorEffect] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    @model_validator(mode="after")
    def normalize_item(self):
        self.transform = {**_default_transform(), **self.transform}
        self.crop = {**_default_crop(), **self.crop}
        self.color = {**_default_color(), **self.color}
        self.audio = {**_default_audio(), **self.audio}
        self.transform["scale_x"] = _clamp(self.transform["scale_x"], 0.01, 100.0)
        self.transform["scale_y"] = _clamp(self.transform["scale_y"], 0.01, 100.0)
        self.transform["rotation"] = float(self.transform["rotation"]) % 360.0
        self.transform["anchor_x"] = _clamp(self.transform["anchor_x"], 0.0, 1.0)
        self.transform["anchor_y"] = _clamp(self.transform["anchor_y"], 0.0, 1.0)
        for key in ("left", "top", "right", "bottom"):
            self.crop[key] = _clamp(self.crop[key], 0.0, 0.99)
        if self.crop["left"] + self.crop["right"] >= 0.99:
            raise ValueError("Horizontal crop removes the entire source")
        if self.crop["top"] + self.crop["bottom"] >= 0.99:
            raise ValueError("Vertical crop removes the entire source")
        if self.source_out is not None and self.source_out <= self.source_in:
            raise ValueError("source_out must be after source_in")
        return self


class EditorTrack(BaseModel):
    id: str = Field(default_factory=lambda: _id("track"))
    kind: EditorTrackKind
    name: str = Field(min_length=1, max_length=160)
    role: str = Field(default="", max_length=120)
    item_ids: list[str] = Field(default_factory=list, max_length=10000)
    enabled: bool = True
    locked: bool = False
    visible: bool = True
    muted: bool = False
    solo: bool = False
    blend_mode: BlendMode = "normal"
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    effects: list[EditorEffect] = Field(default_factory=list)
    keyframes: dict[str, list[EditorKeyframe]] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class EditorMarker(BaseModel):
    id: str = Field(default_factory=lambda: _id("marker"))
    time: float = Field(ge=0.0, le=86400.0)
    name: str = Field(min_length=1, max_length=160)
    kind: Literal["marker", "chapter", "beat", "lyric", "comment", "range"] = "marker"
    end: float | None = Field(default=None, ge=0.0, le=86400.0)
    color: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class EditorSequence(BaseModel):
    id: str = Field(default_factory=lambda: _id("seq"))
    kind: EditorMediaKind
    name: str = Field(min_length=1, max_length=180)
    width: int = Field(default=1920, ge=64, le=16384)
    height: int = Field(default=1080, ge=64, le=16384)
    fps: float = Field(default=24.0, ge=1.0, le=240.0)
    duration: float = Field(default=30.0, gt=0.0, le=86400.0)
    background: str = "#000000"
    track_ids: list[str] = Field(default_factory=list, max_length=4096)
    markers: list[EditorMarker] = Field(default_factory=list)
    locked: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class EditorOperation(BaseModel):
    id: str = Field(default_factory=lambda: _id("op"))
    operation: str = Field(min_length=1, max_length=120)
    label: str = Field(default="Edit", max_length=220)
    actor: str = Field(default="Member", max_length=160)
    target_type: EditorTargetType | Literal["document"] = "document"
    target_id: str | None = None
    # Resource snapshots are keyed as sequence:<id>, track:<id>, item:<id>.
    # A null value means the resource did not exist on that side of the operation.
    before: dict[str, dict[str, Any] | None] = Field(default_factory=dict)
    after: dict[str, dict[str, Any] | None] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)


class EditorBranch(BaseModel):
    id: str = Field(default_factory=lambda: _id("branch"))
    name: str = Field(min_length=1, max_length=120)
    parent_branch_id: str | None = None
    forked_from_operation_id: str | None = None
    sequences: list[EditorSequence] = Field(default_factory=list)
    tracks: list[EditorTrack] = Field(default_factory=list)
    items: list[EditorItem] = Field(default_factory=list)
    operations: list[EditorOperation] = Field(default_factory=list)
    undo_stack: list[str] = Field(default_factory=list)
    redo_stack: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class ProfessionalEditorProject(BaseModel):
    schema_version: int = 1
    project_name: str = Field(min_length=1, max_length=120)
    active_branch_id: str
    branches: list[EditorBranch] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class ProfessionalEditorStore:
    """Non-destructive shared edit graph for image/video work.

    Source media is never rewritten. Every edit mutates only this metadata graph, while
    operations retain the exact resources needed for undo/redo. Editor branches clone only
    metadata, so A/B versions stay cheap even for large media projects.
    """

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self.path = self.project_dir / "work" / EDITOR_FILENAME

    def exists(self) -> bool:
        return self.path.is_file()

    def initialize(self, project_name: str) -> ProfessionalEditorProject:
        if self.exists():
            return self.load()
        branch = EditorBranch(id="branch_main", name="Main")
        project = ProfessionalEditorProject(
            project_name=project_name,
            active_branch_id=branch.id,
            branches=[branch],
        )
        return self.save(project)

    def load(self) -> ProfessionalEditorProject:
        if not self.exists():
            raise FileNotFoundError(self.path)
        return ProfessionalEditorProject.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, project: ProfessionalEditorProject) -> ProfessionalEditorProject:
        project.updated_at = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(project.model_dump(mode="json"), indent=2, ensure_ascii=False)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(payload + "\n", encoding="utf-8")
        temporary.replace(self.path)
        return project

    @staticmethod
    def _branch(project: ProfessionalEditorProject, branch_id: str | None = None) -> EditorBranch:
        wanted = branch_id or project.active_branch_id
        branch = next((item for item in project.branches if item.id == wanted), None)
        if branch is None:
            raise KeyError(wanted)
        return branch

    @staticmethod
    def _sequence(branch: EditorBranch, sequence_id: str) -> EditorSequence:
        sequence = next((item for item in branch.sequences if item.id == sequence_id), None)
        if sequence is None:
            raise KeyError(sequence_id)
        return sequence

    @staticmethod
    def _track(branch: EditorBranch, track_id: str) -> EditorTrack:
        track = next((item for item in branch.tracks if item.id == track_id), None)
        if track is None:
            raise KeyError(track_id)
        return track

    @staticmethod
    def _item(branch: EditorBranch, item_id: str) -> EditorItem:
        item = next((value for value in branch.items if value.id == item_id), None)
        if item is None:
            raise KeyError(item_id)
        return item

    @staticmethod
    def _resource_key(kind: EditorTargetType, resource_id: str) -> str:
        return f"{kind}:{resource_id}"

    @classmethod
    def _capture(cls, branch: EditorBranch, resources: list[tuple[EditorTargetType, str]]) -> dict[str, dict | None]:
        result: dict[str, dict | None] = {}
        for kind, resource_id in resources:
            key = cls._resource_key(kind, resource_id)
            try:
                if kind == "sequence":
                    value = cls._sequence(branch, resource_id)
                elif kind == "track":
                    value = cls._track(branch, resource_id)
                else:
                    value = cls._item(branch, resource_id)
                result[key] = value.model_dump(mode="json")
            except KeyError:
                result[key] = None
        return result

    @staticmethod
    def _set_resource(branch: EditorBranch, key: str, value: dict | None) -> None:
        kind, resource_id = key.split(":", 1)
        if kind == "sequence":
            collection = branch.sequences
            model = EditorSequence
        elif kind == "track":
            collection = branch.tracks
            model = EditorTrack
        elif kind == "item":
            collection = branch.items
            model = EditorItem
        else:
            raise ValueError(f"Unknown editor resource kind: {kind}")
        index = next((i for i, item in enumerate(collection) if item.id == resource_id), None)
        if value is None:
            if index is not None:
                del collection[index]
            return
        restored = model.model_validate(value)
        if index is None:
            collection.append(restored)
        else:
            collection[index] = restored

    @classmethod
    def _apply_snapshot(cls, branch: EditorBranch, snapshot: dict[str, dict | None]) -> None:
        # Parents first on restore, children first on deletion. This keeps relation cleanup
        # deterministic when an operation contains a sequence, track and item together.
        order = {"sequence": 0, "track": 1, "item": 2}
        keys = sorted(snapshot, key=lambda key: order.get(key.split(":", 1)[0], 99))
        for key in keys:
            cls._set_resource(branch, key, snapshot[key])
        cls._repair_relations(branch)

    @staticmethod
    def _repair_relations(branch: EditorBranch) -> None:
        track_ids = {track.id for track in branch.tracks}
        item_ids = {item.id for item in branch.items}
        for sequence in branch.sequences:
            sequence.track_ids = [value for value in sequence.track_ids if value in track_ids]
        for track in branch.tracks:
            track.item_ids = [value for value in track.item_ids if value in item_ids]

    @staticmethod
    def _touch(branch: EditorBranch, *resources) -> None:
        now = _now()
        branch.updated_at = now
        for resource in resources:
            if hasattr(resource, "updated_at"):
                resource.updated_at = now

    @staticmethod
    def _locked(branch: EditorBranch, target_type: EditorTargetType, target_id: str) -> bool:
        if target_type == "sequence":
            return ProfessionalEditorStore._sequence(branch, target_id).locked
        if target_type == "track":
            return ProfessionalEditorStore._track(branch, target_id).locked
        item = ProfessionalEditorStore._item(branch, target_id)
        if item.locked:
            return True
        parent = next((track for track in branch.tracks if item.id in track.item_ids), None)
        return bool(parent and parent.locked)

    @classmethod
    def _record(
        cls,
        branch: EditorBranch,
        *,
        operation: str,
        label: str,
        before: dict[str, dict | None],
        after: dict[str, dict | None],
        actor: str,
        target_type: EditorTargetType | Literal["document"] = "document",
        target_id: str | None = None,
        metadata: dict | None = None,
    ) -> EditorOperation:
        entry = EditorOperation(
            operation=operation,
            label=label,
            actor=actor,
            target_type=target_type,
            target_id=target_id,
            before=before,
            after=after,
            metadata=metadata or {},
        )
        branch.operations.append(entry)
        branch.undo_stack.append(entry.id)
        branch.redo_stack.clear()
        branch.updated_at = _now()
        return entry

    def public_state(self, branch_id: str | None = None) -> dict[str, Any]:
        project = self.load()
        branch = self._branch(project, branch_id)
        return {
            "schema_version": project.schema_version,
            "project_name": project.project_name,
            "active_branch_id": project.active_branch_id,
            "branch": branch.model_dump(mode="json"),
            "branches": [
                {
                    "id": item.id,
                    "name": item.name,
                    "parent_branch_id": item.parent_branch_id,
                    "forked_from_operation_id": item.forked_from_operation_id,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                    "is_active": item.id == project.active_branch_id,
                    "operations": len(item.operations),
                }
                for item in project.branches
            ],
            "source_media_mutated": False,
        }

    def create_sequence(
        self,
        *,
        kind: EditorMediaKind,
        name: str,
        width: int,
        height: int,
        fps: float = 24.0,
        duration: float = 30.0,
        actor: str = "Member",
    ) -> EditorSequence:
        project = self.load()
        branch = self._branch(project)
        sequence = EditorSequence(
            kind=kind,
            name=name,
            width=width,
            height=height,
            fps=fps if kind == "video" else 1.0,
            duration=duration if kind == "video" else 1.0,
            background="#00000000" if kind == "image" else "#000000",
        )
        key = self._resource_key("sequence", sequence.id)
        before = {key: None}
        branch.sequences.append(sequence)
        self._touch(branch, sequence)
        after = self._capture(branch, [("sequence", sequence.id)])
        self._record(
            branch,
            operation="create_sequence",
            label=f"Create {kind} sequence {name}",
            before=before,
            after=after,
            actor=actor,
            target_type="sequence",
            target_id=sequence.id,
        )
        self.save(project)
        return sequence

    def create_track(
        self,
        sequence_id: str,
        *,
        kind: EditorTrackKind,
        name: str,
        role: str = "",
        actor: str = "Member",
    ) -> EditorTrack:
        project = self.load()
        branch = self._branch(project)
        sequence = self._sequence(branch, sequence_id)
        if sequence.locked:
            raise PermissionError("Sequence is locked")
        track = EditorTrack(kind=kind, name=name, role=role)
        resources = [("sequence", sequence.id), ("track", track.id)]
        before = self._capture(branch, resources)
        branch.tracks.append(track)
        sequence.track_ids.append(track.id)
        self._touch(branch, sequence, track)
        after = self._capture(branch, resources)
        self._record(
            branch,
            operation="create_track",
            label=f"Create track {name}",
            before=before,
            after=after,
            actor=actor,
            target_type="track",
            target_id=track.id,
        )
        self.save(project)
        return track

    def create_item(
        self,
        track_id: str,
        *,
        kind: EditorItemKind,
        name: str,
        source_element_id: str | None = None,
        source_ref: str | None = None,
        start: float = 0.0,
        duration: float = 5.0,
        source_in: float = 0.0,
        metadata: dict | None = None,
        actor: str = "Member",
    ) -> EditorItem:
        project = self.load()
        branch = self._branch(project)
        track = self._track(branch, track_id)
        if track.locked:
            raise PermissionError("Track is locked")
        item = EditorItem(
            kind=kind,
            name=name,
            source_element_id=source_element_id,
            source_ref=source_ref,
            start=start,
            duration=duration,
            source_in=source_in,
            metadata=metadata or {},
        )
        resources = [("track", track.id), ("item", item.id)]
        before = self._capture(branch, resources)
        branch.items.append(item)
        track.item_ids.append(item.id)
        self._touch(branch, track, item)
        after = self._capture(branch, resources)
        self._record(
            branch,
            operation="create_item",
            label=f"Add {name}",
            before=before,
            after=after,
            actor=actor,
            target_type="item",
            target_id=item.id,
        )
        self.save(project)
        return item

    @staticmethod
    def _merge_mapping(current: dict, changes: dict) -> dict:
        merged = deepcopy(current)
        for key, value in changes.items():
            merged[key] = value
        return merged

    def patch_item(self, item_id: str, changes: dict[str, Any], *, actor: str = "Member") -> EditorItem:
        project = self.load()
        branch = self._branch(project)
        item = self._item(branch, item_id)
        if self._locked(branch, "item", item.id):
            raise PermissionError("Item or parent track is locked")
        allowed = {
            "name", "start", "duration", "source_in", "source_out", "speed", "reverse",
            "enabled", "locked", "visible", "blend_mode", "opacity", "transform", "crop",
            "color", "audio", "text", "metadata",
        }
        unknown = sorted(set(changes) - allowed)
        if unknown:
            raise ValueError(f"Unsupported item field(s): {', '.join(unknown)}")
        before = self._capture(branch, [("item", item.id)])
        payload = item.model_dump(mode="json")
        for key, value in changes.items():
            if key in {"transform", "crop", "color", "audio", "text", "metadata"} and isinstance(value, dict):
                payload[key] = self._merge_mapping(payload.get(key) or {}, value)
            else:
                payload[key] = value
        payload["updated_at"] = _now()
        updated = EditorItem.model_validate(payload)
        index = next(i for i, value in enumerate(branch.items) if value.id == item.id)
        branch.items[index] = updated
        self._touch(branch, updated)
        after = self._capture(branch, [("item", item.id)])
        self._record(
            branch,
            operation="patch_item",
            label=f"Edit {updated.name}",
            before=before,
            after=after,
            actor=actor,
            target_type="item",
            target_id=item.id,
            metadata={"fields": sorted(changes)},
        )
        self.save(project)
        return updated

    def patch_track(self, track_id: str, changes: dict[str, Any], *, actor: str = "Member") -> EditorTrack:
        project = self.load()
        branch = self._branch(project)
        track = self._track(branch, track_id)
        if track.locked and set(changes) != {"locked"}:
            raise PermissionError("Track is locked")
        allowed = {"name", "role", "enabled", "locked", "visible", "muted", "solo", "blend_mode", "opacity", "metadata"}
        unknown = sorted(set(changes) - allowed)
        if unknown:
            raise ValueError(f"Unsupported track field(s): {', '.join(unknown)}")
        before = self._capture(branch, [("track", track.id)])
        payload = track.model_dump(mode="json")
        for key, value in changes.items():
            if key == "metadata" and isinstance(value, dict):
                payload[key] = self._merge_mapping(payload.get(key) or {}, value)
            else:
                payload[key] = value
        payload["updated_at"] = _now()
        updated = EditorTrack.model_validate(payload)
        index = next(i for i, value in enumerate(branch.tracks) if value.id == track.id)
        branch.tracks[index] = updated
        self._touch(branch, updated)
        after = self._capture(branch, [("track", track.id)])
        self._record(
            branch,
            operation="patch_track",
            label=f"Edit track {updated.name}",
            before=before,
            after=after,
            actor=actor,
            target_type="track",
            target_id=track.id,
            metadata={"fields": sorted(changes)},
        )
        self.save(project)
        return updated

    def patch_sequence(self, sequence_id: str, changes: dict[str, Any], *, actor: str = "Member") -> EditorSequence:
        project = self.load()
        branch = self._branch(project)
        sequence = self._sequence(branch, sequence_id)
        if sequence.locked and set(changes) != {"locked"}:
            raise PermissionError("Sequence is locked")
        allowed = {"name", "width", "height", "fps", "duration", "background", "locked", "metadata"}
        unknown = sorted(set(changes) - allowed)
        if unknown:
            raise ValueError(f"Unsupported sequence field(s): {', '.join(unknown)}")
        before = self._capture(branch, [("sequence", sequence.id)])
        payload = sequence.model_dump(mode="json")
        for key, value in changes.items():
            if key == "metadata" and isinstance(value, dict):
                payload[key] = self._merge_mapping(payload.get(key) or {}, value)
            else:
                payload[key] = value
        payload["updated_at"] = _now()
        updated = EditorSequence.model_validate(payload)
        index = next(i for i, value in enumerate(branch.sequences) if value.id == sequence.id)
        branch.sequences[index] = updated
        self._touch(branch, updated)
        after = self._capture(branch, [("sequence", sequence.id)])
        self._record(
            branch,
            operation="patch_sequence",
            label=f"Edit sequence {updated.name}",
            before=before,
            after=after,
            actor=actor,
            target_type="sequence",
            target_id=sequence.id,
            metadata={"fields": sorted(changes)},
        )
        self.save(project)
        return updated

    def set_item_keyframes(
        self,
        item_id: str,
        parameter: str,
        keyframes: list[dict | EditorKeyframe],
        *,
        actor: str = "Member",
    ) -> EditorItem:
        clean_parameter = str(parameter or "").strip()
        if not clean_parameter or len(clean_parameter) > 160:
            raise ValueError("A valid keyframe parameter path is required")
        project = self.load()
        branch = self._branch(project)
        item = self._item(branch, item_id)
        if self._locked(branch, "item", item.id):
            raise PermissionError("Item or parent track is locked")
        before = self._capture(branch, [("item", item.id)])
        points = [value if isinstance(value, EditorKeyframe) else EditorKeyframe.model_validate(value) for value in keyframes]
        by_time: dict[float, EditorKeyframe] = {float(value.time): value for value in points}
        item.keyframes[clean_parameter] = [by_time[key] for key in sorted(by_time)]
        self._touch(branch, item)
        after = self._capture(branch, [("item", item.id)])
        self._record(
            branch,
            operation="set_keyframes",
            label=f"Keyframe {item.name} · {clean_parameter}",
            before=before,
            after=after,
            actor=actor,
            target_type="item",
            target_id=item.id,
            metadata={"parameter": clean_parameter, "keyframes": len(points)},
        )
        self.save(project)
        return item

    def add_mask(self, item_id: str, mask: EditorMask, *, actor: str = "Member") -> EditorMask:
        project = self.load()
        branch = self._branch(project)
        item = self._item(branch, item_id)
        if self._locked(branch, "item", item.id):
            raise PermissionError("Item or parent track is locked")
        before = self._capture(branch, [("item", item.id)])
        item.masks.append(mask)
        self._touch(branch, item)
        after = self._capture(branch, [("item", item.id)])
        self._record(
            branch,
            operation="add_mask",
            label=f"Add mask to {item.name}",
            before=before,
            after=after,
            actor=actor,
            target_type="item",
            target_id=item.id,
            metadata={"mask_id": mask.id},
        )
        self.save(project)
        return mask

    def add_effect(self, target_type: Literal["item", "track"], target_id: str, effect: EditorEffect, *, actor: str = "Member") -> EditorEffect:
        project = self.load()
        branch = self._branch(project)
        if self._locked(branch, target_type, target_id):
            raise PermissionError(f"{target_type.title()} is locked")
        target = self._item(branch, target_id) if target_type == "item" else self._track(branch, target_id)
        before = self._capture(branch, [(target_type, target_id)])
        target.effects.append(effect)
        self._touch(branch, target)
        after = self._capture(branch, [(target_type, target_id)])
        self._record(
            branch,
            operation="add_effect",
            label=f"Add {effect.type} effect",
            before=before,
            after=after,
            actor=actor,
            target_type=target_type,
            target_id=target_id,
            metadata={"effect_id": effect.id},
        )
        self.save(project)
        return effect

    def split_item(self, item_id: str, timeline_time: float, *, actor: str = "Member") -> tuple[EditorItem, EditorItem]:
        project = self.load()
        branch = self._branch(project)
        item = self._item(branch, item_id)
        if item.kind not in {"video_clip", "audio_clip"}:
            raise ValueError("Only timeline video/audio clips can be split")
        if self._locked(branch, "item", item.id):
            raise PermissionError("Item or parent track is locked")
        track = next((value for value in branch.tracks if item.id in value.item_ids), None)
        if track is None:
            raise ValueError("Timeline item is not attached to a track")
        relative = float(timeline_time) - item.start
        if relative <= 0.001 or relative >= item.duration - 0.001:
            raise ValueError("Split time must be inside the clip")
        resources = [("track", track.id), ("item", item.id)]
        before = self._capture(branch, resources)
        left = deepcopy(item)
        right = deepcopy(item)
        right.id = _id("item")
        left.duration = relative
        right.start = item.start + relative
        right.duration = item.duration - relative
        right.source_in = item.source_in + relative * item.speed
        left.source_out = right.source_in
        right.created_at = _now()
        left.updated_at = right.updated_at = _now()
        item_index = next(i for i, value in enumerate(branch.items) if value.id == item.id)
        branch.items[item_index] = left
        branch.items.append(right)
        track_index = track.item_ids.index(item.id)
        track.item_ids.insert(track_index + 1, right.id)
        self._touch(branch, track, left, right)
        resources.append(("item", right.id))
        after = self._capture(branch, resources)
        self._record(
            branch,
            operation="split_item",
            label=f"Split {item.name}",
            before={**before, self._resource_key("item", right.id): None},
            after=after,
            actor=actor,
            target_type="item",
            target_id=item.id,
            metadata={"new_item_id": right.id, "split_time": timeline_time},
        )
        self.save(project)
        return left, right

    def ripple_delete(self, item_id: str, *, actor: str = "Member") -> list[EditorItem]:
        project = self.load()
        branch = self._branch(project)
        item = self._item(branch, item_id)
        if self._locked(branch, "item", item.id):
            raise PermissionError("Item or parent track is locked")
        track = next((value for value in branch.tracks if item.id in value.item_ids), None)
        if track is None:
            raise ValueError("Timeline item is not attached to a track")
        affected = [value for value in branch.items if value.id in track.item_ids and value.start >= item.start + item.duration - 1e-6]
        resources: list[tuple[EditorTargetType, str]] = [("track", track.id), ("item", item.id)] + [("item", value.id) for value in affected]
        before = self._capture(branch, resources)
        branch.items = [value for value in branch.items if value.id != item.id]
        track.item_ids = [value for value in track.item_ids if value != item.id]
        for value in affected:
            value.start = max(item.start, value.start - item.duration)
            value.updated_at = _now()
        self._touch(branch, track, *affected)
        after = self._capture(branch, resources)
        self._record(
            branch,
            operation="ripple_delete",
            label=f"Ripple delete {item.name}",
            before=before,
            after=after,
            actor=actor,
            target_type="item",
            target_id=item.id,
            metadata={"shifted_items": [value.id for value in affected], "removed_duration": item.duration},
        )
        self.save(project)
        return affected

    def slip_item(self, item_id: str, delta: float, *, actor: str = "Member") -> EditorItem:
        item = self.load_item(item_id)
        new_in = max(0.0, item.source_in + float(delta))
        changes: dict[str, Any] = {"source_in": new_in}
        if item.source_out is not None:
            changes["source_out"] = max(new_in + 0.001, item.source_out + float(delta))
        return self.patch_item(item_id, changes, actor=actor)

    def roll_edit(self, left_item_id: str, right_item_id: str, delta: float, *, actor: str = "Member") -> tuple[EditorItem, EditorItem]:
        project = self.load()
        branch = self._branch(project)
        left = self._item(branch, left_item_id)
        right = self._item(branch, right_item_id)
        if left.kind not in {"video_clip", "audio_clip"} or right.kind not in {"video_clip", "audio_clip"}:
            raise ValueError("Roll edit requires timeline clips")
        if self._locked(branch, "item", left.id) or self._locked(branch, "item", right.id):
            raise PermissionError("One of the roll-edit clips is locked")
        left_track = next((track for track in branch.tracks if left.id in track.item_ids), None)
        right_track = next((track for track in branch.tracks if right.id in track.item_ids), None)
        if left_track is None or right_track is None or left_track.id != right_track.id:
            raise ValueError("Roll-edit clips must share a track")
        amount = float(delta)
        new_left_duration = left.duration + amount
        new_right_duration = right.duration - amount
        if new_left_duration <= 0.01 or new_right_duration <= 0.01:
            raise ValueError("Roll edit would collapse a clip")
        resources = [("item", left.id), ("item", right.id)]
        before = self._capture(branch, resources)
        left.duration = new_left_duration
        right.start += amount
        right.duration = new_right_duration
        right.source_in = max(0.0, right.source_in + amount * right.speed)
        self._touch(branch, left, right)
        after = self._capture(branch, resources)
        self._record(
            branch,
            operation="roll_edit",
            label=f"Roll edit {left.name} / {right.name}",
            before=before,
            after=after,
            actor=actor,
            target_type="item",
            target_id=left.id,
            metadata={"right_item_id": right.id, "delta": amount},
        )
        self.save(project)
        return left, right

    def duplicate_item(self, item_id: str, *, start: float | None = None, actor: str = "Member") -> EditorItem:
        project = self.load()
        branch = self._branch(project)
        source = self._item(branch, item_id)
        track = next((value for value in branch.tracks if source.id in value.item_ids), None)
        if track is None:
            raise ValueError("Item is not attached to a track")
        if track.locked:
            raise PermissionError("Track is locked")
        copy = deepcopy(source)
        copy.id = _id("item")
        copy.name = f"{source.name} Copy"
        copy.start = source.start + source.duration if start is None else max(0.0, float(start))
        copy.locked = False
        copy.created_at = copy.updated_at = _now()
        resources = [("track", track.id), ("item", copy.id)]
        before = self._capture(branch, resources)
        branch.items.append(copy)
        insert_at = track.item_ids.index(source.id) + 1
        track.item_ids.insert(insert_at, copy.id)
        self._touch(branch, track, copy)
        after = self._capture(branch, resources)
        self._record(
            branch,
            operation="duplicate_item",
            label=f"Duplicate {source.name}",
            before=before,
            after=after,
            actor=actor,
            target_type="item",
            target_id=copy.id,
            metadata={"source_item_id": source.id},
        )
        self.save(project)
        return copy

    def reorder_track(self, sequence_id: str, track_id: str, index: int, *, actor: str = "Member") -> EditorSequence:
        project = self.load()
        branch = self._branch(project)
        sequence = self._sequence(branch, sequence_id)
        track = self._track(branch, track_id)
        if sequence.locked:
            raise PermissionError("Sequence is locked")
        if track.id not in sequence.track_ids:
            raise ValueError("Track does not belong to this sequence")
        before = self._capture(branch, [("sequence", sequence.id)])
        sequence.track_ids = [value for value in sequence.track_ids if value != track.id]
        target = max(0, min(int(index), len(sequence.track_ids)))
        sequence.track_ids.insert(target, track.id)
        self._touch(branch, sequence)
        after = self._capture(branch, [("sequence", sequence.id)])
        self._record(
            branch,
            operation="reorder_track",
            label=f"Reorder {track.name}",
            before=before,
            after=after,
            actor=actor,
            target_type="sequence",
            target_id=sequence.id,
            metadata={"track_id": track.id, "index": target},
        )
        self.save(project)
        return sequence

    def undo(self) -> EditorOperation:
        project = self.load()
        branch = self._branch(project)
        if not branch.undo_stack:
            raise ValueError("Nothing to undo")
        operation_id = branch.undo_stack.pop()
        operation = next((item for item in branch.operations if item.id == operation_id), None)
        if operation is None:
            raise ValueError("Undo history is inconsistent")
        self._apply_snapshot(branch, operation.before)
        branch.redo_stack.append(operation.id)
        branch.updated_at = _now()
        self.save(project)
        return operation

    def redo(self) -> EditorOperation:
        project = self.load()
        branch = self._branch(project)
        if not branch.redo_stack:
            raise ValueError("Nothing to redo")
        operation_id = branch.redo_stack.pop()
        operation = next((item for item in branch.operations if item.id == operation_id), None)
        if operation is None:
            raise ValueError("Redo history is inconsistent")
        self._apply_snapshot(branch, operation.after)
        branch.undo_stack.append(operation.id)
        branch.updated_at = _now()
        self.save(project)
        return operation

    def create_branch(self, name: str, *, actor: str = "Member") -> EditorBranch:
        project = self.load()
        source = self._branch(project)
        branch = EditorBranch(
            name=name,
            parent_branch_id=source.id,
            forked_from_operation_id=source.undo_stack[-1] if source.undo_stack else None,
            sequences=deepcopy(source.sequences),
            tracks=deepcopy(source.tracks),
            items=deepcopy(source.items),
            metadata={"created_by": actor},
        )
        project.branches.append(branch)
        project.active_branch_id = branch.id
        self.save(project)
        return branch

    def checkout_branch(self, branch_id: str) -> EditorBranch:
        project = self.load()
        branch = self._branch(project, branch_id)
        project.active_branch_id = branch.id
        self.save(project)
        return branch

    def compare_branches(self, left_branch_id: str, right_branch_id: str) -> dict[str, Any]:
        project = self.load()
        left = self._branch(project, left_branch_id)
        right = self._branch(project, right_branch_id)

        def compare_collection(kind: str, left_values: list, right_values: list) -> dict:
            left_map = {item.id: item.model_dump(mode="json") for item in left_values}
            right_map = {item.id: item.model_dump(mode="json") for item in right_values}
            added = sorted(set(right_map) - set(left_map))
            removed = sorted(set(left_map) - set(right_map))
            changed = []
            for resource_id in sorted(set(left_map) & set(right_map)):
                a = left_map[resource_id]
                b = right_map[resource_id]
                if a == b:
                    continue
                ignored = {"updated_at"}
                fields = sorted(key for key in set(a) | set(b) if key not in ignored and a.get(key) != b.get(key))
                changed.append({"id": resource_id, "fields": fields})
            return {"kind": kind, "added": added, "removed": removed, "changed": changed}

        return {
            "left": {"id": left.id, "name": left.name},
            "right": {"id": right.id, "name": right.name},
            "sequences": compare_collection("sequence", left.sequences, right.sequences),
            "tracks": compare_collection("track", left.tracks, right.tracks),
            "items": compare_collection("item", left.items, right.items),
            "media_files_duplicated": False,
        }

    def load_item(self, item_id: str) -> EditorItem:
        project = self.load()
        return self._item(self._branch(project), item_id)

    def sync_creative_manifest(self, manifest: Any, *, actor: str = "Aura Creative House") -> dict[str, Any]:
        """Import active image/video Creative Elements into editor sequences once.

        Generated media remains the source-of-truth asset. This only creates editable timeline/layer
        references, so later editor changes never rewrite the Creative Element or its binary file.
        """
        project = self.load()
        branch = self._branch(project)
        active = set(getattr(manifest, "active_element_ids", []) or [])
        existing = {item.source_element_id for item in branch.items if item.source_element_id}
        imported: list[str] = []

        def ensure_sequence(kind: EditorMediaKind) -> EditorSequence:
            sequence = next((value for value in branch.sequences if value.kind == kind), None)
            if sequence is not None:
                return sequence
            if kind == "video":
                sequence = EditorSequence(kind="video", name="Main Video", width=1920, height=1080, fps=24.0, duration=30.0)
            else:
                sequence = EditorSequence(kind="image", name="Main Artwork", width=2048, height=2048, fps=1.0, duration=1.0, background="#00000000")
            branch.sequences.append(sequence)
            return sequence

        for element in getattr(manifest, "elements", []) or []:
            if element.id not in active or element.id in existing or element.kind not in {"video", "image"}:
                continue
            kind: EditorMediaKind = element.kind
            sequence = ensure_sequence(kind)
            if kind == "video":
                track = next((value for value in branch.tracks if value.id in sequence.track_ids and value.kind == "video"), None)
                if track is None:
                    track = EditorTrack(kind="video", name="Video 1", role="picture")
                    branch.tracks.append(track)
                    sequence.track_ids.append(track.id)
                duration = 5.0
                render_meta = element.metadata.get("renderer_output") if isinstance(element.metadata, dict) else None
                if isinstance(element.metadata, dict):
                    duration = float(element.metadata.get("duration") or duration)
                    frames = element.metadata.get("frames")
                    fps = element.metadata.get("fps")
                    if frames and fps:
                        duration = max(0.04, float(frames) / max(1.0, float(fps)))
                start = max((self._item(branch, value).start + self._item(branch, value).duration for value in track.item_ids), default=0.0)
                item = EditorItem(
                    kind="video_clip",
                    name=element.label,
                    source_element_id=element.id,
                    source_ref=element.source_ref,
                    start=start,
                    duration=duration,
                    metadata={"creative_element_kind": element.kind, "renderer_output": render_meta or {}},
                )
                branch.items.append(item)
                track.item_ids.append(item.id)
                sequence.duration = max(sequence.duration, item.start + item.duration)
            else:
                track = EditorTrack(kind="image", name=element.label, role="image_layer")
                item = EditorItem(
                    kind="image_layer",
                    name=element.label,
                    source_element_id=element.id,
                    source_ref=element.source_ref,
                    start=0.0,
                    duration=1.0,
                    metadata={"creative_element_kind": element.kind},
                )
                branch.tracks.append(track)
                branch.items.append(item)
                track.item_ids.append(item.id)
                sequence.track_ids.append(track.id)
            imported.append(element.id)

        if imported:
            self._repair_relations(branch)
            branch.updated_at = _now()
            project.metadata["last_manifest_sync_at"] = _now()
            project.metadata["last_manifest_sync_actor"] = actor
            self.save(project)
        return {"imported_element_ids": imported, "imported": len(imported)}


__all__ = [
    "BlendMode",
    "EDITOR_FILENAME",
    "EditorBranch",
    "EditorEffect",
    "EditorItem",
    "EditorKeyframe",
    "EditorMask",
    "EditorSequence",
    "EditorTrack",
    "ProfessionalEditorProject",
    "ProfessionalEditorStore",
]
