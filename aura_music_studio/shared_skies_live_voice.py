from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .esp_niche import require_esp_hub_member
from .rhiannon_voice_contracts import VoiceAssetProvenance, public_voice_contract
from .shared_sky_control_room import (
    PROGRAMME_SAFE_PRIVACY,
    StudioConflict,
    StudioInvariantError,
    StudioTransportError,
    normalize_audio,
    studio,
    studio_repo,
    validate_no_secrets,
)
from .speech import AuraSpeechService

router = APIRouter(tags=["Shared Skies LIVE Voice"])

_AUDIO_BEARING_SOURCE_TYPES = {
    "microphone",
    "audio",
    "desktop_audio",
    "application_audio",
    "video",
    "capture_card",
    "remote_guest",
    "remote_mobile_camera",
    "ndi",
    "srt",
    "game_forge",
    "scene_forge",
    "aura_generated",  # legacy internal source identifier; presentation is Rhiannon-facing.
}

_SOURCE_KIND = {
    "microphone": "microphone",
    "audio": "media_file_or_audio_source",
    "desktop_audio": "system_audio",
    "application_audio": "application_audio",
    "video": "media_source_audio",
    "capture_card": "capture_card_audio",
    "remote_guest": "remote_guest_audio",
    "remote_mobile_camera": "remote_guest_audio",
    "ndi": "remote_media_audio",
    "srt": "remote_media_audio",
    "game_forge": "game_forge_audio",
    "scene_forge": "creative_studio_audio",
    "aura_generated": "generated_voice_or_media",
}

_ALLOWED_PROCESSOR_MODES = {"speech", "singing", "voice_conversion", "tts", "rhiannon_speech"}


class EmergencyVoiceMuteRequest(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=300)


def _short(value: Any, maximum: int = 160) -> str:
    return str(value or "").strip()[:maximum]


def voice_runtime_truth(diagnostics: dict[str, Any] | None = None) -> dict[str, Any]:
    """Project canonical voice capability truth into LIVE-specific readiness.

    Chat 5 never upgrades file/batch speech processing into a real-time claim. The Chat 1
    capability contract remains authoritative for Rhiannon capability presentation, while
    Chat 2 remains authoritative for cloning/conversion/profile runtimes.
    """

    diagnostics = dict(diagnostics or {})
    contract = public_voice_contract(diagnostics)
    capabilities = {
        str(item.get("capability") or ""): dict(item)
        for item in contract.get("capabilities", [])
        if isinstance(item, dict)
    }
    synthesis = capabilities.get("speech_synthesis", {"state": "unavailable"})
    conversion = capabilities.get("voice_conversion", {"state": "unavailable", "streaming": False})
    streaming = capabilities.get("streaming_speech", {"state": "unavailable", "streaming": False})
    stt_configured = bool(
        diagnostics.get("stt_command_configured")
        or diagnostics.get("whisper_model_configured")
    )

    return {
        "rhiannon_contract": {
            "capability_protocol": contract.get("capability_protocol"),
            "timing_protocol": contract.get("timing_protocol"),
            "asset_provenance_protocol": contract.get("asset_provenance_protocol"),
            "identity": contract.get("identity", {}).get("identity", "rhiannon"),
        },
        "speech_generation_runtime": {
            "state": str(synthesis.get("state") or "unavailable"),
            "streaming": bool(synthesis.get("streaming", False)),
            "local_runtime_configured": bool(synthesis.get("local_runtime_configured", False)),
            "remote_provider_configured": bool(synthesis.get("remote_provider_configured", False)),
            "health_proven": str(synthesis.get("state") or "") == "available",
        },
        "real_time_voice_conversion": {
            "state": str(conversion.get("state") or "unavailable"),
            "real_time": bool(conversion.get("streaming", False))
            and str(conversion.get("state") or "") == "available",
            "authority": "Chat 2 voice conversion runtime",
        },
        "streaming_speech": {
            "state": str(streaming.get("state") or "unavailable"),
            "real_time": bool(streaming.get("streaming", False))
            and str(streaming.get("state") or "") == "available",
        },
        "transcription_runtime": {
            "state": "offline_only" if stt_configured else "unavailable",
            "real_time": False,
            "detail": (
                "A file/batch STT path is configured; this does not satisfy LIVE captions."
                if stt_configured
                else "No transcription runtime is configured."
            ),
        },
        "translation_runtime": {
            "state": "unavailable",
            "real_time": False,
            "detail": "No executable real-time translation adapter is attached to Shared Skies LIVE.",
        },
        "live_tts_queue": {
            "state": "unavailable",
            "detail": "Speech generation is not a LIVE queue/playback contract; a bounded queue adapter is still required.",
        },
        "authorised_voice_profile_selection": {
            "state": "integration_required",
            "authority": "Chat 2 consent-gated Voice House / voice profile service",
            "detail": (
                "Tenant-scoped Chat 2 Voice Profiles can be discovered as LIVE selection candidates, "
                "but Shared Skies still has no server-authoritative processor binding; discovery does "
                "not prove executable or real-time voice processing."
            ),
        },
        "private_spoken_auto_cue": {
            "state": "unavailable",
            "text_auto_cue": "available",
            "detail": "Private text Auto Cue exists; no proven private audio monitor route is attached yet.",
        },
    }


