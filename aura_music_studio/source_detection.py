from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np


ROLE_TOKENS = {
    "vocals": {"vocal", "vocals", "voice", "vox", "singing", "singer", "leadvox"},
    "guitar": {"guitar", "gtr", "acoustic", "electricguitar"},
    "bass": {"bass", "bassguitar"},
    "drums": {"drum", "drums", "kit", "percussion", "beat"},
    "piano": {"piano", "keys", "keyboard", "grand", "upright"},
    "synth": {"synth", "synthesizer", "pad", "arp"},
    "strings": {"strings", "violin", "cello", "viola"},
    "brass": {"brass", "trumpet", "trombone", "horn"},
    "woodwinds": {"flute", "clarinet", "sax", "saxophone", "woodwind"},
}


def _token_guess(name: str, tags: list[str] | None = None) -> tuple[str | None, float, list[str]]:
    normalized = " ".join([Path(name).stem.lower(), *[x.lower() for x in (tags or [])]])
    compact = "".join(ch if ch.isalnum() else " " for ch in normalized)
    words = set(compact.split())
    for role, tokens in ROLE_TOKENS.items():
        hits = sorted(words & tokens)
        if hits:
            return role, .96, [f"Matched upload metadata token(s): {', '.join(hits)}"]
    return None, 0.0, []


def detect_source_role(path: Path, *, name: str = "", tags: list[str] | None = None) -> dict:
    """Suggest the musical role of an isolated upload.

    This is a lightweight local classifier, not an identity detector. Metadata wins when explicit;
    waveform heuristics are used only to make a useful suggestion and the member can always override it.
    """
    metadata_role, confidence, reasons = _token_guess(name or path.name, tags)
    if metadata_role:
        return {"role": metadata_role, "confidence": confidence, "reasons": reasons, "method": "metadata+filename"}

    y, sr = librosa.load(path, sr=22050, mono=True, duration=120)
    if not len(y):
        return {"role": "other", "confidence": 0.0, "reasons": ["Empty audio"], "method": "waveform"}

    harmonic, percussive = librosa.effects.hpss(y)
    harm_rms = float(np.sqrt(np.mean(harmonic**2)))
    perc_rms = float(np.sqrt(np.mean(percussive**2)))
    perc_ratio = perc_rms / max(harm_rms + perc_rms, 1e-8)
    centroid = float(np.median(librosa.feature.spectral_centroid(y=y, sr=sr)[0]))
    onset = librosa.onset.onset_strength(y=y, sr=sr)
    onset_density = float(np.mean(onset > (np.median(onset) + np.std(onset))))

    f0, voiced, _ = librosa.pyin(
        y,
        sr=sr,
        fmin=librosa.note_to_hz("C1"),
        fmax=librosa.note_to_hz("C7"),
    )
    valid = f0[np.isfinite(f0)] if f0 is not None else np.array([])
    median_f0 = float(np.median(valid)) if valid.size else None
    voiced_ratio = float(np.mean(voiced)) if voiced is not None else 0.0

    candidates: list[tuple[float, str, list[str]]] = []
    if perc_ratio > .68 and onset_density > .08:
        candidates.append((.88, "drums", [f"Strong percussive energy ({perc_ratio:.2f})", "Dense transient/onset pattern"]))
    if median_f0 is not None and median_f0 < 190 and voiced_ratio > .35 and centroid < 1700:
        candidates.append((.76, "bass", [f"Low median fundamental ({median_f0:.0f} Hz)", f"Low spectral centroid ({centroid:.0f} Hz)"]))
    if voiced_ratio > .58 and .18 < perc_ratio < .62 and median_f0 is not None and 90 <= median_f0 <= 650:
        candidates.append((.70, "vocals", [f"High voiced-frame ratio ({voiced_ratio:.2f})", "Monophonic pitched contour in common singing range"]))
    if harm_rms > perc_rms and voiced_ratio > .25 and 700 <= centroid <= 3200:
        candidates.append((.54, "guitar", ["Harmonic-dominant pitched source", f"Mid-band spectral centroid ({centroid:.0f} Hz)"]))
    if harm_rms > perc_rms and voiced_ratio < .45 and 900 <= centroid <= 3500:
        candidates.append((.48, "piano", ["Harmonic-dominant source with lower sustained voicing confidence", f"Spectral centroid {centroid:.0f} Hz"]))

    if not candidates:
        return {
            "role": "other",
            "confidence": .25,
            "reasons": ["Waveform did not strongly match Aura's isolated-source heuristics"],
            "method": "waveform",
            "metrics": {"percussive_ratio": perc_ratio, "voiced_ratio": voiced_ratio, "median_f0_hz": median_f0, "centroid_hz": centroid},
        }
    score, role, selected_reasons = max(candidates, key=lambda x: x[0])
    return {
        "role": role,
        "confidence": score,
        "reasons": selected_reasons,
        "method": "waveform",
        "metrics": {"percussive_ratio": perc_ratio, "voiced_ratio": voiced_ratio, "median_f0_hz": median_f0, "centroid_hz": centroid},
    }
