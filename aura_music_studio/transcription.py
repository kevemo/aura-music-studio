from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import librosa
import mido
import numpy as np


def audio_to_midi(source: Path, output_midi: Path, *, onset_threshold: float = 0.5) -> Path:
    """Prefer Spotify Basic Pitch for polyphonic transcription, with pYIN fallback."""
    output_midi.parent.mkdir(parents=True, exist_ok=True)
    if importlib.util.find_spec("basic_pitch") is not None:
        try:
            from basic_pitch.inference import predict_and_save
            out_dir = output_midi.parent / "basic_pitch"
            out_dir.mkdir(parents=True, exist_ok=True)
            predict_and_save(
                [str(source)],
                str(out_dir),
                save_midi=True,
                sonify_midi=False,
                save_model_outputs=False,
                save_notes=False,
                onset_threshold=onset_threshold,
            )
            candidates = list(out_dir.glob("*.mid")) + list(out_dir.glob("*.midi"))
            if candidates:
                shutil.copy2(candidates[0], output_midi)
                return output_midi
        except Exception:
            pass
    return monophonic_audio_to_midi(source, output_midi)


def monophonic_audio_to_midi(source: Path, output_midi: Path, bpm: float = 120.0) -> Path:
    y, sr = librosa.load(source, sr=None, mono=True)
    hop = 256
    f0, voiced, _ = librosa.pyin(
        y,
        fmin=librosa.note_to_hz("C1"),
        fmax=librosa.note_to_hz("C8"),
        sr=sr,
        hop_length=hop,
    )
    times = librosa.frames_to_time(np.arange(len(f0)), sr=sr, hop_length=hop)
    midi_notes = np.where(np.isfinite(f0), np.rint(librosa.hz_to_midi(f0)), np.nan)
    ticks_per_beat = 480
    mf = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack(); mf.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))
    seconds_per_tick = (60.0 / bpm) / ticks_per_beat
    events = []
    active = None; start = None
    for i, note in enumerate(midi_notes):
        n = int(note) if np.isfinite(note) and bool(voiced[i]) else None
        t = float(times[i])
        if n != active:
            if active is not None and start is not None and t - start >= 0.04:
                events.extend([(start, True, active), (t, False, active)])
            active = n; start = t if n is not None else None
    if active is not None and start is not None:
        events.extend([(start, True, active), (float(times[-1]), False, active)])
    events.sort(key=lambda x: (x[0], not x[1]))
    last = 0
    for sec, on, note in events:
        tick = max(last, round(sec / seconds_per_tick)); delta = tick - last
        track.append(mido.Message("note_on" if on else "note_off", note=note, velocity=90 if on else 0, time=delta))
        last = tick
    mf.save(output_midi)
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
