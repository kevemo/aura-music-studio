from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import librosa
import numpy as np

from .cloud_providers import MurekaClient
from .rights import RightsLedger, VoiceProfile, authorize_voice_profile


def analyze_voice_sample(path: Path) -> dict:
    y, sr = librosa.load(path, sr=None, mono=True)
    duration = float(librosa.get_duration(y=y, sr=sr))
    f0, voiced_flag, voiced_prob = librosa.pyin(
        y,
        fmin=librosa.note_to_hz("C2"),
        fmax=librosa.note_to_hz("C7"),
        sr=sr,
    )
    voiced = f0[np.isfinite(f0)] if f0 is not None else np.array([])
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    rms = librosa.feature.rms(y=y)[0]
    return {
        "duration_seconds": duration,
        "sample_rate": int(sr),
        "median_f0_hz": float(np.median(voiced)) if voiced.size else None,
        "low_f0_hz": float(np.percentile(voiced, 10)) if voiced.size else None,
        "high_f0_hz": float(np.percentile(voiced, 90)) if voiced.size else None,
        "voiced_ratio": float(np.mean(voiced_flag)) if voiced_flag is not None else None,
        "median_spectral_centroid_hz": float(np.median(centroid)),
        "rms": float(np.mean(rms)),
    }


def create_voice_profile(
    ledger: RightsLedger,
    *,
    name: str,
    owner_label: str,
    reference_files: list[Path],
    consent_statement: str,
    allowed_uses: list[str] | None = None,
) -> VoiceProfile:
    if not reference_files:
        raise ValueError("At least one voice reference file is required")
    analysis = {str(p): analyze_voice_sample(p) for p in reference_files}
    profile = VoiceProfile(
        name=name,
        owner_label=owner_label,
        reference_files=[str(p) for p in reference_files],
        consent_confirmed=True,
        consent_statement=consent_statement,
        allowed_uses=allowed_uses or ["singing", "backing_harmony", "voice_conversion"],
        metadata={"voice_scan": analysis},
    )
    return ledger.save_voice(profile)


def convert_singing_voice(
    source_vocal: Path,
    output: Path,
    *,
    rights_root: Path,
    voice_profile_id: str,
    similarity: float = 0.8,
    pitch_shift: int = 0,
) -> Path:
    """Run singing conversion only after a fresh authoritative consent lookup."""
    profile = authorize_voice_profile(rights_root, voice_profile_id, "voice_conversion")
    if not profile.reference_files:
        raise RuntimeError("Voice Profile has no reference audio")
    target = Path(profile.reference_files[0])
    if not target.exists():
        raise FileNotFoundError(target)

    # Prefer Seed-VC for zero-shot singing conversion, then RVC/Applio.
    command = os.getenv("AURA_SEEDVC_CMD") or os.getenv("AURA_RVC_CMD")
    if not command:
        raise RuntimeError("Configure AURA_SEEDVC_CMD or AURA_RVC_CMD to enable local singing voice conversion")
    output.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "AURA_VOICE_SOURCE": str(source_vocal),
        "AURA_VOICE_REFERENCE": str(target),
        "AURA_VOICE_OUTPUT": str(output),
        "AURA_VOICE_SIMILARITY": str(max(0.0, min(similarity, profile.similarity_limit))),
        "AURA_VOICE_PITCH_SHIFT": str(pitch_shift),
        "AURA_VOICE_PROFILE": profile.model_dump_json(),
    })
    subprocess.run(shlex.split(command), env=env, check=True)
    if not output.exists():
        raise RuntimeError(f"Voice conversion command did not create {output}")
    return output


def create_mureka_vocal_id(*, rights_root: Path, voice_profile_id: str) -> str:
    """Create a cloud vocal ID only after a fresh authoritative consent check."""
    profile = authorize_voice_profile(rights_root, voice_profile_id, "singing")
    if not profile.reference_files:
        raise RuntimeError("Voice Profile has no reference audio")
    source = Path(profile.reference_files[0])
    if not source.exists():
        raise FileNotFoundError(source)
    client = MurekaClient()
    return client.clone_vocal(source, f"Aura Voice Profile: {profile.name}; owner: {profile.owner_label}")
