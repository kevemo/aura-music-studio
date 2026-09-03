from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import librosa
import mido
import numpy as np


TRANSCRIPTION_ENGINE_VERSION = "aura-audio-midi-v2"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _report_path(output_midi: Path) -> Path:
    return output_midi.with_suffix(output_midi.suffix + ".aura.json")


def _midi_summary(path: Path) -> dict:
    midi = mido.MidiFile(path)
    absolute = 0
    notes: list[tuple[int, int]] = []
    active: dict[tuple[int, int], list[int]] = {}
    velocities: list[int] = []
    for message in mido.merge_tracks(midi.tracks):
        absolute += int(message.time or 0)
        if message.type == "note_on" and int(message.velocity) > 0:
            key = (int(message.channel), int(message.note))
            active.setdefault(key, []).append(absolute)
            velocities.append(int(message.velocity))
        elif message.type in {"note_off", "note_on"}:
            key = (int(message.channel), int(message.note))
            starts = active.get(key) or []
            if starts:
                start = starts.pop(0)
                if not starts:
                    active.pop(key, None)
                notes.append((key[1], max(1, absolute - start)))
    pitches = [note for note, _duration in notes]
    return {
        "note_count": len(notes),
        "pitch_low": min(pitches) if pitches else None,
        "pitch_high": max(pitches) if pitches else None,
        "velocity_min": min(velocities) if velocities else None,
        "velocity_max": max(velocities) if velocities else None,
        "velocity_mean": round(float(np.mean(velocities)), 3) if velocities else None,
        "ticks_per_beat": int(midi.ticks_per_beat or 480),
    }


