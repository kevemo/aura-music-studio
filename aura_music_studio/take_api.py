from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .mixer import selected_audio_clips
from .plans import TAKE_LANES
from .revisions import create_revision
from .session import StudioSession
from .tenant_storage import project_path

router = APIRouter(tags=["Take Lanes"])


class CommitTakeRequest(BaseModel):
    take_lane: int = Field(ge=0)


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if not member:
        raise HTTPException(401, "Sign in required")
    if not member.plan.has(TAKE_LANES):
        raise HTTPException(403, "Take lanes unlock on Pro")
    return member


def _session(project_name: str) -> tuple[StudioSession, object, object]:
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


def _public_track(track) -> dict:
    lanes: dict[int, list[dict]] = {}
    for clip in track.clips:
        if clip.kind != "audio":
            continue
        lanes.setdefault(int(clip.take_lane), []).append({
            "clip_id": clip.id,
            "name": clip.name,
            "source": clip.source,
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
    }


@router.get("/projects/{project_name}/takes")
def list_takes(project_name: str, request: Request):
    _member(request)
    session, _, _ = _session(project_name)
    tracks = [_public_track(track) for track in session.tracks if any(c.kind == "audio" for c in track.clips)]
    return {"session_id": session.id, "tracks": tracks}


@router.get("/projects/{project_name}/takes/{track_id}")
def track_takes(project_name: str, track_id: str, request: Request):
    _member(request)
    session, _, _ = _session(project_name)
    return _public_track(_track(session, track_id))


@router.post("/projects/{project_name}/takes/{track_id}/commit")
def commit_take(project_name: str, track_id: str, body: CommitTakeRequest, request: Request):
    member = _member(request)
    session, session_path, project = _session(project_name)
    track = _track(session, track_id)
    clips = [clip for clip in track.clips if clip.kind == "audio" and clip.take_lane == body.take_lane]
    if not clips:
        raise HTTPException(404, "Take lane not found")

    create_revision(
        project,
        label=f"Before selecting take {body.take_lane + 1} on {track.name}",
        reason="take_commit",
        actor=member.user.get("display_name") or "Member",
        keep=200,
    )
    for clip in track.clips:
        if clip.kind == "audio":
            clip.metadata["committed"] = clip.take_lane == body.take_lane
    session.generation_history.append({
        "action": "commit_take_lane",
        "track_id": track.id,
        "take_lane": body.take_lane,
    })
    session.save(session_path)
    return _public_track(track)


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
    for clip in track.clips:
        if clip.kind == "audio":
            clip.metadata.pop("committed", None)
    session.generation_history.append({"action": "clear_committed_take", "track_id": track.id})
    session.save(session_path)
    return _public_track(track)
