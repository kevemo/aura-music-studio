from __future__ import annotations

import re
from html import unescape
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator

from .professional_editor import EditorItem, EditorTrack, ProfessionalEditorStore
from .professional_editor_api import _actor, _member, _state, _store

router = APIRouter(prefix="/creative", tags=["Professional Captions and Subtitles"])

CaptionKind = Literal["caption", "subtitle"]
CaptionFormat = Literal["srt", "vtt"]
CaptionPosition = Literal["top", "middle", "bottom"]
_MAX_IMPORT_BYTES = 512_000
_MAX_CUES = 1000
_MAX_TEXT = 240
_LANG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,15}$")
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?$")
_TAG = re.compile(r"<[^>]{1,120}>")
_ASS_TAG = re.compile(r"\{\\[^}]{0,160}\}")
_TIME = re.compile(r"^(?:(\d{1,2}):)?(\d{2}):(\d{2})[,.](\d{3})$")


class CaptionStyle(BaseModel):
    size: int = Field(default=48, ge=12, le=96)
    color: str = Field(default="#ffffffff", min_length=7, max_length=9)
    stroke_width: int = Field(default=2, ge=0, le=8)
    position: CaptionPosition = "bottom"

    @model_validator(mode="after")
    def validate_color(self):
        if not _HEX.fullmatch(self.color):
            raise ValueError("Caption color must be #RRGGBB or #RRGGBBAA")
        self.color = self.color.lower()
        return self


class CaptionCueInput(BaseModel):
    start: float = Field(ge=0.0, le=86400.0)
    end: float = Field(gt=0.0, le=86400.0)
    text: str = Field(min_length=1, max_length=_MAX_TEXT)

    @model_validator(mode="after")
    def validate_range(self):
        if self.end <= self.start:
            raise ValueError("Caption cue end must be after start")
        return self


class CaptionTrackRequest(BaseModel):
    name: str = Field(default="Captions", min_length=1, max_length=160)
    kind: CaptionKind = "caption"
    language: str = Field(default="und", min_length=1, max_length=16)
    style: CaptionStyle = Field(default_factory=CaptionStyle)
    cues: list[CaptionCueInput] = Field(min_length=1, max_length=_MAX_CUES)

    @model_validator(mode="after")
    def validate_language(self):
        if not _LANG.fullmatch(self.language):
            raise ValueError("Caption language must be a bounded BCP-47 style token")
        self.language = self.language.lower()
        return self


class CaptionImportRequest(BaseModel):
    format: CaptionFormat
    content: str = Field(min_length=1, max_length=_MAX_IMPORT_BYTES)
    name: str = Field(default="Imported Captions", min_length=1, max_length=160)
    kind: CaptionKind = "subtitle"
    language: str = Field(default="und", min_length=1, max_length=16)
    style: CaptionStyle = Field(default_factory=CaptionStyle)

    @model_validator(mode="after")
    def validate_language(self):
        if not _LANG.fullmatch(self.language):
            raise ValueError("Caption language must be a bounded BCP-47 style token")
        self.language = self.language.lower()
        return self


class CaptionPatchRequest(BaseModel):
    start: float | None = Field(default=None, ge=0.0, le=86400.0)
    end: float | None = Field(default=None, gt=0.0, le=86400.0)
    text: str | None = Field(default=None, min_length=1, max_length=_MAX_TEXT)
    style: CaptionStyle | None = None


def _clean_text(value: str) -> str:
    text = unescape(str(value or "")).replace("\r\n", "\n").replace("\r", "\n")
    text = _TAG.sub("", text)
    text = _ASS_TAG.sub("", text)
    text = "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)
    lines = [" ".join(line.split()) for line in text.split("\n")]
    text = "\n".join(line for line in lines if line).strip()
    if not text:
        raise ValueError("Caption text is empty after normalization")
    if len(text) > _MAX_TEXT:
        raise ValueError(f"Caption text may contain at most {_MAX_TEXT} characters")
    return text


def _parse_timestamp(value: str) -> float:
    match = _TIME.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"Invalid caption timestamp: {value[:32]}")
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    millis = int(match.group(4))
    if minutes > 59 or seconds > 59:
        raise ValueError("Caption timestamp minutes/seconds must be below 60")
    result = hours * 3600 + minutes * 60 + seconds + millis / 1000.0
    if result > 86400.0:
        raise ValueError("Caption timestamp exceeds the editor limit")
    return result


