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
    return {
        "python": sys.version.split()[0],
        "gpu": _gpu(),
        "binaries": {
            "ffmpeg": _binary("ffmpeg"),
            "ffprobe": _binary("ffprobe"),
            "fluidsynth": _binary("fluidsynth"),
            "demucs": _binary("demucs") or _module("demucs"),
        },
        "python_modules": {
            "librosa": _module("librosa"),
            "soundfile": _module("soundfile"),
            "gradio_client": _module("gradio_client"),
            "music21": _module("music21"),
        },
        "renderers": {
            "deapi": bool(os.getenv("DEAPI_API_KEY")),
            "acestep_space": True,
            "local_acestep": bool(os.getenv("AURA_LOCAL_RENDER_CMD")),
            "muser": bool(os.getenv("AURA_MUSER_CMD")),
            "yue": bool(os.getenv("AURA_YUE_CMD")),
        },
        "postproduction": {
            "diffsinger": bool(os.getenv("AURA_DIFFSINGER_CMD")),
            "rvc": bool(os.getenv("AURA_RVC_CMD")),
            "guitar_layer": bool(os.getenv("AURA_GUITAR_LAYER_CMD")),
        },
        "ready_for_local_gpu_neural_render": bool(_gpu()["available"] and os.getenv("AURA_LOCAL_RENDER_CMD")),
        "ready_for_hosted_neural_render": bool(os.getenv("DEAPI_API_KEY") or True),
    }
