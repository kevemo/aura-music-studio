from __future__ import annotations

from fastapi import APIRouter

from .aura_avatar import validate_aura_model
from .doctor import system_report
from .model_catalog import public_catalog
from .provider_credentials import credential_report
from .speech import AuraSpeechService

router = APIRouter(prefix="/system", tags=["Studio System"])


def _avatar_status() -> dict:
    raw = validate_aura_model()
    # Deployment paths are implementation details. Surface only readiness/coverage data.
    return {
        "canonical_identity": raw.get("identity_contract", {}).get("identity_version", "auracore-osiris-canonical-v1"),
        "installed": bool(raw.get("exists")),
        "valid_glb": bool(raw.get("valid_glb")),
        "vrm_1": bool(raw.get("vrm_1")),
        "ready_for_embodied_runtime": bool(raw.get("ready_for_embodied_runtime")),
        "missing_humanoid_bones": raw.get("missing_humanoid_bones") or [],
        "expression_coverage": raw.get("recommended_expression_coverage", 0.0),
        "animations": raw.get("animations") or [],
        "missing_recommended_animations": raw.get("missing_recommended_animations") or [],
        "blocking_reason": raw.get("blocking_reason"),
        "generic_avatar_fallback_allowed": False,
        "manual_likeness_review_required": True,
    }


def _speech_status() -> dict:
    raw = AuraSpeechService().diagnostics()
    return {
        "stt_configured": bool(raw.get("stt_command_configured") or raw.get("whisper_model_configured")),
        "tts_configured": bool(
            raw.get("tts_command_configured")
            or raw.get("tts_url_configured")
            or raw.get("piper_model_configured")
        ),
        "locale_aware_tts": bool(raw.get("locale_aware_tts")),
        "browser_audio_transcoding": bool(raw.get("browser_audio_transcoding")),
        "offline_first": bool(raw.get("offline_first")),
        "voice_profile": raw.get("voice_profile") or {},
    }


@router.get("/doctor")
def doctor():
    report = system_report()
    report["credentials"] = credential_report()
    report["aura_avatar"] = _avatar_status()
    report["aura_speech"] = _speech_status()
    return report


@router.get("/credential-status")
def credential_status():
    """Return configuration state only; never return any secret value or fingerprint."""
    return credential_report()


@router.get("/aura-status")
def aura_status():
    """Redacted deployment readiness for Aura's embodied and spoken interfaces."""
    return {
        "avatar": _avatar_status(),
        "speech": _speech_status(),
    }


@router.get("/model-catalog")
def model_catalog():
    return {
        "policy": (
            "ESP Live Sound Studio never assumes that an open repository or downloadable checkpoint is "
            "commercially unrestricted. Code and model/voice licences are tracked separately."
        ),
        "components": public_catalog(),
    }