def _write_report(
    source: Path,
    output_midi: Path,
    *,
    engine: str,
    mode_requested: str,
    bpm: float,
    extra: dict | None = None,
) -> dict:
    summary = _midi_summary(output_midi)
    payload = {
        "schema_version": 1,
        "engine_version": TRANSCRIPTION_ENGINE_VERSION,
        "engine": engine,
        "mode_requested": mode_requested,
        "source_sha256": _sha256(source),
        "output_sha256": _sha256(output_midi),
        "bpm": round(float(bpm), 4),
        "symbolic_guide_only": True,
        "final_audio": False,
        **summary,
        **(extra or {}),
    }
    report = _report_path(output_midi)
    report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def transcription_metadata(output_midi: Path) -> dict:
    """Read Aura's project-local transcription provenance sidecar."""
    report = _report_path(output_midi)
    if not report.is_file():
        return {}
    try:
        value = json.loads(report.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def audio_to_midi(
    source: Path,
    output_midi: Path,
    *,
    onset_threshold: float = 0.5,
    bpm: float = 120.0,
    mode: str = "auto",
    min_note_ms: float = 45.0,
    velocity_tracking: bool = True,
) -> Path:
    """Transcribe real audio into editable MIDI control data.

    ``auto`` prefers Basic Pitch for polyphonic material when that optional runtime is
    installed and falls back to Aura's pYIN monophonic path. ``polyphonic`` fails closed
    when Basic Pitch is unavailable instead of pretending a monophonic scan is polyphonic.
    MIDI produced here is symbolic control/edit data and is never final rendered audio.
    """
    source = Path(source)
    output_midi = Path(output_midi)
    if not source.is_file():
        raise FileNotFoundError(source)
    if output_midi.suffix.lower() not in {".mid", ".midi"}:
        raise ValueError("Audio transcription output must use .mid or .midi")
    normalized_mode = str(mode or "auto").strip().lower()
    if normalized_mode not in {"auto", "polyphonic", "monophonic"}:
        raise ValueError("Transcription mode must be auto, polyphonic or monophonic")
    if not 0.05 <= float(onset_threshold) <= 0.95:
        raise ValueError("onset_threshold must be between 0.05 and 0.95")
    if not 20.0 <= float(bpm) <= 400.0:
        raise ValueError("bpm must be between 20 and 400")
    if not 20.0 <= float(min_note_ms) <= 1000.0:
        raise ValueError("min_note_ms must be between 20 and 1000")

    output_midi.parent.mkdir(parents=True, exist_ok=True)
    basic_pitch_available = importlib.util.find_spec("basic_pitch") is not None
    if normalized_mode in {"auto", "polyphonic"} and basic_pitch_available:
        work_dir = output_midi.parent / f".basic_pitch_{uuid4().hex}"
        work_dir.mkdir(parents=True, exist_ok=False)
        try:
            from basic_pitch.inference import predict_and_save

            predict_and_save(
                [str(source)],
                str(work_dir),
                save_midi=True,
                sonify_midi=False,
                save_model_outputs=False,
                save_notes=False,
                onset_threshold=float(onset_threshold),
            )
            candidates = sorted(work_dir.glob("*.mid")) + sorted(work_dir.glob("*.midi"))
            if not candidates:
                raise RuntimeError("Basic Pitch did not produce MIDI output")
            shutil.copy2(candidates[0], output_midi)
            summary = _midi_summary(output_midi)
            if int(summary["note_count"]) <= 0:
                raise RuntimeError("Basic Pitch produced an empty MIDI transcription")
            _write_report(
                source,
                output_midi,
                engine="basic_pitch",
                mode_requested=normalized_mode,
                bpm=bpm,
                extra={"polyphonic_capable": True, "velocity_tracking": True},
            )
            return output_midi
        except Exception:
            output_midi.unlink(missing_ok=True)
            if normalized_mode == "polyphonic":
                raise
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
    elif normalized_mode == "polyphonic":
        raise RuntimeError("Polyphonic transcription requires the optional Basic Pitch runtime")

    return monophonic_audio_to_midi(
        source,
        output_midi,
        bpm=bpm,
        min_note_ms=min_note_ms,
        velocity_tracking=velocity_tracking,
        mode_requested=normalized_mode,
    )


def monophonic_audio_to_midi(
    source: Path,
    output_midi: Path,
    bpm: float = 120.0,
    *,
    min_note_ms: float = 45.0,
    velocity_tracking: bool = True,
    mode_requested: str = "monophonic",
) -> Path:
    """Performance-aware monophonic transcription using pYIN pitch and source dynamics."""
    source = Path(source)
    output_midi = Path(output_midi)
    y, sr = librosa.load(source, sr=None, mono=True)
    if y.size == 0:
        raise ValueError("Audio transcription source is empty")
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    if not np.isfinite(peak) or peak <= 1e-6:
        raise ValueError("Audio transcription source is silent")

    hop = 256
    f0, voiced, voiced_probability = librosa.pyin(
        y,
        fmin=librosa.note_to_hz("C1"),
        fmax=librosa.note_to_hz("C8"),
        sr=sr,
        hop_length=hop,
    )
    if f0 is None or len(f0) == 0:
        raise RuntimeError("Pitch analysis produced no frames")
    times = librosa.frames_to_time(np.arange(len(f0)), sr=sr, hop_length=hop)
    midi_float = np.where(np.isfinite(f0), librosa.hz_to_midi(f0), np.nan)
    rounded = np.where(np.isfinite(midi_float), np.rint(midi_float), np.nan)

    # A short rolling median suppresses single-frame pYIN pitch chatter without quantising
    # the performance timing itself.
    finite = np.where(np.isfinite(rounded), rounded, -999.0)
    if len(finite) >= 5:
        padded = np.pad(finite, (2, 2), mode="edge")
        windows = np.lib.stride_tricks.sliding_window_view(padded, 5)
        finite = np.median(windows, axis=1)
    notes = np.where(finite > -100.0, finite, np.nan)

    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop, center=True)[0]
    if len(rms) < len(notes):
        rms = np.pad(rms, (0, len(notes) - len(rms)), mode="edge")
    rms = rms[: len(notes)]
    positive_rms = rms[rms > 1e-8]
    reference_rms = float(np.percentile(positive_rms, 90)) if positive_rms.size else 1.0
    reference_rms = max(reference_rms, 1e-8)

    min_duration = float(min_note_ms) / 1000.0
    segments: list[tuple[float, float, int, int, float]] = []
    start_index: int | None = None
    active_note: int | None = None

    def close_segment(end_index: int) -> None:
        nonlocal start_index, active_note
        if start_index is None or active_note is None:
            return
        start_sec = float(times[start_index])
        end_sec = float(times[min(max(end_index, start_index + 1), len(times) - 1)])
        if end_sec - start_sec < min_duration:
            return
        segment_rms = float(np.median(rms[start_index : max(start_index + 1, end_index)]))
        if velocity_tracking:
            relative = max(0.0, min(1.25, segment_rms / reference_rms))
            velocity = int(np.clip(round(34.0 + 82.0 * np.sqrt(relative)), 28, 124))
        else:
            velocity = 90
        probability_slice = voiced_probability[start_index : max(start_index + 1, end_index)]
        confidence = float(np.nanmean(probability_slice)) if probability_slice.size else 0.0
        segments.append((start_sec, end_sec, active_note, velocity, confidence))

    for index, note in enumerate(notes):
        current = int(note) if np.isfinite(note) and bool(voiced[index]) else None
        if current != active_note:
            close_segment(index)
            active_note = current
            start_index = index if current is not None else None
    if active_note is not None and start_index is not None:
        close_segment(len(notes) - 1)

    if not segments:
        raise RuntimeError("No stable pitched notes were detected in the audio")

    ticks_per_beat = 480
    midi = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="Aura Audio Transcription", time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(float(bpm)), time=0))
    seconds_per_tick = (60.0 / float(bpm)) / ticks_per_beat
    events: list[tuple[float, bool, int, int]] = []
    for start_sec, end_sec, pitch, velocity, _confidence in segments:
        events.append((start_sec, True, pitch, velocity))
        events.append((end_sec, False, pitch, 0))
    events.sort(key=lambda row: (row[0], 1 if row[1] else 0))
    last_tick = 0
    for seconds, on, pitch, velocity in events:
        tick = max(last_tick, round(float(seconds) / seconds_per_tick))
        track.append(
            mido.Message(
                "note_on" if on else "note_off",
                note=int(np.clip(pitch, 0, 127)),
                velocity=int(velocity),
                time=tick - last_tick,
            )
        )
        last_tick = tick
    output_midi.parent.mkdir(parents=True, exist_ok=True)
    midi.save(output_midi)

    _write_report(
        source,
        output_midi,
        engine="librosa_pyin",
        mode_requested=mode_requested,
        bpm=bpm,
        extra={
            "polyphonic_capable": False,
            "velocity_tracking": bool(velocity_tracking),
            "minimum_note_ms": round(float(min_note_ms), 3),
            "mean_voicing_confidence": round(float(np.mean([row[4] for row in segments])), 4),
        },
    )
    return output_midi


def midi_to_musicxml(midi_path: Path, output_xml: Path) -> Path:
    from music21 import converter

    score = converter.parse(str(midi_path))
    output_xml.parent.mkdir(parents=True, exist_ok=True)
    score.write("musicxml", fp=str(output_xml))
    return output_xml


def midi_to_pdf(midi_path: Path, output_pdf: Path) -> Path:
    """Requires MuseScore in PATH. Creates printable notation from Aura's transcription."""
    musescore = shutil.which("musescore4") or shutil.which("musescore") or shutil.which("mscore")
    if not musescore:
        raise RuntimeError("MuseScore is required for PDF notation export")
    xml = output_pdf.with_suffix(".musicxml")
    midi_to_musicxml(midi_path, xml)
    subprocess.run([musescore, str(xml), "-o", str(output_pdf)], check=True)
    return output_pdf
