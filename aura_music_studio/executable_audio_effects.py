from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

import numpy as np
import soundfile as sf
from pydantic import BaseModel, Field, model_validator
from scipy.signal import butter, sosfilt

EffectKind = Literal[
    "gain",
    "high_pass",
    "low_pass",
    "fade_in",
    "fade_out",
    "normalize_peak",
]

_SAFE_PRESET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_SUPPORTED_AUDIO = {".wav", ".flac", ".ogg"}


class AudioEffectNode(BaseModel):
    kind: EffectKind
    enabled: bool = True
    mix: float = Field(default=1.0, ge=0.0, le=1.0)
    gain_db: float = Field(default=0.0, ge=-60.0, le=24.0)
    cutoff_hz: float = Field(default=1000.0, ge=20.0, le=20000.0)
    duration_seconds: float = Field(default=0.5, ge=0.0, le=30.0)
    peak_dbfs: float = Field(default=-1.0, ge=-24.0, le=0.0)


class AudioEffectGraph(BaseModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    name: str = Field(default="Aura Audio FX", min_length=1, max_length=120)
    nodes: list[AudioEffectNode] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def validate_filter_cutoffs(self):
        for node in self.nodes:
            if node.kind in {"high_pass", "low_pass"} and node.cutoff_hz <= 0:
                raise ValueError("Filter cutoff must be positive")
        return self

    def fingerprint(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def _read_audio(source: Path) -> tuple[np.ndarray, int]:
    if source.suffix.lower() not in _SUPPORTED_AUDIO:
        raise ValueError("Executable audio effects require WAV, FLAC or OGG input")
    if not source.is_file():
        raise FileNotFoundError(source)
    audio, sample_rate = sf.read(source, dtype="float32", always_2d=True)
    if sample_rate < 8000 or sample_rate > 384000:
        raise ValueError("Unsupported sample rate")
    if audio.shape[0] < 1:
        raise ValueError("Audio input is empty")
    if audio.shape[1] > 32:
        raise ValueError("Audio channel count exceeds the supported limit")
    if not np.isfinite(audio).all():
        raise ValueError("Audio contains non-finite samples")
    return audio, int(sample_rate)


def _blend(original: np.ndarray, effected: np.ndarray, mix: float) -> np.ndarray:
    if mix <= 0.0:
        return original
    if mix >= 1.0:
        return effected
    return original * (1.0 - mix) + effected * mix


def _filter(audio: np.ndarray, sample_rate: int, cutoff_hz: float, kind: str) -> np.ndarray:
    nyquist = sample_rate / 2.0
    if cutoff_hz >= nyquist * 0.99:
        raise ValueError("Filter cutoff must be below the Nyquist frequency")
    sos = butter(2, cutoff_hz / nyquist, btype="highpass" if kind == "high_pass" else "lowpass", output="sos")
    return sosfilt(sos, audio, axis=0).astype(np.float32, copy=False)


def _fade(audio: np.ndarray, sample_rate: int, seconds: float, *, fade_in: bool) -> np.ndarray:
    count = min(audio.shape[0], int(round(seconds * sample_rate)))
    if count <= 0:
        return audio.copy()
    result = audio.copy()
    ramp = np.linspace(0.0, 1.0, count, dtype=np.float32)
    if not fade_in:
        ramp = ramp[::-1]
        result[-count:] *= ramp[:, None]
    else:
        result[:count] *= ramp[:, None]
    return result


def _apply_node(audio: np.ndarray, sample_rate: int, node: AudioEffectNode) -> np.ndarray:
    if not node.enabled or node.mix <= 0.0:
        return audio
    original = audio
    if node.kind == "gain":
        effected = audio * np.float32(10.0 ** (node.gain_db / 20.0))
    elif node.kind in {"high_pass", "low_pass"}:
        effected = _filter(audio, sample_rate, node.cutoff_hz, node.kind)
    elif node.kind == "fade_in":
        effected = _fade(audio, sample_rate, node.duration_seconds, fade_in=True)
    elif node.kind == "fade_out":
        effected = _fade(audio, sample_rate, node.duration_seconds, fade_in=False)
    elif node.kind == "normalize_peak":
        peak = float(np.max(np.abs(audio)))
        if peak <= 1e-12:
            effected = audio.copy()
        else:
            target = 10.0 ** (node.peak_dbfs / 20.0)
            effected = audio * np.float32(target / peak)
    else:  # pragma: no cover - pydantic prevents unknown kinds
        raise ValueError("Unsupported effect node")
    return _blend(original, effected, node.mix).astype(np.float32, copy=False)


def render_audio_effect_graph(source: str | Path, destination: str | Path, graph: AudioEffectGraph) -> dict:
    """Render an allowlisted, editable audio-effect graph into a real audio file.

    This runtime deliberately accepts no command strings, URLs, plugins, Python expressions or
    arbitrary effect identifiers. It performs local DSP only and returns logical render evidence.
    """
    src = Path(source)
    dst = Path(destination)
    if dst.suffix.lower() not in _SUPPORTED_AUDIO:
        raise ValueError("Effect render output must be WAV, FLAC or OGG")
    audio, sample_rate = _read_audio(src)
    result = audio
    for node in graph.nodes:
        result = _apply_node(result, sample_rate, node)
    result = np.clip(result, -1.0, 1.0).astype(np.float32, copy=False)
    dst.parent.mkdir(parents=True, exist_ok=True)
    temporary = dst.with_name(f".{dst.name}.tmp{dst.suffix}")
    sf.write(temporary, result, sample_rate, subtype="FLOAT" if dst.suffix.lower() == ".wav" else None)
    temporary.replace(dst)
    return {
        "rendered": True,
        "effect_graph_fingerprint": graph.fingerprint(),
        "node_count": len(graph.nodes),
        "sample_rate": sample_rate,
        "channels": int(result.shape[1]),
        "frames": int(result.shape[0]),
        "audio_origin": "local_allowlisted_dsp",
        "arbitrary_code_execution": False,
        "network_access": False,
    }


def save_effect_preset(directory: str | Path, preset_name: str, graph: AudioEffectGraph) -> Path:
    """Persist a reusable editable effect graph without allowing path traversal."""
    if not _SAFE_PRESET_NAME.fullmatch(preset_name):
        raise ValueError("Preset name contains unsupported characters")
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = (root / f"{preset_name}.json").resolve()
    if target.parent != root:
        raise ValueError("Preset path escapes its library")
    temporary = root / f".{target.name}.tmp"
    temporary.write_text(
        json.dumps(graph.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def load_effect_preset(directory: str | Path, preset_name: str) -> AudioEffectGraph:
    if not _SAFE_PRESET_NAME.fullmatch(preset_name):
        raise ValueError("Preset name contains unsupported characters")
    root = Path(directory).resolve()
    target = (root / f"{preset_name}.json").resolve()
    if target.parent != root or not target.is_file():
        raise FileNotFoundError(preset_name)
    return AudioEffectGraph.model_validate_json(target.read_text(encoding="utf-8"))


__all__ = [
    "AudioEffectGraph",
    "AudioEffectNode",
    "load_effect_preset",
    "render_audio_effect_graph",
    "save_effect_preset",
]
