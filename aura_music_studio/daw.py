from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import soundfile as sf

from .session import AutomationLane, Clip, Effect, Send, StudioSession, Track


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
            "sends": [send.model_dump() for send in track.sends],
            "frozen": bool(track.metadata.get("frozen", False)),
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


def crossfade_clips(session: StudioSession, left_clip_id: str, right_clip_id: str, duration: float) -> tuple[Clip, Clip]:
    left_track, left = find_clip(session, left_clip_id)
    right_track, right = find_clip(session, right_clip_id)
    if left_track.id != right_track.id:
        raise ValueError("Crossfade clips must be on the same track")
    if left.kind != "audio" or right.kind != "audio":
        raise ValueError("Crossfade requires real audio clips")
    if left.take_lane != right.take_lane:
        raise ValueError("Crossfade clips must be on the same take lane")
    if right.start < left.start:
        left, right = right, left
    requested = max(0.001, min(30.0, float(duration)))
    maximum = min(left.duration, right.duration) / 2.0
    fade = min(requested, maximum)
    if fade <= 0.0:
        raise ValueError("Clips are too short to crossfade")
    desired_right_start = left.start + left.duration - fade
    shift = desired_right_start - right.start
    if shift > 0:
        right.start += shift
    overlap = max(0.0, left.start + left.duration - right.start)
    if overlap <= 0.0:
        right.start = desired_right_start
        overlap = fade
    overlap = min(overlap, fade, left.duration / 2.0, right.duration / 2.0)
    left.fade_out = overlap
    right.fade_in = overlap
    session.touch()
    return left, right


def create_bus(session: StudioSession, *, name: str = "", preset: str = "reverb") -> Track:
    preset = (preset or "reverb").strip().lower()
    if preset not in {"reverb", "delay", "clean"}:
        raise ValueError("Unsupported bus preset")
    bus = session.add_track((name or f"Aura {preset.title()} Bus")[:100], "bus")
    bus.metadata["bus_preset"] = preset
    if preset == "reverb":
        bus.effects.append(Effect(type="reverb", parameters={"predelay_ms": 28.0, "mix": 0.24}))
    elif preset == "delay":
        bus.effects.append(Effect(type="delay", parameters={"delay_ms": 320.0, "feedback": 0.28}))
    session.touch()
    return bus


def delete_bus(session: StudioSession, bus_track_id: str) -> bool:
    try:
        bus = session.find_track(bus_track_id)
    except KeyError:
        return False
    if bus.role != "bus":
        raise ValueError("Only auxiliary bus tracks can be removed through the bus API")
    session.tracks = [track for track in session.tracks if track.id != bus_track_id]
    for track in session.tracks:
        track.sends = [send for send in track.sends if send.bus_track_id != bus_track_id]
    session.touch()
    return True


def set_send(
    session: StudioSession,
    source_track_id: str,
    bus_track_id: str,
    *,
    level_db: float = -18.0,
    enabled: bool = True,
) -> Send:
    source = session.find_track(source_track_id)
    bus = session.find_track(bus_track_id)
    if source.role in {"master", "bus"}:
        raise ValueError("Only ordinary audio tracks can send to an auxiliary bus")
    if bus.role != "bus":
        raise ValueError("Send destination must be an auxiliary bus")
    level = max(-60.0, min(12.0, float(level_db)))
    existing = next((send for send in source.sends if send.bus_track_id == bus_track_id), None)
    if existing is None:
        existing = Send(bus_track_id=bus_track_id, level_db=level, enabled=bool(enabled))
        source.sends.append(existing)
    else:
        existing.level_db = level
        existing.enabled = bool(enabled)
    session.touch()
    return existing


def remove_send(session: StudioSession, source_track_id: str, send_id: str) -> bool:
    source = session.find_track(source_track_id)
    before = len(source.sends)
    source.sends = [send for send in source.sends if send.id != send_id]
    if len(source.sends) == before:
        return False
    session.touch()
    return True


