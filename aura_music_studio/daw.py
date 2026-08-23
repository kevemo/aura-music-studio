from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import soundfile as sf

from .session import AutomationLane, AutomationPoint, Clip, StudioSession, Track


SESSION_FILE = "aura_session.json"


def session_path(project: Path) -> Path:
    return project / SESSION_FILE


def load_session(project: Path, *, create: bool = False, name: str | None = None) -> StudioSession:
    path = session_path(project)
    if path.is_file():
        return StudioSession.load(path)
    if not create:
        raise FileNotFoundError(path)
    session = StudioSession(name=name or project.name)
    session.add_track("Master", "master")
    session.save(path)
    return session


def save_session(project: Path, session: StudioSession) -> None:
    session.save(session_path(project))


def find_clip(session: StudioSession, clip_id: str) -> tuple[Track, Clip]:
    for track in session.tracks:
        for clip in track.clips:
            if clip.id == clip_id:
                return track, clip
    raise KeyError(clip_id)


def session_duration(session: StudioSession) -> float:
    return max(
        (clip.start + clip.duration for track in session.tracks for clip in track.clips if clip.kind == "audio"),
        default=0.0,
    )


def public_session(session: StudioSession) -> dict:
    """Browser-safe DAW state. Source paths and private metadata never leave the server."""
    tracks = []
    for track in session.tracks:
        clips = []
        for clip in track.clips:
            clips.append({
                "id": clip.id,
                "name": clip.name,
                "kind": clip.kind,
                "start": clip.start,
                "duration": clip.duration,
                "source_offset": clip.source_offset,
                "gain_db": clip.gain_db,
                "fade_in": clip.fade_in,
                "fade_out": clip.fade_out,
                "muted": clip.muted,
                "take_lane": clip.take_lane,
                "committed": bool(clip.metadata.get("committed", False)),
                "generated": bool(clip.metadata.get("generated", False)),
            })
        tracks.append({
            "id": track.id,
            "name": track.name,
            "role": track.role,
            "volume_db": track.volume_db,
            "pan": track.pan,
            "mute": track.mute,
            "solo": track.solo,
            "color": track.color,
            "clips": clips,
            "automation": [lane.model_dump() for lane in track.automation],
            "effect_count": len(track.effects),
        })
    return {
        "id": session.id,
        "name": session.name,
        "bpm": session.bpm,
        "key": session.key,
        "meter": session.meter,
        "sample_rate": session.sample_rate,
        "modified_at": session.modified_at,
        "duration": session_duration(session),
        "loop_start": session.loop_start,
        "loop_end": session.loop_end,
        "markers": [marker.model_dump() for marker in session.markers],
        "tracks": tracks,
        "source_paths_exposed": False,
    }


def _source_path(project: Path, clip: Clip) -> Path:
    if clip.kind != "audio" or not clip.source:
        raise ValueError("Clip is not backed by real audio")
    root = project.resolve()
    source = Path(clip.source)
    if not source.is_absolute():
        source = root / source
    source = source.resolve()
    if source != root and root not in source.parents:
        raise PermissionError("DAW clip source escapes the member project")
    if not source.is_file():
        raise FileNotFoundError(source)
    return source


