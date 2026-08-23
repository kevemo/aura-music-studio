from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


@dataclass(frozen=True)
class AudioProbe:
    path: str
    valid: bool
    duration_seconds: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    error: str | None = None


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _command_ready(name: str) -> bool:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return False
    try:
        parts = shlex.split(raw)
    except ValueError:
        return False
    if not parts:
        return False
    executable = parts[0]
    return bool(shutil.which(executable) or Path(executable).is_file())


def probe_real_audio(path: str | Path, *, minimum_seconds: float = 1.0) -> AudioProbe:
    """Verify that a renderer result is a decodable waveform, not a symbolic/control file."""
    p = Path(path)
    if not p.is_file():
        return AudioProbe(str(p), False, error="file does not exist")
    if p.suffix.lower() in {".mid", ".midi", ".musicxml", ".xml", ".mscz"}:
        return AudioProbe(str(p), False, error="symbolic/control formats cannot be Final Master audio")
    try:
        import soundfile as sf

        info = sf.info(p)
        duration = float(info.frames / info.samplerate) if info.samplerate else 0.0
        valid = bool(info.frames > 0 and info.samplerate > 0 and info.channels > 0 and duration >= minimum_seconds)
        return AudioProbe(
            str(p), valid, duration_seconds=duration, sample_rate=int(info.samplerate), channels=int(info.channels),
            error=None if valid else f"decoded audio shorter than {minimum_seconds:.2f}s",
        )
    except Exception as sf_exc:
        if not shutil.which("ffprobe"):
            return AudioProbe(str(p), False, error=f"soundfile decode failed and ffprobe unavailable: {sf_exc}")
        try:
            import json

            raw = subprocess.check_output(
                [
                    "ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-show_entries", "stream=sample_rate,channels,codec_type", "-of", "json", str(p),
                ],
                text=True,
                timeout=20,
            )
            data = json.loads(raw)
            streams = [x for x in data.get("streams", []) if x.get("codec_type") == "audio"]
            if not streams:
                return AudioProbe(str(p), False, error="ffprobe found no audio stream")
            duration = float((data.get("format") or {}).get("duration") or 0.0)
            sample_rate = int(float(streams[0].get("sample_rate") or 0))
            channels = int(streams[0].get("channels") or 0)
            valid = duration >= minimum_seconds and sample_rate > 0 and channels > 0
            return AudioProbe(
                str(p), valid, duration_seconds=duration, sample_rate=sample_rate, channels=channels,
                error=None if valid else f"decoded audio shorter than {minimum_seconds:.2f}s",
            )
        except Exception as ff_exc:
            return AudioProbe(str(p), False, error=f"audio decode failed: {type(ff_exc).__name__}: {ff_exc}")


def _ace_step_status() -> dict[str, Any]:
    url = (os.getenv("AURA_ACESTEP_API_URL") or "").strip()
    if not url:
        return {"id": "ace-step", "configured": False, "reachable": False, "primary": True}
    try:
        from .acestep_api import AceStepClient

        client = AceStepClient(base_url=url)
        reachable = client.health()
        models: list[str] = []
        if reachable:
            try:
                response = client.session.get(client.base_url + "v1/models", timeout=10)
                response.raise_for_status()
                payload = response.json()
                rows = payload.get("data") if isinstance(payload, dict) else payload
                if isinstance(rows, dict):
                    rows = rows.get("models") or rows.get("items") or []
                if isinstance(rows, list):
                    for item in rows:
                        if isinstance(item, str):
                            models.append(item)
                        elif isinstance(item, dict):
                            model_name = item.get("id") or item.get("name") or item.get("model")
                            if model_name:
                                models.append(str(model_name))
            except Exception:
                models = []
        return {
            "id": "ace-step",
            "configured": True,
            "reachable": reachable,
            "primary": True,
            "full_song_model": os.getenv("AURA_ACESTEP_FULL_MODEL", "acestep-v15-xl-turbo"),
            "track_model": os.getenv("AURA_ACESTEP_TRACK_MODEL", "acestep-v15-base"),
            "models_reported": sorted(set(models))[:40],
            "supports": ["text2music", "cover", "repaint", "complete", "lego", "extract"],
            "internal_url_exposed": False,
        }
    except Exception as exc:
        return {
            "id": "ace-step", "configured": True, "reachable": False, "primary": True,
            "error": f"{type(exc).__name__}: {exc}", "internal_url_exposed": False,
        }


