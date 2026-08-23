from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path

import soundfile as sf

from .acestep_api import AceStepClient

LEGACY_ACE_MODEL_ALIASES = {
    "acestep-v15-xl-turbo": "acestep-v15-turbo",
    "ace-step-v15-xl-turbo": "acestep-v15-turbo",
}


class RendererUnavailable(RuntimeError):
    """Raised when no real-waveform renderer can safely accept a production job."""


def _unwrap(payload):
    if isinstance(payload, dict) and "code" in payload:
        if int(payload.get("code", 500)) != 200:
            raise RuntimeError(payload.get("error") or "Renderer API request failed")
        return payload.get("data")
    return payload


def validate_real_audio(path: Path, *, minimum_seconds: float = 0.25) -> dict:
    path = Path(path)
    if not path.is_file() or path.stat().st_size < 1024:
        raise RuntimeError(f"Renderer output is missing or too small to be valid audio: {path}")
    try:
        info = sf.info(path)
    except Exception as exc:
        raise RuntimeError(f"Renderer output is not readable audio: {path}") from exc
    duration = float(info.frames / info.samplerate) if info.samplerate else 0.0
    if info.frames <= 0 or info.samplerate < 8000 or duration < minimum_seconds:
        raise RuntimeError(f"Renderer returned invalid/empty waveform audio: {path}")
    return {
        "path": str(path),
        "duration_seconds": duration,
        "sample_rate": int(info.samplerate),
        "channels": int(info.channels),
        "format": str(info.format),
        "subtype": str(info.subtype),
        "bytes": int(path.stat().st_size),
        "real_audio_verified": True,
    }


class AceStepRuntime:
    """Operational readiness/model-routing layer for the private ACE-Step 1.5 REST worker."""

    def __init__(self, client: AceStepClient | None = None):
        self.client = client or AceStepClient()

    def _get(self, endpoint: str, *, timeout: int = 10):
        response = self.client.session.get(self.client.base_url + endpoint.lstrip("/"), timeout=timeout)
        response.raise_for_status()
        return _unwrap(response.json())

    def health(self) -> dict:
        try:
            data = self._get("health") or {}
            return {"reachable": True, **(data if isinstance(data, dict) else {})}
        except Exception as exc:
            return {"reachable": False, "error": f"{type(exc).__name__}: {exc}"}

    def inventory(self) -> dict:
        data = self._get("v1/models") or {}
        if not isinstance(data, dict):
            raise RuntimeError("ACE-Step model inventory returned an invalid payload")
        return data

    def stats(self) -> dict:
        data = self._get("v1/stats") or {}
        return data if isinstance(data, dict) else {}

    def initialize(
        self,
        *,
        model: str | None = None,
        slot: int = 1,
        init_llm: bool = False,
        lm_model: str | None = None,
    ) -> dict:
        payload = {"slot": int(slot), "init_llm": bool(init_llm)}
        if model:
            payload["model"] = self._canonical_model(model)
        if lm_model:
            payload["lm_model_path"] = lm_model
        response = self.client.session.post(self.client.base_url + "v1/init", json=payload, timeout=900)
        response.raise_for_status()
        data = _unwrap(response.json())
        if not isinstance(data, dict):
            raise RuntimeError("ACE-Step model initialization returned an invalid payload")
        return data

    @staticmethod
    def _canonical_model(name: str | None) -> str | None:
        if not name:
            return None
        clean = str(name).strip()
        return LEGACY_ACE_MODEL_ALIASES.get(clean, clean)

    def resolve_model(
        self,
        requested: str | None = None,
        *,
        task_type: str = "text2music",
        require_loaded: bool = True,
    ) -> str:
        inventory = self.inventory()
        models = [row for row in inventory.get("models", []) if isinstance(row, dict) and row.get("name")]
        requested = self._canonical_model(requested)
        default = self._canonical_model(inventory.get("default_model"))

        def supports(row):
            tasks = row.get("supported_task_types") or []
            return not tasks or task_type in tasks

        candidates = [row for row in models if supports(row) and (row.get("is_loaded") or not require_loaded)]
        if requested:
            for row in candidates:
                if row["name"] == requested:
                    return requested
            raise RendererUnavailable(
                f"ACE-Step model '{requested}' is not loaded with support for {task_type}. "
                "Check renderer status or initialize that model slot."
            )
        if default and any(row["name"] == default for row in candidates):
            return default
        if candidates:
            return str(candidates[0]["name"])
        raise RendererUnavailable(f"ACE-Step has no loaded model supporting {task_type}")

    def status(self, *, include_stats: bool = True) -> dict:
        health = self.health()
        if not health.get("reachable"):
            return {
                "engine": "ace-step-1.5",
                "configured": bool(os.getenv("AURA_ACESTEP_API_URL")),
                "reachable": False,
                "ready": False,
                "health": health,
                "models": [],
                "default_model": None,
                "full_song_model": None,
                "track_model": None,
            }
        try:
            inventory = self.inventory()
            full_requested = os.getenv("AURA_ACESTEP_FULL_MODEL", "acestep-v15-turbo")
            track_requested = os.getenv("AURA_ACESTEP_TRACK_MODEL", "acestep-v15-base")
            full_model = self.resolve_model(full_requested, task_type="text2music")
            try:
                track_model = self.resolve_model(track_requested, task_type="complete")
            except RendererUnavailable:
                track_model = None
            stats = self.stats() if include_stats else {}
            return {
                "engine": "ace-step-1.5",
                "configured": True,
                "reachable": True,
                "ready": bool(full_model),
                "health": health,
                "models": inventory.get("models", []),
                "default_model": inventory.get("default_model"),
                "llm_initialized": bool(inventory.get("llm_initialized")),
                "loaded_lm_model": inventory.get("loaded_lm_model"),
                "full_song_model": full_model,
                "track_model": track_model,
                "supports_build_around": bool(track_model),
                "stats": stats,
            }
        except Exception as exc:
            return {
                "engine": "ace-step-1.5",
                "configured": True,
                "reachable": True,
                "ready": False,
                "health": health,
                "models": [],
                "error": f"{type(exc).__name__}: {exc}",
            }

    def require_ready(self, *, task_type: str = "text2music") -> str:
        requested = (
            os.getenv("AURA_ACESTEP_TRACK_MODEL", "acestep-v15-base")
            if task_type in {"lego", "extract", "complete"}
            else os.getenv("AURA_ACESTEP_FULL_MODEL", "acestep-v15-turbo")
        )
        if not self.health().get("reachable"):
            raise RendererUnavailable(
                "ACE-Step neural renderer is offline. Aura cannot accept this neural render until a GPU worker is available."
            )
        return self.resolve_model(requested, task_type=task_type)


