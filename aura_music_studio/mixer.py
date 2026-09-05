from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .automation import (
    apply_gain_automation,
    apply_track_automation,
    blend_audio_with_mix_automation,
    find_automation_lane,
)
from .effects import render_effects
from .session import AutomationLane, Clip, StudioSession, Track


def _resolve(source: str, project_root: Path) -> Path:
    p = Path(source)
    if not p.is_absolute():
        p = project_root / p
    return p.resolve()


def _clip_audio(clip: Clip, track: Track, project_root: Path, output: Path, sample_rate: int) -> Path:
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
    gain_lane = find_automation_lane(track, f"clip:{clip.id}:gain_db")
    filters = [
        f"atrim=start={max(0.0, clip.source_offset)}:duration={duration}",
        "asetpts=PTS-STARTPTS",
        # When a clip lane exists its default is still clip.gain_db, but gain is applied after
        # timeline delay so lane times use the same absolute session clock as all other lanes.
        f"volume={0.0 if gain_lane else clip.gain_db}dB",
    ]
    if fade_in > 0:
        filters.append(f"afade=t=in:st=0:d={fade_in}")
    if fade_out > 0:
        filters.append(f"afade=t=out:st={max(0.0, duration-fade_out)}:d={fade_out}")
    delay_ms = max(0, int(round(clip.start * 1000)))
    filters.append(f"adelay={delay_ms}|{delay_ms}")

    encoded_output = output if gain_lane is None else output.with_name(output.stem + "_pre_gain.wav")
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
        "-af", ",".join(filters), "-c:a", "pcm_f32le", "-ar", str(sample_rate), str(encoded_output),
    ], check=True)
    if gain_lane is not None:
        apply_gain_automation(
            encoded_output,
            output,
            gain_lane,
            default_db=clip.gain_db,
            expected_sample_rate=sample_rate,
        )
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


def _gain_file(
    source: Path,
    output: Path,
    gain_db: float,
    sample_rate: int,
    lane: AutomationLane | None = None,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    if lane is not None:
        return apply_gain_automation(
            source,
            output,
            lane,
            default_db=gain_db,
            expected_sample_rate=sample_rate,
        )
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
        "-af", f"volume={float(gain_db):.4f}dB", "-c:a", "pcm_f32le", "-ar", str(sample_rate), str(output),
    ], check=True)
    return output


def _render_effect_chain(
    source: Path,
    output: Path,
    track: Track,
    *,
    sample_rate: int,
    work_dir: Path,
    stem: str,
) -> Path:
    """Render effects sequentially with automatable per-effect wet/dry mix."""
    enabled = [effect for effect in track.effects if effect.enabled]
    if not enabled:
        return render_effects(source, output, [], sample_rate=sample_rate)

    current = source
    fx_dir = work_dir / "automated_fx" / stem
    fx_dir.mkdir(parents=True, exist_ok=True)
    for index, effect in enumerate(enabled):
        wet = fx_dir / f"{index:03d}_{effect.id}_wet.wav"
        render_effects(current, wet, [effect], sample_rate=sample_rate)
        lane = find_automation_lane(track, f"fx:{effect.id}:mix")
        if lane is not None or effect.mix < 0.999999:
            blended = fx_dir / f"{index:03d}_{effect.id}_mix.wav"
            blend_audio_with_mix_automation(
                current,
                wet,
                blended,
                lane,
                default_mix=effect.mix,
                expected_sample_rate=sample_rate,
            )
            current = blended
        else:
            current = wet

    output.parent.mkdir(parents=True, exist_ok=True)
    if current.resolve() != output.resolve():
        shutil.copyfile(current, output)
    return output


def selected_audio_clips(track: Track) -> list[Clip]:
    """Return the audio clips that should currently render for a track."""
    audio_clips = [clip for clip in track.clips if clip.kind == "audio" and not clip.muted]
    if not audio_clips:
        return []
    committed = [clip for clip in audio_clips if bool(clip.metadata.get("committed", False))]
    if committed:
        return committed
    max_lane = max((clip.take_lane for clip in audio_clips), default=0)
    return [clip for clip in audio_clips if clip.take_lane == max_lane]


def render_track(track: Track, session: StudioSession, project_root: Path, work_dir: Path) -> Path | None:
    if track.mute or track.role == "bus":
        return None
    selected = selected_audio_clips(track)
    if not selected:
        return None

    clip_files = [
        _clip_audio(clip, track, project_root, work_dir / "clips" / f"{track.id}_{index:03d}.wav", session.sample_rate)
        for index, clip in enumerate(selected)
    ]
    dry = _mix_files(clip_files, work_dir / "tracks" / f"{track.id}_dry.wav", session.sample_rate)
    processed = work_dir / "tracks" / f"{track.id}_processed.wav"
    _render_effect_chain(
        dry,
        processed,
        track,
        sample_rate=session.sample_rate,
        work_dir=work_dir,
        stem=f"track_{track.id}",
    )
    final = work_dir / "tracks" / f"{track.id}_final.wav"
    apply_track_automation(processed, final, track, expected_sample_rate=session.sample_rate)
    return final


