from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

import librosa
import mido
import numpy as np

from .rights import authorize_voice_profile


@dataclass
class HarmonySpec:
    key: str = "C Major"
    voices: tuple[str, ...] = ("third_above", "third_below")
    humanize_ms: int = 18
    velocity_scale: float = 0.82


def audio_vocal_to_midi(source: Path, output_midi: Path, bpm: float = 120.0) -> Path:
    """Monophonic lead-vocal melody scan using pYIN.

    This creates an editable guide MIDI. For dense/polyphonic material Aura should separate the
    vocal first, then scan the isolated vocal.
    """
    y, sr = librosa.load(source, sr=None, mono=True)
    hop = 256
    f0, voiced, _ = librosa.pyin(
        y,
        fmin=librosa.note_to_hz("C2"),
        fmax=librosa.note_to_hz("C7"),
        sr=sr,
        hop_length=hop,
    )
    times = librosa.frames_to_time(np.arange(len(f0)), sr=sr, hop_length=hop)
    midi_notes = np.where(np.isfinite(f0), np.rint(librosa.hz_to_midi(f0)), np.nan)

    ticks_per_beat = 480
    mf = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    mf.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))

    sec_per_tick = (60.0 / bpm) / ticks_per_beat
    events = []
    active = None
    start_time = None
    for i, note in enumerate(midi_notes):
        note_int = int(note) if np.isfinite(note) and voiced[i] else None
        t = float(times[i])
        if note_int != active:
            if active is not None and start_time is not None:
                events.append((start_time, 1, active, 88))
                events.append((t, 0, active, 0))
            active = note_int
            start_time = t if note_int is not None else None
    if active is not None and start_time is not None:
        events.append((start_time, 1, active, 88))
        events.append((float(times[-1]), 0, active, 0))

    events.sort(key=lambda x: (x[0], -x[1]))
    last_tick = 0
    for t, on, note, vel in events:
        tick = max(last_tick, round(t / sec_per_tick))
        delta = tick - last_tick
        track.append(mido.Message("note_on" if on else "note_off", note=note, velocity=vel, time=delta))
        last_tick = tick
    output_midi.parent.mkdir(parents=True, exist_ok=True)
    mf.save(output_midi)
    return output_midi


def _scale_pitch_classes(key: str) -> list[int]:
    names = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5,
             "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
    parts = key.split()
    root = names.get(parts[0], 0)
    minor = len(parts) > 1 and parts[1].lower().startswith("min")
    intervals = [0, 2, 3, 5, 7, 8, 10] if minor else [0, 2, 4, 5, 7, 9, 11]
    return [(root + x) % 12 for x in intervals]


def _diatonic_shift(note: int, steps: int, scale: list[int]) -> int:
    direction = 1 if steps >= 0 else -1
    remaining = abs(steps)
    current = note
    while remaining:
        current += direction
        if current % 12 in scale:
            remaining -= 1
    return current


def generate_harmony_midis(lead_midi: Path, output_dir: Path, spec: HarmonySpec) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    source = mido.MidiFile(lead_midi)
    scale = _scale_pitch_classes(spec.key)
    shifts = {
        "third_above": 2,
        "third_below": -2,
        "fifth_above": 4,
        "sixth_below": -5,
        "octave_above": 7,
        "octave_below": -7,
    }
    outputs = {}
    for voice in spec.voices:
        if voice not in shifts:
            continue
        result = mido.MidiFile(ticks_per_beat=source.ticks_per_beat)
        for src_track in source.tracks:
            dst = mido.MidiTrack()
            result.tracks.append(dst)
            for msg in src_track:
                new = msg.copy()
                if msg.type in {"note_on", "note_off"}:
                    new.note = int(np.clip(_diatonic_shift(msg.note, shifts[voice], scale), 0, 127))
                    if msg.type == "note_on":
                        new.velocity = int(np.clip(round(msg.velocity * spec.velocity_scale), 1, 127))
                dst.append(new)
        path = output_dir / f"harmony_{voice}.mid"
        result.save(path)
        outputs[voice] = path
    return outputs


def render_harmony_voice(
    harmony_midi: Path,
    lyrics_file: Path,
    output: Path,
    *,
    rights_root: Path | None = None,
    voice_profile_id: str | None = None,
) -> Path:
    """Render harmony audio, reauthorizing any Voice Profile at execution time."""
    voice_profile_json = ""
    if voice_profile_id:
        if rights_root is None:
            raise PermissionError("Authoritative Voice Profile rights storage is required for synthesis.")
        profile = authorize_voice_profile(rights_root, voice_profile_id, "backing_harmony")
        voice_profile_json = profile.model_dump_json()

    command = os.getenv("AURA_DIFFSINGER_CMD")
    if not command:
        raise RuntimeError("AURA_DIFFSINGER_CMD is not configured")
    env = os.environ.copy()
    env.update({
        "AURA_HARMONY_MIDI": str(harmony_midi),
        "AURA_LYRICS": str(lyrics_file),
        "AURA_VOICE_PROFILE": voice_profile_json,
        "AURA_OUTPUT": str(output),
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(shlex.split(command), env=env, check=True)
    if not output.exists():
        raise RuntimeError(f"DiffSinger harmony command did not create {output}")
    return output
