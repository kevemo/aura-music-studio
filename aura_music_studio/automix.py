from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import soundfile as sf

from .session import StudioSession, Track


ROLE_TARGETS = {
    "master": -6.0,
    "vocals": -12.5,
    "backing_vocals": -18.0,
    "drums": -14.0,
    "bass": -15.0,
    "guitar": -17.0,
    "piano": -18.0,
    "keyboard": -18.0,
    "strings": -20.0,
    "synth": -19.0,
    "percussion": -21.0,
    "brass": -20.0,
    "woodwinds": -21.0,
    "fx": -24.0,
    "other": -20.0,
}

GENRE_OFFSETS = {
    "pop": {"vocals": 1.0, "drums": .5, "bass": .3, "synth": .2},
    "rock": {"drums": 1.2, "guitar": 1.0, "bass": .6, "vocals": .4},
    "metal": {"drums": 1.3, "guitar": 1.2, "bass": .8},
    "hiphop": {"vocals": 1.0, "bass": 1.4, "drums": .9, "keyboard": -.4},
    "electronic": {"bass": 1.0, "drums": .8, "synth": .8},
    "acoustic": {"vocals": 1.0, "guitar": .8, "drums": -.8, "strings": -.5},
    "jazz": {"vocals": .4, "drums": -.4, "bass": .5, "piano": .5, "guitar": .2},
    "cinematic": {"strings": .8, "brass": .5, "percussion": .4, "vocals": -.5},
}

PAN_DEFAULTS = {
    "vocals": 0.0,
    "backing_vocals": 0.0,
    "drums": 0.0,
    "bass": 0.0,
    "guitar": -0.18,
    "piano": 0.12,
    "keyboard": 0.18,
    "strings": 0.0,
    "synth": 0.22,
    "percussion": 0.28,
    "brass": -0.15,
    "woodwinds": 0.15,
    "fx": 0.3,
    "other": 0.0,
}


def _rms_db(path: Path) -> float | None:
    try:
        audio, _ = sf.read(path, always_2d=True, dtype="float32")
        if not len(audio):
            return None
        rms = float(np.sqrt(np.mean(np.square(audio))))
        return 20 * math.log10(max(rms, 1e-9))
    except Exception:
        return None


def _track_source(project: Path, track: Track) -> Path | None:
    candidates = [c for c in track.clips if c.kind == "audio" and c.source and not c.muted]
    if not candidates:
        return None
    # Prefer the highest take lane because generated alternatives append new lanes.
    clip = sorted(candidates, key=lambda c: (c.take_lane, c.start), reverse=True)[0]
    p = Path(clip.source)
    if not p.is_absolute():
        p = project / p
    return p if p.exists() else None


def apply_automix(session: StudioSession, project: Path, *, genre: str = "pop", intensity: float = .8) -> dict:
    """Apply editable gain/pan suggestions based on role, actual RMS and genre.

    This intentionally writes ordinary DAW controls rather than baking the mix destructively. Users
    can change any decision before rendering the session.
    """
    genre_key = (genre or "pop").strip().lower()
    offsets = GENRE_OFFSETS.get(genre_key, GENRE_OFFSETS["pop"])
    intensity = max(0.0, min(float(intensity), 1.0))
    changes = []
    same_role_counts: dict[str, int] = {}

    for track in session.tracks:
        if track.role == "master":
            continue
        source = _track_source(project, track)
        measured = _rms_db(source) if source else None
        target = ROLE_TARGETS.get(track.role, ROLE_TARGETS["other"]) + offsets.get(track.role, 0.0)
        suggested_gain = (target - measured) if measured is not None else 0.0
        suggested_gain = max(-12.0, min(suggested_gain, 12.0))
        old_gain = track.volume_db
        track.volume_db = old_gain + (suggested_gain - old_gain) * intensity

        base_pan = PAN_DEFAULTS.get(track.role, 0.0)
        index = same_role_counts.get(track.role, 0)
        same_role_counts[track.role] = index + 1
        # Pair repeated guitars/keys/backing vocals across the field while keeping bass/lead centered.
        if track.role in {"guitar", "keyboard", "piano", "synth", "backing_vocals", "percussion"} and index:
            sign = -1 if index % 2 else 1
            base_pan = sign * min(.65, abs(base_pan) + .12 * index)
        old_pan = track.pan
        track.pan = max(-1.0, min(1.0, old_pan + (base_pan - old_pan) * intensity))
        changes.append({
            "track_id": track.id,
            "track": track.name,
            "role": track.role,
            "measured_rms_db": measured,
            "target_rms_db": target,
            "old_volume_db": old_gain,
            "new_volume_db": track.volume_db,
            "old_pan": old_pan,
            "new_pan": track.pan,
        })

    session.generation_history.append({"action": "automix", "genre": genre_key, "intensity": intensity, "changes": changes})
    session.touch()
    return {"genre": genre_key, "intensity": intensity, "changes": changes}
