from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .mixer import selected_audio_clips
from .plans import TAKE_LANES
from .revisions import create_revision
from .session import Clip, StudioSession
from .tenant_storage import project_path

router = APIRouter(tags=["Take Lanes"])


class CommitTakeRequest(BaseModel):
    take_lane: int = Field(ge=0)


class CompRegionRequest(BaseModel):
    take_lane: int = Field(ge=0)
    start_seconds: float = Field(ge=0.0)
    end_seconds: float = Field(gt=0.0)
    edge_fade_ms: float = Field(default=5.0, ge=0.0, le=100.0)


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if not member:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(TAKE_LANES):
        raise HTTPException(403, "Take lanes unlock on Pro")
    return member


def _session(project_name: str) -> tuple[StudioSession, Path, Path]:
    try:
        project = project_path(project_name, must_exist=True)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "Project not found") from exc
    path = project / "aura_session.json"
    if not path.is_file():
        raise HTTPException(404, "This project does not have a multitrack session yet")
    return StudioSession.load(path), path, project


def _track(session: StudioSession, track_id: str):
    try:
        return session.find_track(track_id)
    except KeyError as exc:
        raise HTTPException(404, "Track not found") from exc


def _is_comp(clip: Clip) -> bool:
    return bool(clip.metadata.get("comp_segment", False))


def _public_track(track, project_name: str) -> dict:
    lanes: dict[int, list[dict]] = {}
    comp_segments: list[dict] = []
    for clip in track.clips:
        if clip.kind != "audio":
            continue
        if _is_comp(clip):
            comp_segments.append({
                "clip_id": clip.id,
                "name": clip.name,
                "start": clip.start,
                "duration": clip.duration,
                "source_offset": clip.source_offset,
                "source_take_lane": clip.metadata.get("comp_source_lane"),
                "source_clip_id": clip.metadata.get("comp_source_clip_id"),
            })
            continue
        lanes.setdefault(int(clip.take_lane), []).append({
            "clip_id": clip.id,
            "name": clip.name,
            "start": clip.start,
            "duration": clip.duration,
            "source_offset": clip.source_offset,
            "fade_in": clip.fade_in,
            "fade_out": clip.fade_out,
            "muted": clip.muted,
            "committed": bool(clip.metadata.get("committed", False)),
            "generated": bool(clip.metadata.get("generated", False)),
            "generation_id": clip.metadata.get("generation_id"),
            "instrument_type": clip.metadata.get("instrument_type"),
            "preview_url": f"/projects/{quote(project_name, safe='')}/takes/audio/{quote(clip.id, safe='')}",
        })
    rendered = selected_audio_clips(track)
    return {
        "track_id": track.id,
        "name": track.name,
        "role": track.role,
        "take_count": len(lanes),
        "lanes": [{"take_lane": lane, "clips": clips} for lane, clips in sorted(lanes.items())],
        "selected_clip_ids": [clip.id for clip in rendered],
        "committed_lane": next((lane for lane, clips in lanes.items() if any(c["committed"] for c in clips)), None),
        "comp_active": bool(comp_segments),
        "comp_segments": sorted(comp_segments, key=lambda x: (x["start"], x["clip_id"])),
    }


def _clip_source(project: Path, session: StudioSession, clip_id: str) -> Path:
    clip = next((c for t in session.tracks for c in t.clips if c.id == clip_id and c.kind == "audio"), None)
    if not clip or not clip.source:
        raise HTTPException(404, "Take audio not found")
    source = Path(clip.source)
    if not source.is_absolute():
        source = project / source
    source = source.resolve()
    root = project.resolve()
    if root not in source.parents or not source.is_file():
        raise HTTPException(404, "Take audio not found")
    return source


def _copy_segment(clip: Clip, start: float, end: float, *, source_lane: int, fade_seconds: float) -> Clip:
    if end <= start:
        raise ValueError("Invalid comp segment")
    offset_delta = max(0.0, start - clip.start)
    return Clip(
        name=f"Comp · {clip.name}",
        kind="audio",
        source=clip.source,
        start=start,
        duration=end - start,
        source_offset=clip.source_offset + offset_delta,
        gain_db=clip.gain_db,
        fade_in=min(fade_seconds, (end - start) / 4),
        fade_out=min(fade_seconds, (end - start) / 4),
        muted=False,
        take_lane=clip.take_lane,
        metadata={
            **{k: v for k, v in clip.metadata.items() if k not in {"committed", "comp_segment"}},
            "committed": True,
            "comp_segment": True,
            "comp_source_lane": source_lane,
            "comp_source_clip_id": clip.metadata.get("comp_source_clip_id") or clip.id,
        },
    )


def _base_comp_segments(track) -> list[Clip]:
    committed = [c for c in track.clips if c.kind == "audio" and c.metadata.get("committed", False)]
    if committed:
        return sorted(committed, key=lambda c: (c.start, c.source_offset))
    normal = [c for c in track.clips if c.kind == "audio" and not c.muted and not _is_comp(c)]
    if not normal:
        return []
    latest = max(c.take_lane for c in normal)
    return sorted([c for c in normal if c.take_lane == latest], key=lambda c: (c.start, c.source_offset))


def _source_clip_for_region(track, lane: int, start: float, end: float) -> Clip:
    candidates = [
        c for c in track.clips
        if c.kind == "audio" and not _is_comp(c) and c.take_lane == lane and not c.muted
        and c.start <= start + 1e-6 and (c.start + c.duration) >= end - 1e-6
    ]
    if not candidates:
        raise HTTPException(400, "The selected take does not contain the complete requested time region")
    return sorted(candidates, key=lambda c: (c.start, -c.duration))[0]