def _parse_timing(line: str) -> tuple[float, float]:
    if "-->" not in line:
        raise ValueError("Caption cue is missing a timing arrow")
    left, right = line.split("-->", 1)
    end_token = right.strip().split()[0] if right.strip() else ""
    start = _parse_timestamp(left.strip())
    end = _parse_timestamp(end_token)
    if end <= start:
        raise ValueError("Caption cue end must be after start")
    return start, end


def parse_caption_text(content: str, format: CaptionFormat) -> list[CaptionCueInput]:
    raw = str(content or "")
    if len(raw.encode("utf-8")) > _MAX_IMPORT_BYTES:
        raise ValueError("Caption import exceeds the 512 KB limit")
    raw = raw.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if format == "vtt":
        lines = raw.split("\n")
        if lines and lines[0].strip().upper().startswith("WEBVTT"):
            raw = "\n".join(lines[1:])

    blocks = [block.strip() for block in re.split(r"\n\s*\n", raw) if block.strip()]
    cues: list[CaptionCueInput] = []
    for block in blocks:
        lines = [line.strip("\ufeff") for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        timing_index = next((index for index, line in enumerate(lines[:3]) if "-->" in line), None)
        if timing_index is None:
            # VTT NOTE/STYLE/REGION blocks and malformed SRT headings are not executable cues.
            if format == "vtt" and lines[0].upper().startswith(("NOTE", "STYLE", "REGION")):
                continue
            raise ValueError("Caption block is missing an executable timing line")
        start, end = _parse_timing(lines[timing_index])
        text = _clean_text("\n".join(lines[timing_index + 1 :]))
        cues.append(CaptionCueInput(start=start, end=end, text=text))
        if len(cues) > _MAX_CUES:
            raise ValueError(f"Caption tracks may contain at most {_MAX_CUES} cues")
    if not cues:
        raise ValueError("Caption import contains no executable cues")
    return cues


def _sequence(branch: dict[str, Any], sequence_id: str) -> dict[str, Any]:
    value = next((row for row in branch.get("sequences", []) if row.get("id") == sequence_id), None)
    if value is None:
        raise KeyError(sequence_id)
    if value.get("kind") != "video":
        raise ValueError("Captions and subtitles require a video sequence")
    return value


def _position_y(height: int, position: CaptionPosition) -> float:
    if position == "top":
        return -0.34 * height
    if position == "middle":
        return 0.0
    return 0.34 * height


def _text_payload(text: str, style: CaptionStyle) -> dict[str, Any]:
    return {
        "content": _clean_text(text),
        "size": style.size,
        "color": style.color,
        "stroke_width": style.stroke_width,
    }


def _caption_metadata(*, kind: CaptionKind, language: str, track_id: str, ordinal: int, position: CaptionPosition) -> dict[str, Any]:
    return {
        "caption_cue": True,
        "caption_kind": kind,
        "caption_language": language,
        "caption_track_id": track_id,
        "caption_ordinal": ordinal,
        "caption_position": position,
        "caption_archived": False,
        "render_runtime": "advanced_video_text_layer",
    }


def add_caption_track(
    store: ProfessionalEditorStore,
    sequence_id: str,
    request: CaptionTrackRequest,
    *,
    actor: str = "Member",
) -> dict[str, Any]:
    project = store.load()
    branch = store._branch(project)
    sequence = store._sequence(branch, sequence_id)
    if sequence.kind != "video":
        raise ValueError("Captions and subtitles require a video sequence")
    if sequence.locked:
        raise PermissionError("Sequence is locked")

    cues = [CaptionCueInput(start=cue.start, end=cue.end, text=_clean_text(cue.text)) for cue in request.cues]
    for cue in cues:
        if cue.end > sequence.duration + 1e-8:
            raise ValueError("Caption cue extends beyond the video sequence duration")

    track = EditorTrack(
        kind="text",
        name=request.name,
        role="captions" if request.kind == "caption" else "subtitles",
        metadata={
            "caption_track": True,
            "caption_kind": request.kind,
            "caption_language": request.language,
            "caption_style": request.style.model_dump(mode="json"),
            "render_runtime": "advanced_video_text_layer",
        },
    )
    items: list[EditorItem] = []
    y = _position_y(sequence.height, request.style.position)
    for ordinal, cue in enumerate(cues, start=1):
        item = EditorItem(
            kind="text",
            name=f"{request.name} · {ordinal}",
            start=cue.start,
            duration=cue.end - cue.start,
            text=_text_payload(cue.text, request.style),
            transform={"x": 0.0, "y": y},
            metadata=_caption_metadata(
                kind=request.kind,
                language=request.language,
                track_id=track.id,
                ordinal=ordinal,
                position=request.style.position,
            ),
        )
        items.append(item)

    resources = [("sequence", sequence.id), ("track", track.id)] + [("item", item.id) for item in items]
    before = store._capture(branch, resources)
    branch.tracks.append(track)
    sequence.track_ids.append(track.id)
    branch.items.extend(items)
    track.item_ids.extend(item.id for item in items)
    store._touch(branch, sequence, track, *items)
    after = store._capture(branch, resources)
    store._record(
        branch,
        operation="add_caption_track",
        label=f"Add {request.kind} track {request.name}",
        before=before,
        after=after,
        actor=actor,
        target_type="track",
        target_id=track.id,
        metadata={
            "caption_track": True,
            "cue_count": len(items),
            "language": request.language,
            "kind": request.kind,
        },
    )
    store.save(project)
    return {
        "track": track.model_dump(mode="json"),
        "cues": [item.model_dump(mode="json") for item in items],
        "render_runtime": "advanced_video_text_layer",
        "burn_in_supported": True,
        "non_destructive": True,
    }


def _caption_rows(store: ProfessionalEditorStore, sequence_id: str, *, include_archived: bool = False) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    state = store.public_state()
    branch = state["branch"]
    sequence = _sequence(branch, sequence_id)
    track_ids = set(sequence.get("track_ids", []))
    tracks = [row for row in branch.get("tracks", []) if row.get("id") in track_ids and (row.get("metadata") or {}).get("caption_track")]
    caption_track_ids = {row["id"] for row in tracks}
    cues = []
    for item in branch.get("items", []):
        metadata = item.get("metadata") or {}
        if not metadata.get("caption_cue") or metadata.get("caption_track_id") not in caption_track_ids:
            continue
        if metadata.get("caption_archived") and not include_archived:
            continue
        cues.append(item)
    cues.sort(key=lambda row: (float(row.get("start") or 0.0), int((row.get("metadata") or {}).get("caption_ordinal") or 0)))
    return sequence, tracks, cues


def _format_timestamp(value: float, *, vtt: bool) -> str:
    millis = max(0, int(round(float(value) * 1000.0)))
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, ms = divmod(rest, 1000)
    separator = "." if vtt else ","
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{ms:03d}"


def export_caption_text(cues: list[dict[str, Any]], format: CaptionFormat) -> str:
    rows = []
    if format == "vtt":
        rows.append("WEBVTT\n")
    for ordinal, item in enumerate(cues, start=1):
        text = _clean_text(str((item.get("text") or {}).get("content") or ""))
        start = float(item.get("start") or 0.0)
        end = start + float(item.get("duration") or 0.0)
        if format == "srt":
            rows.append(f"{ordinal}\n{_format_timestamp(start, vtt=False)} --> {_format_timestamp(end, vtt=False)}\n{text}\n")
        else:
            rows.append(f"{_format_timestamp(start, vtt=True)} --> {_format_timestamp(end, vtt=True)}\n{text}\n")
    return "\n".join(rows).rstrip() + "\n"


def _caption_item(store: ProfessionalEditorStore, item_id: str) -> dict[str, Any]:
    item = next((row for row in store.public_state()["branch"].get("items", []) if row.get("id") == item_id), None)
    if item is None:
        raise KeyError(item_id)
    if not (item.get("metadata") or {}).get("caption_cue"):
        raise ValueError("Editor item is not a caption/subtitle cue")
    return item


@router.post("/projects/{project_name}/editor/sequences/{sequence_id}/captions")
def create_caption_track(project_name: str, sequence_id: str, body: CaptionTrackRequest, request: Request):
    member = _member(request)
    store = _store(project_name)
    try:
        result = add_caption_track(store, sequence_id, body, actor=_actor(member))
    except KeyError as exc:
        raise HTTPException(404, f"Editor resource not found: {exc.args[0]}") from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {**result, "editor": _state(store)}


@router.post("/projects/{project_name}/editor/sequences/{sequence_id}/captions/import")
def import_captions(project_name: str, sequence_id: str, body: CaptionImportRequest, request: Request):
    member = _member(request)
    store = _store(project_name)
    try:
        cues = parse_caption_text(body.content, body.format)
        result = add_caption_track(
            store,
            sequence_id,
            CaptionTrackRequest(
                name=body.name,
                kind=body.kind,
                language=body.language,
                style=body.style,
                cues=cues,
            ),
            actor=_actor(member),
        )
    except KeyError as exc:
        raise HTTPException(404, f"Editor resource not found: {exc.args[0]}") from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {**result, "import_format": body.format, "editor": _state(store)}


@router.get("/projects/{project_name}/editor/sequences/{sequence_id}/captions")
def list_captions(project_name: str, sequence_id: str, request: Request, include_archived: bool = False):
    _member(request)
    store = _store(project_name)
    try:
        sequence, tracks, cues = _caption_rows(store, sequence_id, include_archived=include_archived)
    except KeyError as exc:
        raise HTTPException(404, "Editor sequence not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "sequence": {"id": sequence["id"], "name": sequence["name"], "duration": sequence["duration"]},
        "tracks": tracks,
        "cues": cues,
        "burn_in_runtime": "advanced_video_text_layer",
        "source_media_mutated": False,
    }


@router.get("/projects/{project_name}/editor/sequences/{sequence_id}/captions/export")
def export_captions(
    project_name: str,
    sequence_id: str,
    request: Request,
    format: CaptionFormat = Query(default="srt"),
    track_id: str | None = Query(default=None, max_length=200),
):
    _member(request)
    store = _store(project_name)
    try:
        _sequence_row, tracks, cues = _caption_rows(store, sequence_id)
        valid_tracks = {row["id"] for row in tracks}
        if track_id is not None:
            if track_id not in valid_tracks:
                raise KeyError(track_id)
            cues = [row for row in cues if (row.get("metadata") or {}).get("caption_track_id") == track_id]
        content = export_caption_text(cues, format)
    except KeyError as exc:
        raise HTTPException(404, "Caption track not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    media_type = "text/vtt; charset=utf-8" if format == "vtt" else "application/x-subrip; charset=utf-8"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.patch("/projects/{project_name}/editor/captions/{item_id}")
def patch_caption(project_name: str, item_id: str, body: CaptionPatchRequest, request: Request):
    member = _member(request)
    store = _store(project_name)
    try:
        current = _caption_item(store, item_id)
        changes: dict[str, Any] = {}
        start = float(body.start if body.start is not None else current["start"])
        end = float(body.end if body.end is not None else start + float(current["duration"]))
        if end <= start:
            raise ValueError("Caption cue end must be after start")
        changes["start"] = start
        changes["duration"] = end - start
        if body.text is not None:
            changes["text"] = {"content": _clean_text(body.text)}
        if body.style is not None:
            changes["text"] = {**dict(changes.get("text") or {}), **_text_payload(
                str((current.get("text") or {}).get("content") or current.get("name") or "Caption"), body.style
            )}
            state = store.public_state()["branch"]
            parent_track = next((row for row in state.get("tracks", []) if item_id in row.get("item_ids", [])), None)
            if parent_track is None:
                raise KeyError(item_id)
            sequence = next((row for row in state.get("sequences", []) if parent_track["id"] in row.get("track_ids", [])), None)
            if sequence is None:
                raise KeyError(item_id)
            changes["transform"] = {"x": 0.0, "y": _position_y(int(sequence["height"]), body.style.position)}
            changes["metadata"] = {"caption_position": body.style.position}
        updated = store.patch_item(item_id, changes, actor=_actor(member))
    except KeyError as exc:
        raise HTTPException(404, "Caption cue not found") from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"cue": updated.model_dump(mode="json"), "editor": _state(store)}


@router.delete("/projects/{project_name}/editor/captions/{item_id}")
def archive_caption(project_name: str, item_id: str, request: Request):
    member = _member(request)
    store = _store(project_name)
    try:
        _caption_item(store, item_id)
        updated = store.patch_item(
            item_id,
            {"enabled": False, "metadata": {"caption_archived": True}},
            actor=_actor(member),
        )
    except KeyError as exc:
        raise HTTPException(404, "Caption cue not found") from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"cue": updated.model_dump(mode="json"), "non_destructive": True, "editor": _state(store)}


@router.post("/projects/{project_name}/editor/captions/{item_id}/restore")
def restore_caption(project_name: str, item_id: str, request: Request):
    member = _member(request)
    store = _store(project_name)
    try:
        _caption_item(store, item_id)
        updated = store.patch_item(
            item_id,
            {"enabled": True, "metadata": {"caption_archived": False}},
            actor=_actor(member),
        )
    except KeyError as exc:
        raise HTTPException(404, "Caption cue not found") from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"cue": updated.model_dump(mode="json"), "editor": _state(store)}


__all__ = [
    "CaptionCueInput",
    "CaptionImportRequest",
    "CaptionStyle",
    "CaptionTrackRequest",
    "add_caption_track",
    "export_caption_text",
    "parse_caption_text",
    "router",
]
