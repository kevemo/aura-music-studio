from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .automation import apply_track_automation
from .effects import render_effects
from .session import Clip, StudioSession, Track


def _resolve(source: str, project_root: Path) -> Path:
    p = Path(source)
    if not p.is_absolute():
        p = project_root / p
    return p.resolve()


def _clip_audio(clip: Clip, project_root: Path, output: Path, sample_rate: int) -> Path:
    if clip.kind != "audio":
        raise ValueError("Only real audio clips can be rendered into Aura's final mixer")
    if not clip.source:
        raise ValueError("Audio clip has no source")
    src = _resolve(clip.source, project_root)
    if not src.exists():
        raise FileNotFoundError(src)
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required for session mixing")
    output.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.01, clip.duration)
    fade_in = min(max(0.0, clip.fade_in), duration / 2)
    fade_out = min(max(0.0, clip.fade_out), duration / 2)
    filters = [
        f"atrim=start={max(0.0, clip.source_offset)}:duration={duration}",
        "asetpts=PTS-STARTPTS",
        f"volume={clip.gain_db}dB",
    ]
    if fade_in > 0:
        filters.append(f"afade=t=in:st=0:d={fade_in}")
    if fade_out > 0:
        filters.append(f"afade=t=out:st={max(0.0, duration-fade_out)}:d={fade_out}")
    delay_ms = max(0, int(round(clip.start * 1000)))
    filters.append(f"adelay={delay_ms}|{delay_ms}")
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
        "-af", ",".join(filters), "-c:a", "pcm_f32le", "-ar", str(sample_rate), str(output),
    ], check=True)
    return output


def _mix_files(files: list[Path], output: Path, sample_rate: int) -> Path:
    if not files:
        raise ValueError("Nothing to mix")
    output.parent.mkdir(parents=True, exist_ok=True)
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for p in files:
        args += ["-i", str(p)]
    inputs = "".join(f"[{i}:a]" for i in range(len(files)))
    filt = f"{inputs}amix=inputs={len(files)}:normalize=0:dropout_transition=0[out]"
    args += ["-filter_complex", filt, "-map", "[out]", "-c:a", "pcm_f32le", "-ar", str(sample_rate), str(output)]
    subprocess.run(args, check=True)
    return output


def _gain_file(source: Path, output: Path, gain_db: float, sample_rate: int) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
        "-af", f"volume={float(gain_db):.4f}dB", "-c:a", "pcm_f32le", "-ar", str(sample_rate), str(output),
    ], check=True)
    return output


def selected_audio_clips(track: Track) -> list[Clip]:
    """Return the audio clips that should currently render for a track."""
    audio_clips = [c for c in track.clips if c.kind == "audio" and not c.muted]
    if not audio_clips:
        return []
    committed = [c for c in audio_clips if bool(c.metadata.get("committed", False))]
    if committed:
        return committed
    max_lane = max((c.take_lane for c in audio_clips), default=0)
    return [c for c in audio_clips if c.take_lane == max_lane]


def render_track(track: Track, session: StudioSession, project_root: Path, work_dir: Path) -> Path | None:
    if track.mute or track.role == "bus":
        return None
    selected = selected_audio_clips(track)
    if not selected:
        return None

    clip_files = [
        _clip_audio(clip, project_root, work_dir / "clips" / f"{track.id}_{i:03d}.wav", session.sample_rate)
        for i, clip in enumerate(selected)
    ]
    dry = _mix_files(clip_files, work_dir / "tracks" / f"{track.id}_dry.wav", session.sample_rate)
    processed = work_dir / "tracks" / f"{track.id}_processed.wav"
    render_effects(dry, processed, track.effects, sample_rate=session.sample_rate)
    final = work_dir / "tracks" / f"{track.id}_final.wav"
    apply_track_automation(processed, final, track, expected_sample_rate=session.sample_rate)
    return final


def _render_bus(bus: Track, inputs: list[Path], session: StudioSession, work_dir: Path) -> Path | None:
    if bus.mute or not inputs:
        return None
    dry = _mix_files(inputs, work_dir / "buses" / f"{bus.id}_dry.wav", session.sample_rate)
    processed = work_dir / "buses" / f"{bus.id}_processed.wav"
    render_effects(dry, processed, bus.effects, sample_rate=session.sample_rate)
    final = work_dir / "buses" / f"{bus.id}_final.wav"
    apply_track_automation(processed, final, bus, expected_sample_rate=session.sample_rate)
    return final


def render_session(session: StudioSession, project_root: Path, output: Path, work_dir: Path | None = None) -> Path:
    """Render a StudioSession to real waveform audio with parallel auxiliary buses.

    MIDI/lyrics/marker clips are never rendered. Sends are post-fader waveform copies into bus effects;
    a bus cannot create sound without a real-audio source feeding it.
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required for Aura's real-audio session mixer")
    work_dir = work_dir or project_root / "work" / "session_mix"
    work_dir.mkdir(parents=True, exist_ok=True)

    sources = [t for t in session.tracks if t.role not in {"master", "bus"} and not t.mute]
    buses = [t for t in session.tracks if t.role == "bus" and not t.mute]
    source_solos = [t for t in sources if t.solo]
    bus_solos = [t for t in buses if t.solo]

    feed_sources = source_solos if source_solos else sources
    direct_sources = source_solos if source_solos else ([] if bus_solos else sources)
    active_buses = bus_solos if bus_solos else buses

    rendered_by_id: dict[str, Path] = {}
    for track in feed_sources:
        rendered = render_track(track, session, project_root, work_dir)
        if rendered is not None:
            rendered_by_id[track.id] = rendered

    master_inputs: list[Path] = [rendered_by_id[t.id] for t in direct_sources if t.id in rendered_by_id]
    active_bus_ids = {bus.id for bus in active_buses}
    sends_by_bus: dict[str, list[Path]] = {bus.id: [] for bus in active_buses}
    for source in feed_sources:
        rendered = rendered_by_id.get(source.id)
        if rendered is None:
            continue
        for index, send in enumerate(source.sends):
            if not send.enabled or send.bus_track_id not in active_bus_ids:
                continue
            send_file = _gain_file(
                rendered,
                work_dir / "sends" / f"{source.id}_{index:03d}_{send.bus_track_id}.wav",
                send.level_db,
                session.sample_rate,
            )
            sends_by_bus[send.bus_track_id].append(send_file)

    for bus in active_buses:
        rendered_bus = _render_bus(bus, sends_by_bus.get(bus.id, []), session, work_dir)
        if rendered_bus is not None:
            master_inputs.append(rendered_bus)

    if not master_inputs:
        raise RuntimeError("Aura session has no real-audio clips to render. Symbolic/MIDI clips cannot become the final master.")

    premaster = _mix_files(master_inputs, work_dir / "premaster.wav", session.sample_rate)
    master_tracks = [t for t in session.tracks if t.role == "master"]
    output.parent.mkdir(parents=True, exist_ok=True)
    if master_tracks and master_tracks[0].effects:
        master_fx = work_dir / "master_fx.wav"
        render_effects(premaster, master_fx, master_tracks[0].effects, sample_rate=session.sample_rate)
        apply_track_automation(master_fx, output, master_tracks[0], expected_sample_rate=session.sample_rate)
    elif master_tracks:
        apply_track_automation(premaster, output, master_tracks[0], expected_sample_rate=session.sample_rate)
    else:
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(premaster),
            "-c:a", "pcm_s24le", "-ar", str(session.sample_rate), str(output),
        ], check=True)
    return output