@router.get("/projects/{project_name}/takes")
def list_takes(project_name: str, request: Request):
    _member(request)
    session, _, _ = _session(project_name)
    tracks = [_public_track(track, project_name) for track in session.tracks if any(c.kind == "audio" for c in track.clips)]
    return {"session_id": session.id, "tracks": tracks}


@router.get("/projects/{project_name}/takes/audio/{clip_id}")
def preview_take(project_name: str, clip_id: str, request: Request):
    _member(request)
    session, _, project = _session(project_name)
    source = _clip_source(project, session, clip_id)
    return FileResponse(
        source,
        media_type=mimetypes.guess_type(source.name)[0] or "audio/wav",
        content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/projects/{project_name}/takes/{track_id}")
def track_takes(project_name: str, track_id: str, request: Request):
    _member(request)
    session, _, _ = _session(project_name)
    return _public_track(_track(session, track_id), project_name)


@router.post("/projects/{project_name}/takes/{track_id}/commit")
def commit_take(project_name: str, track_id: str, body: CommitTakeRequest, request: Request):
    member = _member(request)
    session, session_path, project = _session(project_name)
    track = _track(session, track_id)
    clips = [clip for clip in track.clips if clip.kind == "audio" and not _is_comp(clip) and clip.take_lane == body.take_lane]
    if not clips:
        raise HTTPException(404, "Take lane not found")

    create_revision(
        project,
        label=f"Before selecting take {body.take_lane + 1} on {track.name}",
        reason="take_commit",
        actor=member.user.get("display_name") or "Member",
        keep=200,
    )
    track.clips = [clip for clip in track.clips if not _is_comp(clip)]
    for clip in track.clips:
        if clip.kind == "audio":
            clip.metadata["committed"] = clip.take_lane == body.take_lane
    session.generation_history.append({
        "action": "commit_take_lane",
        "track_id": track.id,
        "take_lane": body.take_lane,
    })
    session.save(session_path)
    return _public_track(track, project_name)


@router.post("/projects/{project_name}/takes/{track_id}/comp")
def comp_region(project_name: str, track_id: str, body: CompRegionRequest, request: Request):
    member = _member(request)
    if body.end_seconds <= body.start_seconds:
        raise HTTPException(400, "Comp end must be after comp start")
    session, session_path, project = _session(project_name)
    track = _track(session, track_id)
    source_clip = _source_clip_for_region(track, body.take_lane, body.start_seconds, body.end_seconds)
    base = _base_comp_segments(track)
    if not base:
        raise HTTPException(400, "Track has no audio to comp")

    create_revision(
        project,
        label=f"Before comping {track.name} {body.start_seconds:.2f}-{body.end_seconds:.2f}s",
        reason="take_comp",
        actor=member.user.get("display_name") or "Member",
        keep=200,
    )
    fade = body.edge_fade_ms / 1000.0
    rebuilt: list[Clip] = []
    for segment in base:
        seg_start = segment.start
        seg_end = segment.start + segment.duration
        lane = int(segment.metadata.get("comp_source_lane", segment.take_lane))
        if seg_end <= body.start_seconds or seg_start >= body.end_seconds:
            rebuilt.append(_copy_segment(segment, seg_start, seg_end, source_lane=lane, fade_seconds=fade))
            continue
        if seg_start < body.start_seconds:
            rebuilt.append(_copy_segment(segment, seg_start, body.start_seconds, source_lane=lane, fade_seconds=fade))
        if seg_end > body.end_seconds:
            rebuilt.append(_copy_segment(segment, body.end_seconds, seg_end, source_lane=lane, fade_seconds=fade))

    rebuilt.append(_copy_segment(
        source_clip,
        body.start_seconds,
        body.end_seconds,
        source_lane=body.take_lane,
        fade_seconds=fade,
    ))
    track.clips = [clip for clip in track.clips if not _is_comp(clip)]
    for clip in track.clips:
        if clip.kind == "audio":
            clip.metadata.pop("committed", None)
    track.clips.extend(sorted(rebuilt, key=lambda c: (c.start, c.source_offset)))
    session.generation_history.append({
        "action": "comp_take_region",
        "track_id": track.id,
        "take_lane": body.take_lane,
        "start_seconds": body.start_seconds,
        "end_seconds": body.end_seconds,
        "edge_fade_ms": body.edge_fade_ms,
    })
    session.save(session_path)
    return _public_track(track, project_name)


@router.delete("/projects/{project_name}/takes/{track_id}/commit")
def clear_take_commit(project_name: str, track_id: str, request: Request):
    member = _member(request)
    session, session_path, project = _session(project_name)
    track = _track(session, track_id)
    committed = [clip for clip in track.clips if clip.kind == "audio" and clip.metadata.get("committed", False)]
    if committed:
        create_revision(
            project,
            label=f"Before returning {track.name} to latest take",
            reason="take_clear",
            actor=member.user.get("display_name") or "Member",
            keep=200,
        )
    track.clips = [clip for clip in track.clips if not _is_comp(clip)]
    for clip in track.clips:
        if clip.kind == "audio":
            clip.metadata.pop("committed", None)
    session.generation_history.append({"action": "clear_committed_take", "track_id": track.id})
    session.save(session_path)
    return _public_track(track, project_name)