def _render_bus(bus: Track, inputs: list[Path], session: StudioSession, work_dir: Path) -> Path | None:
    if bus.mute or not inputs:
        return None
    dry = _mix_files(inputs, work_dir / "buses" / f"{bus.id}_dry.wav", session.sample_rate)
    processed = work_dir / "buses" / f"{bus.id}_processed.wav"
    _render_effect_chain(
        dry,
        processed,
        bus,
        sample_rate=session.sample_rate,
        work_dir=work_dir,
        stem=f"bus_{bus.id}",
    )
    final = work_dir / "buses" / f"{bus.id}_final.wav"
    apply_track_automation(processed, final, bus, expected_sample_rate=session.sample_rate)
    return final


def render_session(session: StudioSession, project_root: Path, output: Path, work_dir: Path | None = None) -> Path:
    """Render a StudioSession to real waveform audio with deep non-destructive automation.

    MIDI/lyrics/marker clips are never rendered. Automation can address track fader/pan,
    ``clip:<id>:gain_db``, ``send:<id>:level_db`` and ``fx:<id>:mix``. Sends remain post-fader
    waveform copies into bus effects; a bus cannot create sound without a real-audio source.
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required for Aura's real-audio session mixer")
    work_dir = work_dir or project_root / "work" / "session_mix"
    work_dir.mkdir(parents=True, exist_ok=True)

    sources = [track for track in session.tracks if track.role not in {"master", "bus"} and not track.mute]
    buses = [track for track in session.tracks if track.role == "bus" and not track.mute]
    source_solos = [track for track in sources if track.solo]
    bus_solos = [track for track in buses if track.solo]

    feed_sources = source_solos if source_solos else sources
    direct_sources = source_solos if source_solos else ([] if bus_solos else sources)
    active_buses = bus_solos if bus_solos else buses

    rendered_by_id: dict[str, Path] = {}
    for track in feed_sources:
        rendered = render_track(track, session, project_root, work_dir)
        if rendered is not None:
            rendered_by_id[track.id] = rendered

    master_inputs: list[Path] = [rendered_by_id[track.id] for track in direct_sources if track.id in rendered_by_id]
    active_bus_ids = {bus.id for bus in active_buses}
    sends_by_bus: dict[str, list[Path]] = {bus.id: [] for bus in active_buses}
    for source in feed_sources:
        rendered = rendered_by_id.get(source.id)
        if rendered is None:
            continue
        for index, send in enumerate(source.sends):
            if not send.enabled or send.bus_track_id not in active_bus_ids:
                continue
            lane = find_automation_lane(source, f"send:{send.id}:level_db")
            send_file = _gain_file(
                rendered,
                work_dir / "sends" / f"{source.id}_{index:03d}_{send.bus_track_id}.wav",
                send.level_db,
                session.sample_rate,
                lane,
            )
            sends_by_bus[send.bus_track_id].append(send_file)

    for bus in active_buses:
        rendered_bus = _render_bus(bus, sends_by_bus.get(bus.id, []), session, work_dir)
        if rendered_bus is not None:
            master_inputs.append(rendered_bus)

    if not master_inputs:
        raise RuntimeError("Aura session has no real-audio clips to render. Symbolic/MIDI clips cannot become the final master.")

    premaster = _mix_files(master_inputs, work_dir / "premaster.wav", session.sample_rate)
    master_tracks = [track for track in session.tracks if track.role == "master"]
    output.parent.mkdir(parents=True, exist_ok=True)
    if master_tracks:
        master = master_tracks[0]
        if master.effects:
            master_fx = work_dir / "master_fx.wav"
            _render_effect_chain(
                premaster,
                master_fx,
                master,
                sample_rate=session.sample_rate,
                work_dir=work_dir,
                stem=f"master_{master.id}",
            )
            apply_track_automation(master_fx, output, master, expected_sample_rate=session.sample_rate)
        else:
            apply_track_automation(premaster, output, master, expected_sample_rate=session.sample_rate)
    else:
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(premaster),
            "-c:a", "pcm_s24le", "-ar", str(session.sample_rate), str(output),
        ], check=True)
    return output


__all__ = ["render_session", "render_track", "selected_audio_clips"]