def _safe_track_name(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", value or "Track").strip("._")
    return clean[:100] or "Track"


def bounce_track(project: Path, session: StudioSession, track_id: str) -> Path:
    """Render one editable track to a private member output without mutating the session."""
    from .mixer import render_track

    track = session.find_track(track_id)
    if track.role in {"master", "bus"}:
        raise ValueError("Bounce an ordinary audio track, not the master or an auxiliary bus")
    rendered = render_track(track, session, project, project / "work" / "daw_bounce" / uuid4().hex)
    if rendered is None:
        raise ValueError("Track has no audible real audio to bounce")
    audio, sr = sf.read(rendered, always_2d=True, dtype="float32")
    output = project / "output" / "daw" / "bounces" / f"{_safe_track_name(track.name)}_{uuid4().hex[:8]}_Bounce.wav"
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, audio, sr, subtype="PCM_24")
    return output


def freeze_track(project: Path, session: StudioSession, track_id: str) -> Track:
    """Render a track to waveform and replace its editable processors with a reversible frozen clip."""
    from .mixer import render_track

    track = session.find_track(track_id)
    if track.role in {"master", "bus"}:
        raise ValueError("Only ordinary audio tracks can be frozen")
    if track.metadata.get("frozen"):
        raise ValueError("Track is already frozen")
    rendered = render_track(track, session, project, project / "work" / "freeze_render" / uuid4().hex)
    if rendered is None:
        raise ValueError("Track has no audible real audio to freeze")

    freeze_state = {
        "clips": [clip.model_dump() for clip in track.clips],
        "effects": [effect.model_dump() for effect in track.effects],
        "automation": [lane.model_dump() for lane in track.automation],
        "volume_db": track.volume_db,
        "pan": track.pan,
    }
    freeze_dir = project / "work" / "freeze"
    freeze_dir.mkdir(parents=True, exist_ok=True)
    frozen_path = freeze_dir / f"{track.id}_{uuid4().hex[:8]}_Frozen.wav"
    audio, sr = sf.read(rendered, always_2d=True, dtype="float32")
    sf.write(frozen_path, audio, sr, subtype="PCM_24")
    duration = float(len(audio) / sr) if sr else 0.0
    if duration <= 0.01:
        raise ValueError("Frozen track render is empty")

    relative = frozen_path.resolve().relative_to(project.resolve()).as_posix()
    track.metadata["freeze_state"] = freeze_state
    track.metadata["frozen"] = True
    track.clips = [Clip(
        name=f"{track.name} — Frozen",
        kind="audio",
        source=relative,
        start=0.0,
        duration=duration,
        metadata={"real_audio": True, "frozen_render": True},
    )]
    track.effects = []
    track.automation = []
    track.volume_db = 0.0
    track.pan = 0.0
    session.touch()
    return track


def thaw_track(session: StudioSession, track_id: str) -> Track:
    track = session.find_track(track_id)
    state = track.metadata.get("freeze_state")
    if not track.metadata.get("frozen") or not isinstance(state, dict):
        raise ValueError("Track is not frozen")
    track.clips = [Clip.model_validate(item) for item in state.get("clips", [])]
    track.effects = [Effect.model_validate(item) for item in state.get("effects", [])]
    track.automation = [AutomationLane.model_validate(item) for item in state.get("automation", [])]
    track.volume_db = float(state.get("volume_db", 0.0))
    track.pan = max(-1.0, min(1.0, float(state.get("pan", 0.0))))
    track.metadata.pop("freeze_state", None)
    track.metadata.pop("frozen", None)
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
    """Write one canonical validated automation lane.

    AutomationLane owns normalization, clamping, non-finite filtering and duplicate-time collapse.
    Canonicalizing before lookup prevents aliases such as ``volume`` and ``volume_db`` from creating
    separate lanes that would fight each other during render.
    """
    track = session.find_track(track_id)
    candidate = AutomationLane(parameter=parameter, points=points)
    lane = next((item for item in track.automation if item.parameter == candidate.parameter), None)
    if lane is None:
        lane = candidate
        track.automation.append(lane)
    else:
        lane.parameter = candidate.parameter
        lane.points = candidate.points
    session.touch()
    return lane
