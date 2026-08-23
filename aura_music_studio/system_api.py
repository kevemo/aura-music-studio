from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter

from .aura_avatar import validate_aura_model
from .backup_scheduler import BackupScheduler
from .compute_capabilities import compatibility
from .compute_nodes import ComputeNodeRegistry
from .doctor import system_report
from .model_catalog import public_catalog
from .provider_credentials import credential_report
from .renderer_runtime import renderer_runtime_status
from .speech import AuraSpeechService

router = APIRouter(prefix="/system", tags=["Studio System"])


def _safe_backup_status() -> dict:
    scheduler = BackupScheduler()
    config = scheduler.configuration()
    value: dict = {}
    if scheduler.status_path.is_file():
        try:
            loaded = json.loads(scheduler.status_path.read_text(encoding="utf-8"))
            value = loaded if isinstance(loaded, dict) else {}
        except Exception:
            value = {}
    last = value.get("last_result") if isinstance(value.get("last_result"), dict) else {}
    backup = last.get("backup")
    return {
        "automatic_enabled": config["enabled"],
        "interval_hours": config["interval_hours"],
        "retention_count": config["retention_count"],
        "include_finished_outputs": config["include_outputs"],
        "include_work_files": config["include_work"],
        "last_success_at": value.get("last_success_at"),
        "last_failure_at": value.get("last_failure_at"),
        "last_ok": last.get("ok"),
        "last_archive": Path(str(backup)).name if backup else None,
        "encrypted_when_recipient_configured": config["encrypted_when_recipient_configured"],
        "filesystem_path_exposed": False,
        "encryption_recipient_exposed": False,
    }


def _safe_node_status() -> dict:
    try:
        registry = ComputeNodeRegistry()
        summary = registry.summary()
        nodes = registry.list_nodes(stale_after_seconds=max(60, int(os.getenv("LSS_NODE_HEARTBEAT_SECONDS", "30")) * 4))
        compatible_online = sum(
            1 for node in nodes
            if node.get("online") and node.get("status") != "revoked" and compatibility(node)["compatible"]
        )
        return {
            **summary,
            "compatible_online": compatible_online,
            "same_version_required": os.getenv("LSS_NODE_REQUIRE_SAME_VERSION", "true").lower() == "true",
        }
    except Exception as exc:
        return {"enabled": False, "error": f"{type(exc).__name__}: {exc}"}


def independence_status() -> dict:
    return {
        "self_host_first": True,
        "paid_domain_required": False,
        "cloud_host_required": False,
        "automatic_local_backups": _safe_backup_status(),
        "esp_compute_nodes": _safe_node_status(),
        "compute_node_credentials_exposed": False,
        "compute_node_network_addresses_exposed": False,
    }


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
    report["independence"] = independence_status()
    report["live_renderers"] = renderer_runtime_status()
    report["credentials"] = credential_report()
    report["aura_avatar"] = _avatar_status()
    report["aura_speech"] = _speech_status()
    return report


@router.get("/renderers")
def live_renderers():
    """Member-safe renderer readiness; internal renderer URLs and credentials are never returned."""
    return renderer_runtime_status()


@router.get("/independence")
def independence():
    return independence_status()


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
