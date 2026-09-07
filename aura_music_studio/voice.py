from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import librosa
import numpy as np

from .cloud_providers import MurekaClient
from .request_context import current_user_id
from .rights import RightsLedger, VoiceProfile, authorize_voice_profile

_MIN_REFERENCE_SECONDS = 1.0
_MAX_REFERENCE_SECONDS = 15 * 60.0
_MIN_REFERENCE_SAMPLE_RATE = 16000
_MIN_REFERENCE_RMS = 1e-4
_MIN_VOICED_RATIO = 0.02


def analyze_voice_sample(path: Path) -> dict:
    """Decode and quality-gate a voice reference before it can back an identity profile."""
    try:
        y, sr = librosa.load(path, sr=None, mono=True)
    except Exception as exc:
        raise ValueError(f"Voice reference could not be decoded: {path.name}") from exc
    if y.size == 0 or not np.all(np.isfinite(y)):
        raise ValueError("Voice reference is empty or contains invalid samples")

    duration = float(librosa.get_duration(y=y, sr=sr))
    if duration < _MIN_REFERENCE_SECONDS:
        raise ValueError(f"Voice reference must be at least {_MIN_REFERENCE_SECONDS:g} second long")
    if duration > _MAX_REFERENCE_SECONDS:
        raise ValueError("Voice reference exceeds the 15 minute per-file duration limit")
    if int(sr) < _MIN_REFERENCE_SAMPLE_RATE:
        raise ValueError("Voice reference sample rate must be at least 16 kHz")

    f0, voiced_flag, voiced_prob = librosa.pyin(
        y,
        fmin=librosa.note_to_hz("C2"),
        fmax=librosa.note_to_hz("C7"),
        sr=sr,
    )
    voiced = f0[np.isfinite(f0)] if f0 is not None else np.array([])
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    rms_frames = librosa.feature.rms(y=y)[0]
    rms = float(np.mean(rms_frames))
    voiced_ratio = float(np.mean(voiced_flag)) if voiced_flag is not None else 0.0
    if rms < _MIN_REFERENCE_RMS:
        raise ValueError("Voice reference is effectively silent; record a clearer vocal sample")
    if voiced_ratio < _MIN_VOICED_RATIO:
        raise ValueError("Voice reference contains insufficient detectable voiced material")

    peak = float(np.max(np.abs(y)))
    return {
        "duration_seconds": duration,
        "sample_rate": int(sr),
        "median_f0_hz": float(np.median(voiced)) if voiced.size else None,
        "low_f0_hz": float(np.percentile(voiced, 10)) if voiced.size else None,
        "high_f0_hz": float(np.percentile(voiced, 90)) if voiced.size else None,
        "voiced_ratio": voiced_ratio,
        "median_voiced_probability": (
            float(np.nanmedian(voiced_prob)) if voiced_prob is not None and np.any(np.isfinite(voiced_prob)) else None
        ),
        "median_spectral_centroid_hz": float(np.median(centroid)),
        "rms": rms,
        "peak": peak,
        "quality_state": "accepted",
        "quality_warnings": (["near_clipping"] if peak >= 0.995 else []),
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
    """Compatibility-only import path for older UI/API callers.

    Ordinary uploads must never become immediately usable identity-replicating profiles. This
    helper therefore stores a *locked candidate* only. The production Voice House challenge route
    records the explicit verification recording and creates the authorised reusable profile.
    """
    if not reference_files:
        raise ValueError("At least one voice reference file is required")
    analysis = {str(p): analyze_voice_sample(p) for p in reference_files}
    user_id = current_user_id()
    profile = VoiceProfile(
        name=name,
        owner_label=owner_label,
        reference_files=[str(p) for p in reference_files],
        consent_confirmed=False,
        consent_statement=(consent_statement or "").strip(),
        verification_state="unverified",
        verification_method="legacy_upload_locked_pending_voice_house_challenge",
        allowed_uses=allowed_uses or ["singing", "backing_harmony", "voice_conversion"],
        created_by_user_id=user_id,
        tenant_user_id=user_id,
        subject_relationship="legacy_unspecified",
        metadata={
            "voice_scan": analysis,
            "identity_profile_locked": True,
            "requires_voice_house_challenge": True,
            "legacy_creation_path": True,
        },
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