def _command_status(env_var: str) -> dict:
    raw = (os.getenv(env_var) or "").strip()
    if not raw:
        return {"configured": False, "ready": False}
    try:
        executable = shlex.split(raw)[0]
    except Exception:
        return {"configured": True, "ready": False, "error": "invalid command"}
    ready = bool(Path(executable).expanduser().exists() or shutil.which(executable))
    return {"configured": True, "ready": ready, "executable": Path(executable).name}


def yue_status() -> dict:
    status = _command_status("AURA_YUE_CMD")
    return {
        "engine": "yue",
        **status,
        "role": "optional_high_vram_lyrics_first_renderer",
        "license": "Apache-2.0",
    }


def real_renderer_status(*, include_stats: bool = True) -> dict:
    ace = AceStepRuntime().status(include_stats=include_stats)
    yue = yue_status()
    other = {
        "local_acestep": _command_status("AURA_LOCAL_RENDER_CMD"),
        "muser": _command_status("AURA_MUSER_CMD"),
        "diffrhythm": _command_status("AURA_DIFFRHYTHM_CMD"),
        "stable_audio": _command_status("AURA_STABLE_AUDIO_CMD"),
    }
    hosted_optional = {
        "eleven_music": bool(os.getenv("ELEVENLABS_API_KEY")),
        "mureka": bool(os.getenv("MUREKA_API_KEY")),
        "deapi": bool(os.getenv("DEAPI_API_KEY")),
    }
    any_local = any(item.get("ready") for item in other.values())
    return {
        "primary": "ace-step-1.5",
        "primary_ready": bool(ace.get("ready")),
        "ace_step": ace,
        "yue": yue,
        "other_local_commands": other,
        "hosted_optional": hosted_optional,
        "any_real_renderer_ready": bool(
            ace.get("ready") or yue.get("ready") or any_local or any(hosted_optional.values())
        ),
        "final_audio_policy": "real_waveform_only",
    }


def require_render_capacity(*, task_type: str = "text2music") -> dict:
    """Fail before quota/queue admission when there is no executable real-audio path.

    ACE-Step remains first priority. For full-song text generation only, an explicitly configured
    executable local/YuE engine or existing hosted fallback may keep the service available.
    Complete/Lego/Extract require ACE-Step Base because those workflows depend on its native task API.
    """
    try:
        model = AceStepRuntime().require_ready(task_type=task_type)
        return {"engine": "ace-step-1.5", "model": model, "task_type": task_type}
    except RendererUnavailable as ace_error:
        if task_type in {"complete", "lego", "extract"}:
            raise
        status = real_renderer_status(include_stats=False)
        if status["yue"].get("ready"):
            return {"engine": "yue", "task_type": task_type}
        for name, item in status["other_local_commands"].items():
            if item.get("ready"):
                return {"engine": name, "task_type": task_type}
        for name, ready in status["hosted_optional"].items():
            if ready:
                return {"engine": name, "task_type": task_type}
        raise RendererUnavailable(
            "No verified real-audio renderer is available. Start the ESP ACE-Step GPU worker, "
            "an enrolled compatible compute node, or an explicitly configured fallback before rendering. "
            f"ACE-Step status: {ace_error}"
        ) from ace_error