def _provenance_projection(config: dict[str, Any]) -> dict[str, Any] | None:
    raw = config.get("voice_asset_provenance")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return {"present": True, "valid": False}
    try:
        provenance = VoiceAssetProvenance.model_validate(raw)
    except Exception:
        return {"present": True, "valid": False}
    return {
        "present": True,
        "valid": True,
        "generation_type": provenance.generation_type,
        "voice_profile_ref": provenance.voice_profile_ref,
        "voice_profile_kind": provenance.voice_profile_kind.value,
        "consent_state": provenance.consent_state.value,
        "provider_runtime_class": provenance.provider_runtime_class,
        "synthetic_generated": provenance.synthetic_generated,
        "private": provenance.private,
        "entitlement_ref_present": bool(provenance.entitlement_ref),
        "source_project_id": provenance.source_project_id,
        "revision": provenance.revision,
    }


def _processor_projection(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("voice_processing")
    if not isinstance(raw, dict):
        return {
            "declared": False,
            "runtime_proven": False,
            "real_time_proven": False,
            "bypass_available": False,
        }
    mode = _short(raw.get("mode"), 40).lower()
    return {
        "declared": True,
        "mode": mode if mode in _ALLOWED_PROCESSOR_MODES else "unknown",
        "voice_profile_ref": _short(raw.get("voice_profile_ref"), 120) or None,
        "enabled_requested": bool(raw.get("enabled", False)),
        "bypass_requested": bool(raw.get("bypass", False)),
        "runtime_proven": False,
        "real_time_proven": False,
        "bypass_available": False,
        "detail": "Source metadata is not runtime health evidence; Chat 2 must provide the processor contract.",
    }


def project_voice_source(source: dict[str, Any], bus: Literal["preview", "programme"]) -> dict[str, Any] | None:
    """Return a redacted voice/audio source projection for operators.

    The raw source configuration is intentionally never returned because it can contain device,
    provider or project-specific details. Programme projection also enforces the existing privacy
    invariant instead of allowing a private cue to be presented as viewer audio.
    """

    source_type = _short(source.get("source_type"), 80)
    if source_type not in _AUDIO_BEARING_SOURCE_TYPES:
        return None
    config = source.get("config") or {}
    if not isinstance(config, dict):
        raise StudioInvariantError("Voice/audio source configuration must be an object")
    validate_no_secrets(config)
    privacy = _short(config.get("privacy") or "programme_safe", 40).lower()
    private = privacy not in PROGRAMME_SAFE_PRIVACY or bool(config.get("backstage_only", False))
    visible = bool(source.get("visible", True))
    audio = normalize_audio(config.get("audio") if isinstance(config.get("audio"), dict) else {})
    if bus == "programme" and visible and private:
        raise StudioInvariantError("A private/backstage voice source cannot be active on Programme")
    provenance = _provenance_projection(config)
    indicator = None
    if provenance and provenance.get("valid"):
        indicator = {
            "speech_synthesis": "synthetic_tts_or_generated_speech",
            "cloned_speech": "authorised_cloned_voice",
            "singing_voice": "authorised_synthetic_or_cloned_singing_voice",
            "voice_conversion": "ai_converted_voice",
        }.get(str(provenance.get("generation_type") or ""))
    return {
        "id": _short(source.get("id"), 128),
        "name": _short(source.get("name") or "Audio source", 120),
        "source_type": source_type,
        "source_kind": _SOURCE_KIND.get(source_type, "audio_source"),
        "bus": bus,
        "visible": visible,
        "private_or_backstage": private,
        "programme_output_active": bus == "programme" and visible and not private and not bool(audio["muted"]),
        "audio": audio,
        "voice_processing": _processor_projection(config),
        "voice_asset_provenance": provenance,
        "voice_identity_indicator": indicator,
        "raw_config_exposed": False,
        "capture_or_processor_health_proven": False,
    }


def _project_sources(rows: Any, bus: Literal["preview", "programme"]) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    projected: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = project_voice_source(row, bus)
        if item is not None:
            projected.append(item)
    return projected


def build_emergency_voice_mute_snapshot(
    programme_snapshot: dict[str, Any], source_id: str
) -> tuple[dict[str, Any], bool]:
    """Mute exactly one Programme voice/audio source without depending on an AI processor.

    This is intentionally narrower than a claimed voice-conversion bypass. It uses the canonical
    Programme mixer state, so an unavailable cloning/TTS/conversion service is not required for the
    safety action itself. Processor bypass remains unavailable until Chat 2 exposes that executable
    runtime contract.
    """

    clean_source_id = _short(source_id, 128)
    if not clean_source_id:
        raise StudioInvariantError("A Programme voice/audio source ID is required")
    if not isinstance(programme_snapshot, dict) or not programme_snapshot.get("scene"):
        raise StudioInvariantError("No committed Programme snapshot is available")
    snapshot = deepcopy(programme_snapshot)
    sources = snapshot.get("sources")
    if not isinstance(sources, list):
        raise StudioInvariantError("Committed Programme snapshot has no source list")
    matched = False
    changed = False
    for source in sources:
        if not isinstance(source, dict) or _short(source.get("id"), 128) != clean_source_id:
            continue
        matched = True
        source_type = _short(source.get("source_type"), 80)
        if source_type not in _AUDIO_BEARING_SOURCE_TYPES:
            raise StudioInvariantError("Programme source is not an audio/voice source")
        config = source.get("config") or {}
        if not isinstance(config, dict):
            raise StudioInvariantError("Voice/audio source configuration must be an object")
        validate_no_secrets(config)
        audio = normalize_audio(config.get("audio") if isinstance(config.get("audio"), dict) else {})
        changed = not bool(audio.get("muted"))
        audio["muted"] = True
        config = dict(config)
        config["audio"] = audio
        source["config"] = config
        break
    if not matched:
        raise StudioInvariantError("Programme voice/audio source is not present in the committed snapshot")
    validate_no_secrets(snapshot)
    return snapshot, changed


def voice_readiness_projection(
    user_id: str,
    session_id: str,
    *,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = studio_repo.get_session(user_id, session_id)
    preview_sources: list[dict[str, Any]] = []
    if current.get("preview_scene_id"):
        preview_scene = studio.graph.scene(user_id, str(current["preview_scene_id"]))
        preview_sources = _project_sources(preview_scene.get("sources", []), "preview")
    programme_snapshot = current.get("programme_snapshot") or {}
    programme_sources = _project_sources(programme_snapshot.get("sources", []), "programme")
    transport = studio.transport.status(user_id, current.get("broadcast_id"))
    recording = studio.transport.recording_capabilities(user_id, current.get("broadcast_id"))
    safe_diagnostics = diagnostics if diagnostics is not None else AuraSpeechService().diagnostics()
    return {
        "product": "Shared Skies Streaming Studios LIVE Voice",
        "session_id": session_id,
        "broadcast_id": current.get("broadcast_id"),
        "session_version": current.get("version"),
        "runtime": voice_runtime_truth(safe_diagnostics),
        "preview_voice_sources": preview_sources,
        "programme_voice_sources": programme_sources,
        "control_room": {
            "audio_mixer_contract": "available",
            "mixer_controls": ["gain", "mute", "pan", "monitor", "high_pass", "compressor", "limiter"],
            "programme_commit_supported": bool(transport.get("programme_commit_supported", False)),
            "transport_state": transport.get("state"),
            "capture_runtime_health_proven": False,
            "processor_runtime_health_proven": False,
        },
        "recording": recording,
        "emergency_controls": {
            "programme_voice_mute": "available_via_authoritative_programme_commit",
            "voice_processor_bypass": "unavailable_until_chat2_runtime_contract",
            "tts_clear": "unavailable_until_live_tts_queue_exists",
        },
        "captions": {
            "real_time_available": False,
            "reason": "Current STT integration is file/batch oriented; no executable streaming caption pipeline is attached.",
        },
        "translation": {
            "real_time_available": False,
            "reason": "No executable streaming translation pipeline is attached.",
        },
        "consent_boundaries": {
            "microphone_transmission_implies_recording_consent": False,
            "live_recording_implies_clone_consent": False,
            "joining_live_implies_clone_consent": False,
            "public_clone_use_requires_separate_authorisation": True,
            "commercial_clone_use_requires_separate_authorisation": True,
        },
        "authority": {
            "rhiannon_identity": "Chat 1",
            "voice_profiles_cloning_conversion": "Chat 2",
            "live_routing_programme_preview": "Chat 5",
            "payments_coins_gifts_entitlements": "Chat 6",
            "deployment_workers_security": "Chat 7",
            "voice_profile_database_owned_by_chat5": False,
            "client_entitlement_authority": False,
            "battle_or_economy_mutation_from_voice": False,
        },
        "load_test_status": "not_evidenced",
    }


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(404, "Shared Skies Studio session or source was not found")
    if isinstance(exc, StudioConflict):
        return HTTPException(409, "Studio state changed in another operator session")
    if isinstance(exc, StudioInvariantError):
        return HTTPException(409, str(exc))
    if isinstance(exc, StudioTransportError):
        return HTTPException(409, "Programme voice safety update was rejected by authoritative transport")
    return HTTPException(400, "Shared Skies LIVE voice operation could not be completed")


@router.get("/shared-sky/studio/api/sessions/{session_id}/voice/readiness")
def live_voice_readiness(session_id: str, request: Request):
    member, _ = require_esp_hub_member(request)
    try:
        return voice_readiness_projection(str(member.user_id), session_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/shared-sky/studio/api/sessions/{session_id}/voice/emergency/mute/{source_id}")
def emergency_mute_programme_voice(
    session_id: str,
    source_id: str,
    body: EmergencyVoiceMuteRequest,
    request: Request,
):
    member, _ = require_esp_hub_member(request)
    user_id = str(member.user_id)
    try:
        current = studio_repo.get_session(user_id, session_id)
        if int(current.get("version") or 0) != body.expected_version:
            raise StudioConflict("Studio state changed in another operator session")
        snapshot, changed = build_emergency_voice_mute_snapshot(
            current.get("programme_snapshot") or {}, source_id
        )
        if not changed:
            return {
                **studio.hydrate(user_id, current),
                "emergency_voice": {
                    "action": "mute_programme_voice_source",
                    "source_id": source_id,
                    "already_muted": True,
                    "authoritative_programme_commit": False,
                    "processor_bypass_claimed": False,
                },
            }
        correlation_id = f"voice-mute-{uuid4().hex}"
        commit = studio.transport.commit_programme(
            user_id,
            current.get("broadcast_id"),
            snapshot,
            correlation_id,
        )
        if not commit.accepted:
            raise StudioTransportError("Authoritative transport rejected emergency voice mute")
        updated = studio_repo.complete_programme(
            user_id,
            session_id,
            body.expected_version,
            snapshot,
            commit.__dict__,
        )
        studio.graph.event(
            user_id,
            current.get("broadcast_id"),
            "studio_emergency_voice_muted",
            {
                "session_id": session_id,
                "source_id": source_id,
                "reason": body.reason.strip(),
                "correlation_id": correlation_id,
            },
        )
        return {
            **studio.hydrate(user_id, updated),
            "emergency_voice": {
                "action": "mute_programme_voice_source",
                "source_id": source_id,
                "already_muted": False,
                "authoritative_programme_commit": bool(commit.authoritative),
                "correlation_id": correlation_id,
                "transport_stopped": False,
                "processor_bypass_claimed": False,
            },
        }
    except Exception as exc:
        raise _http_error(exc) from exc


def install_shared_skies_live_voice(app: Any) -> None:
    existing = {
        (
            str(getattr(route, "path", "")),
            tuple(sorted(str(method).upper() for method in (getattr(route, "methods", set()) or set()))),
        )
        for route in app.router.routes
    }
    for route in tuple(router.routes):
        signature = (
            str(getattr(route, "path", "")),
            tuple(sorted(str(method).upper() for method in (getattr(route, "methods", set()) or set()))),
        )
        if signature in existing:
            continue
        app.router.routes.append(route)
        existing.add(signature)


__all__ = [
    "EmergencyVoiceMuteRequest",
    "build_emergency_voice_mute_snapshot",
    "emergency_mute_programme_voice",
    "install_shared_skies_live_voice",
    "live_voice_readiness",
    "project_voice_source",
    "router",
    "voice_readiness_projection",
    "voice_runtime_truth",
]
