from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys

import requests


def _binary(name: str) -> bool:
    return bool(shutil.which(name))


def _module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _gpu() -> dict:
    if not _binary("nvidia-smi"):
        return {"available": False, "details": "nvidia-smi not found in this service"}
    try:
        text = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            text=True,
            timeout=10,
        ).strip()
        return {"available": bool(text), "details": text}
    except Exception as exc:
        return {"available": False, "details": str(exc)}


def _acestep_api_status() -> dict:
    url = (os.getenv("AURA_ACESTEP_API_URL") or "").strip()
    if not url:
        return {"configured": False, "reachable": False, "url": None}
    try:
        from .acestep_api import AceStepClient
        client = AceStepClient(base_url=url)
        return {"configured": True, "reachable": client.health(), "url": client.base_url}
    except Exception as exc:
        return {"configured": True, "reachable": False, "url": url, "error": f"{type(exc).__name__}: {exc}"}


def _ollama_status() -> dict:
    base = (os.getenv("OLLAMA_BASE_URL") or "").rstrip("/")
    if not base:
        return {"configured": False, "reachable": False, "model": os.getenv("AURA_OLLAMA_MODEL") or None}
    try:
        response = requests.get(f"{base}/api/tags", timeout=3)
        response.raise_for_status()
        installed = [item.get("name") for item in response.json().get("models", []) if item.get("name")]
        return {
            "configured": True,
            "reachable": True,
            "url": base,
            "requested_model": os.getenv("AURA_OLLAMA_MODEL", "qwen3:4b"),
            "installed_models": installed,
        }
    except Exception as exc:
        return {
            "configured": True,
            "reachable": False,
            "url": base,
            "requested_model": os.getenv("AURA_OLLAMA_MODEL", "qwen3:4b"),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _web_status() -> dict:
    try:
        from .web_access import AuraWebGateway
        gateway = AuraWebGateway()
        report = gateway.diagnostics()
        if gateway.searxng_url:
            try:
                response = requests.get(
                    f"{gateway.searxng_url}/search",
                    params={"q": "music", "format": "json", "safesearch": 1},
                    timeout=4,
                )
                report["search_backend_reachable"] = response.ok
            except Exception as exc:
                report["search_backend_reachable"] = False
                report["search_backend_error"] = f"{type(exc).__name__}: {exc}"
        else:
            report["search_backend_reachable"] = False
        return report
    except Exception as exc:
        return {"enabled": False, "error": f"{type(exc).__name__}: {exc}"}


def _speech_status() -> dict:
    try:
        from .speech import AuraSpeechService
        return AuraSpeechService().diagnostics()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _queue_status() -> dict:
    try:
        from .jobs import StudioJobQueue
        return StudioJobQueue().summary()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _public_address_status() -> dict:
    try:
        from .public_address import PublicAddressManager
        manager = PublicAddressManager()
        status = manager.read_status()
        return {
            "enabled": True,
            "mode": (os.getenv("LSS_DDNS_PROVIDER") or "none").strip().lower(),
            "configured_hostname": manager.hostname,
            "caddy_site_address": os.getenv("LSS_PUBLIC_SITE_ADDRESS") or "http://:80",
            "upnp_module_available": _module("miniupnpc"),
            "upnp_discovery_enabled": (os.getenv("LSS_UPNP_DISCOVERY", "true").lower() == "true"),
            "automatic_port_forwarding_enabled": (os.getenv("LSS_UPNP_PORT_FORWARD", "false").lower() == "true"),
            "status": status,
            "cloud_host_required": False,
            "cloudflare_required": False,
            "ddns_secret_exposed": False,
        }
    except Exception as exc:
        return {"enabled": False, "error": f"{type(exc).__name__}: {exc}"}


def _production_suite_status(modules: dict, ace_api: dict) -> dict:
    try:
        from .fx_presets import PRESETS as FX_PRESETS
        from .instrument_catalog import CATALOG
        from .mastering import PRESETS as MASTER_PRESETS
        from .separation import VALID_MODES

        instrument_types = sum(len(items) for items in CATALOG.values())
        return {
            "enabled": True,
            "workspace": "/production-suite",
            "build_around_upload": {
                "enabled": True,
                "engine": "ACE-Step 1.5 Complete",
                "renderer_reachable": bool(ace_api.get("reachable")),
                "supports_vocal_anchor": True,
                "supports_instrument_anchor": True,
                "can_add_lead_vocal_to_instrument": True,
            },
            "instrument_selector": {
                "families": len(CATALOG),
                "performance_types": instrument_types,
                "advanced_tier_switches": True,
            },
            "fx": {
                "preset_count": len(FX_PRESETS),
                "stock_waveform_dsp_ready": _binary("ffmpeg"),
                "plugin_host_configured": bool(os.getenv("AURA_PLUGIN_HOST_CMD")),
                "pedalboard_python_available": modules.get("pedalboard", False),
            },
            "aura_tune": {
                "built_in_offline_fallback": True,
                "professional_backend_configured": bool(os.getenv("AURA_AUTOTUNE_CMD")),
                "modes": ["natural", "classic", "hard", "robot", "custom"],
                "automatic_key_detection": True,
            },
            "automix": {
                "enabled": True,
                "editable_non_destructive": True,
                "genre_aware": True,
            },
            "mastering": {
                "preset_count": len(MASTER_PRESETS),
                "reference_mastering": modules.get("matchering", False),
                "album_consistency_mastering": True,
                "manual_eq_width_loudness_controls": True,
            },
            "splitter": {
                "modes": sorted(VALID_MODES),
                "audio_separator_ready": modules.get("audio_separator", False),
                "demucs_ready": modules.get("demucs", False),
                "custom_separator_configured": bool(os.getenv("AURA_SEPARATOR_CMD")),
            },
        }
    except Exception as exc:
        return {"enabled": False, "error": f"{type(exc).__name__}: {exc}"}


def system_report() -> dict:
    gpu = _gpu()
    ace_api = _acestep_api_status()
    ollama = _ollama_status()
    web = _web_status()
    speech = _speech_status()
    queue = _queue_status()
    public_address = _public_address_status()

    api_renderers = {
        "deapi": bool(os.getenv("DEAPI_API_KEY")),
        "eleven_music": bool(os.getenv("ELEVENLABS_API_KEY")),
        "mureka": bool(os.getenv("MUREKA_API_KEY")),
    }
    local_renderers = {
        "acestep_api": ace_api,
        "local_acestep_command": bool(os.getenv("AURA_LOCAL_RENDER_CMD")),
        "muser": bool(os.getenv("AURA_MUSER_CMD")),
        "yue": bool(os.getenv("AURA_YUE_CMD")),
        "diffrhythm": bool(os.getenv("AURA_DIFFRHYTHM_CMD")),
        "audiocraft": bool(os.getenv("AURA_AUDIOCRAFT_CMD")),
        "stable_audio": bool(os.getenv("AURA_STABLE_AUDIO_CMD")),
        "region_renderer": bool(os.getenv("AURA_REGION_RENDER_CMD")),
        "layer_renderer": bool(os.getenv("AURA_LAYER_RENDER_CMD")),
        "sample_renderer": bool(os.getenv("AURA_SAMPLE_RENDER_CMD") or os.getenv("AURA_LAYER_RENDER_CMD")),
    }
    modules = {
        "librosa": _module("librosa"),
        "soundfile": _module("soundfile"),
        "gradio_client": _module("gradio_client"),
        "music21": _module("music21"),
        "basic_pitch": _module("basic_pitch"),
        "demucs": _binary("demucs") or _module("demucs"),
        "matchering": _module("matchering"),
        "pedalboard": _module("pedalboard"),
        "audio_separator": _binary("audio-separator") or _module("audio_separator"),
        "miniupnpc": _module("miniupnpc"),
    }
    production_suite = _production_suite_status(modules, ace_api)
    public_spaces = [
        x.strip()
        for x in os.getenv(
            "AURA_ACESTEP_SPACES",
            "critesjosh/ace-step-music-studio,ACE-Step/Ace-Step-v1.5",
        ).split(",")
        if x.strip()
    ]

    try:
        from .engine_manager import EngineManager
        local_engine_status = EngineManager().status()
    except Exception as exc:
        local_engine_status = [{"error": f"{type(exc).__name__}: {exc}"}]

    command_ready = any(bool(v) for key, v in local_renderers.items() if key != "acestep_api")
    return {
        "python": sys.version.split()[0],
        "gpu": gpu,
        "service_architecture_note": (
            "The web service does not itself require GPU visibility when a private ACE-Step worker is reachable."
        ),
        "binaries": {
            "git": _binary("git"),
            "ffmpeg": _binary("ffmpeg"),
            "ffprobe": _binary("ffprobe"),
            "whisper_cli": _binary("whisper-cli"),
            "fluidsynth_control_preview_only": _binary("fluidsynth"),
            "musescore": any(_binary(x) for x in ("musescore4", "musescore", "mscore")),
        },
        "python_modules": modules,
        "aura_reasoning": {
            "local_ollama": ollama,
            "external_producer_planner_configured": bool(os.getenv("AURA_PRODUCER_LLM_URL")),
            "local_first": True,
        },
        "aura_internet": web,
        "self_hosted_public_address": public_address,
        "spoken_aura": speech,
        "production_queue": queue,
        "production_suite": production_suite,
        "provenance": {
            "enabled": True,
            "hmac_signing_enabled": bool(os.getenv("LSS_PROVENANCE_SECRET")),
        },
        "tenant_security": {
            "per_member_projects": True,
            "server_side_entitlements": True,
            "web_ssrf_protection": True,
        },
        "real_audio_renderers": {
            "native_self_hosted_primary": ace_api,
            "other_local_or_self_hosted": local_renderers,
            "optional_hosted_authenticated": api_renderers,
            "public_acestep_spaces": public_spaces,
            "public_spaces_are_best_effort": True,
        },
        "voice_and_harmony": {
            "diffsinger": bool(os.getenv("AURA_DIFFSINGER_CMD")),
            "seed_vc": bool(os.getenv("AURA_SEEDVC_CMD")),
            "rvc": bool(os.getenv("AURA_RVC_CMD")),
            "consent_gate_enabled": True,
        },
        "separation": {
            "custom": bool(os.getenv("AURA_SEPARATOR_CMD")),
            "audio_separator_model": os.getenv("AURA_SEPARATOR_MODEL") or None,
            "audio_separator_installed": modules["audio_separator"],
            "demucs_installed": modules["demucs"],
            "eleven_stems_available": api_renderers["eleven_music"],
        },
        "local_engines": local_engine_status,
        "ready_for_self_hosted_neural_render": bool(ace_api.get("reachable") or (gpu["available"] and command_ready)),
        "ready_for_authenticated_hosted_neural_render": any(api_renderers.values()),
        "public_hosted_fallback_present": bool(public_spaces),
        "final_audio_policy": {
            "require_real_audio": True,
            "symbolic_or_soundfont_final_allowed": False,
        },
    }
