from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys


def _binary(name: str) -> bool:
    return bool(shutil.which(name))


def _module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _gpu() -> dict:
    if not _binary("nvidia-smi"):
        return {"available": False, "details": "nvidia-smi not found"}
    try:
        text = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            text=True,
            timeout=10,
        ).strip()
        return {"available": bool(text), "details": text}
    except Exception as exc:
        return {"available": False, "details": str(exc)}


def system_report() -> dict:
    gpu = _gpu()
    api_renderers = {
        "deapi": bool(os.getenv("DEAPI_API_KEY")),
        "eleven_music": bool(os.getenv("ELEVENLABS_API_KEY")),
        "mureka": bool(os.getenv("MUREKA_API_KEY")),
    }
    local_renderers = {
        "local_acestep": bool(os.getenv("AURA_LOCAL_RENDER_CMD")),
        "muser": bool(os.getenv("AURA_MUSER_CMD")),
        "yue": bool(os.getenv("AURA_YUE_CMD")),
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
        "audio_separator": _binary("audio-separator") or _module("audio_separator"),
    }
    public_spaces = [x.strip() for x in os.getenv(
        "AURA_ACESTEP_SPACES",
        "critesjosh/ace-step-music-studio,ACE-Step/Ace-Step-v1.5",
    ).split(",") if x.strip()]

    try:
        from .engine_manager import EngineManager
        local_engine_status = EngineManager().status()
    except Exception as exc:
        local_engine_status = [{"error": f"{type(exc).__name__}: {exc}"}]

    return {
        "python": sys.version.split()[0],
        "gpu": gpu,
        "binaries": {
            "git": _binary("git"),
            "ffmpeg": _binary("ffmpeg"),
            "ffprobe": _binary("ffprobe"),
            "fluidsynth_control_preview_only": _binary("fluidsynth"),
            "musescore": any(_binary(x) for x in ("musescore4", "musescore", "mscore")),
        },
        "python_modules": modules,
        "real_audio_renderers": {
            "hosted_authenticated": api_renderers,
            "local_or_self_hosted": local_renderers,
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
        "ready_for_local_gpu_neural_render": bool(gpu["available"] and any(local_renderers.values())),
        "ready_for_authenticated_hosted_neural_render": any(api_renderers.values()),
        "public_hosted_fallback_present": bool(public_spaces),
        "final_audio_policy": {
            "require_real_audio": True,
            "symbolic_or_soundfont_final_allowed": False,
        },
    }
