from __future__ import annotations

import json
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf
from pydantic import BaseModel, Field, model_validator


class StyleReference(BaseModel):
    path: str
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    role: str = "overall"  # overall | rhythm | harmony | instrumentation | production | vocal
    notes: str = ""


class StyleBlend(BaseModel):
    references: list[StyleReference]
    preserve_originality: bool = True
    description: str = ""

    @model_validator(mode="after")
    def require_reference(self):
        if not self.references:
            raise ValueError("At least one style reference is required")
        if sum(x.weight for x in self.references) <= 0:
            raise ValueError("At least one style reference must have non-zero weight")
        return self


def analyze_style(path: Path) -> dict:
    y, sr = librosa.load(path, sr=None, mono=True, duration=240)
    if len(y) == 0:
        raise ValueError("Empty style reference")
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    tempo = float(tempo[0] if hasattr(tempo, "__len__") else tempo)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    rms = librosa.feature.rms(y=y)[0]
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    contrast = librosa.feature.spectral_contrast(y=y, sr=sr)
    onset = librosa.onset.onset_strength(y=y, sr=sr)
    pc = int(chroma.mean(axis=1).argmax())
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return {
        "duration": float(librosa.get_duration(y=y, sr=sr)),
        "estimated_bpm": tempo,
        "dominant_pitch_class": names[pc],
        "mean_rms": float(np.mean(rms)),
        "spectral_centroid_hz": float(np.mean(centroid)),
        "spectral_contrast": float(np.mean(contrast)),
        "onset_density": float(np.mean(onset)),
    }


def build_style_dna(blend: StyleBlend) -> dict:
    analyses = []
    total = sum(r.weight for r in blend.references) or 1.0
    for ref in blend.references:
        p = Path(ref.path)
        if not p.exists():
            raise FileNotFoundError(p)
        analysis = analyze_style(p)
        analyses.append({"reference": ref.model_dump(), "analysis": analysis})

    def weighted(field: str) -> float:
        return float(sum(item["analysis"][field] * item["reference"]["weight"] for item in analyses) / total)

    pitch_votes: dict[str, float] = {}
    for item in analyses:
        pc = item["analysis"]["dominant_pitch_class"]
        pitch_votes[pc] = pitch_votes.get(pc, 0.0) + item["reference"]["weight"]

    dna = {
        "reference_count": len(analyses),
        "weighted_bpm": weighted("estimated_bpm"),
        "weighted_rms": weighted("mean_rms"),
        "weighted_brightness_hz": weighted("spectral_centroid_hz"),
        "weighted_spectral_contrast": weighted("spectral_contrast"),
        "weighted_onset_density": weighted("onset_density"),
        "dominant_pitch_class_vote": max(pitch_votes, key=pitch_votes.get),
        "roles": [{"role": x["reference"]["role"], "weight": x["reference"]["weight"], "notes": x["reference"]["notes"]} for x in analyses],
        "preserve_originality": blend.preserve_originality,
        "description": blend.description,
        "references": analyses,
    }
    return dna


def style_prompt(dna: dict) -> str:
    roles = ", ".join(f"{r['role']} influence {r['weight']:.2f}" for r in dna.get("roles", []))
    originality = "Use references only as high-level style/production guidance; compose original musical material." if dna.get("preserve_originality", True) else "Follow the supplied authorized reference direction strongly."
    return (
        f"Multi-reference style DNA: target pulse around {dna.get('weighted_bpm', 120):.1f} BPM; "
        f"brightness centroid around {dna.get('weighted_brightness_hz', 2500):.0f} Hz; "
        f"transient/onset character {dna.get('weighted_onset_density', 0):.2f}; "
        f"reference roles: {roles}. {originality} {dna.get('description','')}"
    ).strip()


def save_style_dna(blend: StyleBlend, destination: Path) -> dict:
    dna = build_style_dna(blend)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(dna, indent=2), encoding="utf-8")
    return dna