def waveform_peaks(project: Path, clip: Clip, *, points: int = 900) -> dict:
    """Return compact min/max waveform bins for browser rendering without exposing the source file."""
    points = max(64, min(int(points), 4000))
    source = _source_path(project, clip)
    with sf.SoundFile(source) as handle:
        rate = int(handle.samplerate)
        source_frames = int(handle.frames)
        start_frame = min(source_frames, max(0, int(round(clip.source_offset * rate))))
        available = max(0, source_frames - start_frame)
        requested = int(round(max(0.0, clip.duration) * rate)) if clip.duration > 0 else available
        frames = min(available, requested)
        if frames <= 0:
            return {"clip_id": clip.id, "sample_rate": rate, "duration": 0.0, "peaks": []}
        handle.seek(start_frame)
        frames_per_bin = max(1, (frames + points - 1) // points)
        remaining = frames
        peaks: list[list[float]] = []
        while remaining > 0 and len(peaks) < points:
            count = min(frames_per_bin, remaining)
            data = handle.read(count, dtype="float32", always_2d=True)
            if len(data) == 0:
                break
            minimum = float(data.min())
            maximum = float(data.max())
            peaks.append([max(-1.0, minimum), min(1.0, maximum)])
            remaining -= len(data)
    return {
        "clip_id": clip.id,
        "sample_rate": rate,
        "duration": frames / rate,
        "points": len(peaks),
        "peaks": peaks,
    }


def move_clip(session: StudioSession, clip_id: str, start: float) -> Clip:
    _, clip = find_clip(session, clip_id)
    clip.start = max(0.0, float(start))
    session.touch()
    return clip


def set_clip_gain(session: StudioSession, clip_id: str, gain_db: float) -> Clip:
    _, clip = find_clip(session, clip_id)
    clip.gain_db = max(-60.0, min(24.0, float(gain_db)))
    session.touch()
    return clip


def set_clip_fades(session: StudioSession, clip_id: str, fade_in: float, fade_out: float) -> Clip:
    _, clip = find_clip(session, clip_id)
    maximum = max(0.0, clip.duration / 2.0)
    clip.fade_in = max(0.0, min(float(fade_in), maximum))
    clip.fade_out = max(0.0, min(float(fade_out), maximum))
    session.touch()
    return clip


def trim_clip(
    project: Path,
    session: StudioSession,
    clip_id: str,
    *,
    start: float,
    duration: float,
    source_offset: float,
) -> Clip:
    _, clip = find_clip(session, clip_id)
    source = _source_path(project, clip)
    info = sf.info(source)
    source_duration = float(info.frames / info.samplerate)
    offset = max(0.0, min(float(source_offset), source_duration))
    maximum = max(0.0, source_duration - offset)
    if maximum <= 0.0:
        raise ValueError("Trim offset is beyond the available audio")
    new_duration = max(0.01, min(float(duration), maximum))
    clip.start = max(0.0, float(start))
    clip.source_offset = offset
    clip.duration = new_duration
    clip.fade_in = min(clip.fade_in, new_duration / 2.0)
    clip.fade_out = min(clip.fade_out, new_duration / 2.0)
    session.touch()
    return clip


def split_clip(session: StudioSession, clip_id: str, timeline_time: float) -> tuple[Clip, Clip]:
    track, clip = find_clip(session, clip_id)
    split_at = float(timeline_time)
    relative = split_at - clip.start
    if relative <= 0.001 or relative >= clip.duration - 0.001:
        raise ValueError("Split point must be inside the clip")

    left = deepcopy(clip)
    right = deepcopy(clip)
    left.id = uuid4().hex
    right.id = uuid4().hex
    left.name = f"{clip.name} A"
    right.name = f"{clip.name} B"
    left.duration = relative
    left.fade_out = 0.0
    right.start = split_at
    right.source_offset = clip.source_offset + relative
    right.duration = clip.duration - relative
    right.fade_in = 0.0

    index = next(i for i, item in enumerate(track.clips) if item.id == clip_id)
    track.clips[index:index + 1] = [left, right]
    session.touch()
    return left, right


def delete_clip(session: StudioSession, clip_id: str) -> bool:
    for track in session.tracks:
        for index, clip in enumerate(track.clips):
            if clip.id == clip_id:
                del track.clips[index]
                session.touch()
                return True
    return False


def set_track_mix(
    session: StudioSession,
    track_id: str,
    *,
    volume_db: float | None = None,
    pan: float | None = None,
    mute: bool | None = None,
    solo: bool | None = None,
) -> Track:
    track = session.find_track(track_id)
    if volume_db is not None:
        track.volume_db = max(-60.0, min(18.0, float(volume_db)))
    if pan is not None:
        track.pan = max(-1.0, min(1.0, float(pan)))
    if mute is not None:
        track.mute = bool(mute)
    if solo is not None:
        track.solo = bool(solo)
    session.touch()
    return track


def set_loop(session: StudioSession, start: float | None, end: float | None) -> None:
    if start is None or end is None:
        session.loop_start = None
        session.loop_end = None
    else:
        start = max(0.0, float(start))
        end = max(0.0, float(end))
        if end <= start:
            raise ValueError("Loop end must be after loop start")
        session.loop_start = start
        session.loop_end = end
    session.touch()


def set_automation(
    session: StudioSession,
    track_id: str,
    parameter: str,
    points: list[dict],
) -> AutomationLane:
    track = session.find_track(track_id)
    clean = [
        AutomationPoint(time=max(0.0, float(item["time"])), value=float(item["value"]))
        for item in points
    ]
    clean.sort(key=lambda item: item.time)
    lane = next((item for item in track.automation if item.parameter == parameter), None)
    if lane is None:
        lane = AutomationLane(parameter=parameter)
        track.automation.append(lane)
    lane.points = clean
    session.touch()
    return lane