def _yue_status() -> dict[str, Any]:
    url = (os.getenv("AURA_YUE_API_URL") or "").strip().rstrip("/")
    command_configured = _command_ready("AURA_YUE_CMD")
    if not url:
        return {
            "id": "yue", "configured": command_configured, "reachable": False, "primary": False,
            "role": "lyrics-first optional full-song renderer", "internal_url_exposed": False,
        }
    try:
        response = requests.get(f"{url}/health", timeout=5)
        response.raise_for_status()
        payload = response.json() if response.content else {}
        return {
            "id": "yue", "configured": True, "reachable": True, "primary": False,
            "role": "lyrics-first optional full-song renderer",
            "busy": bool(payload.get("busy", False)) if isinstance(payload, dict) else False,
            "max_segments": int(os.getenv("AURA_YUE_MAX_SEGMENTS", "2")),
            "stage1_model": os.getenv("AURA_YUE_STAGE1_MODEL", "m-a-p/YuE-s1-7B-anneal-en-cot"),
            "stage2_model": os.getenv("AURA_YUE_STAGE2_MODEL", "m-a-p/YuE-s2-1B-general"),
            "internal_url_exposed": False,
        }
    except Exception as exc:
        return {
            "id": "yue", "configured": True, "reachable": False, "primary": False,
            "role": "lyrics-first optional full-song renderer",
            "error": f"{type(exc).__name__}: {exc}", "internal_url_exposed": False,
        }


def renderer_runtime_status() -> dict[str, Any]:
    """Member-safe truth about real neural rendering readiness."""
    ace = _ace_step_status()
    yue = _yue_status()
    hosted = {
        "deapi": bool(os.getenv("DEAPI_API_KEY")),
        "eleven_music": bool(os.getenv("ELEVENLABS_API_KEY")),
        "mureka": bool(os.getenv("MUREKA_API_KEY")),
    }
    local_commands = {
        "local_acestep": _command_ready("AURA_LOCAL_RENDER_CMD"),
        "muser": _command_ready("AURA_MUSER_CMD"),
        "diffrhythm": _command_ready("AURA_DIFFRHYTHM_CMD"),
        "audiocraft": _command_ready("AURA_AUDIOCRAFT_CMD"),
        "stable_audio": _command_ready("AURA_STABLE_AUDIO_CMD"),
    }
    self_hosted_ready = bool(ace.get("reachable") or yue.get("reachable") or any(local_commands.values()))
    authenticated_hosted_ready = any(hosted.values())
    primary = "ace-step" if ace.get("reachable") else ("yue" if yue.get("reachable") else None)
    return {
        "real_audio_required": True,
        "symbolic_final_allowed": False,
        "self_hosted_ready": self_hosted_ready,
        "authenticated_hosted_fallback_ready": authenticated_hosted_ready,
        "final_master_renderer_ready": bool(self_hosted_ready or authenticated_hosted_ready),
        "active_primary": primary,
        "engines": [ace, yue],
        "other_local_commands": local_commands,
        "optional_authenticated_hosted": hosted,
        "public_space_fallback_enabled": bool((os.getenv("AURA_ACESTEP_SPACES") or "").strip()),
        # Base/development mode may inspect or edit projects without a GPU. The live GPU overlays
        # force this true so queued Final Master jobs fail immediately if the renderer disappears.
        "fail_closed_without_real_renderer": _bool_env("AURA_REQUIRE_LIVE_RENDERER", False),
    }


def require_live_renderer() -> dict[str, Any]:
    status = renderer_runtime_status()
    if status["fail_closed_without_real_renderer"] and not status["final_master_renderer_ready"]:
        raise RuntimeError(
            "No live real-audio music renderer is reachable/configured. Start the private ACE-Step GPU "
            "renderer (preferred), enable the optional YuE worker, or configure an authenticated real-audio fallback."
        )
    return status
