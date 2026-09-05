from __future__ import annotations

import os
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from .command_templates import render_command_argv


class SpatialRenderer:
    """Stereo positioning plus adapter for true binaural/Ambisonic rendering."""

    def __init__(self):
        self.command = (os.getenv("AURA_SPATIAL_CMD") or "").strip()

    @staticmethod
    def _equal_power_pan(stereo: np.ndarray, pan: float) -> np.ndarray:
        pan = max(-1.0, min(1.0, float(pan)))
        mono = stereo.mean(axis=1)
        angle = (pan + 1.0) * np.pi / 4.0
        left = mono * np.cos(angle)
        right = mono * np.sin(angle)
        return np.column_stack([left, right]).astype(np.float32)

    def stereo_position(
        self,
        source: str | Path,
        output: str | Path,
        *,
        pan: float = 0.0,
        width: float = 1.0,
    ) -> tuple[Path, dict]:
        source = Path(source).resolve()
        output = Path(output).resolve()
        audio, sr = sf.read(source, always_2d=True, dtype="float32")
        if audio.shape[1] == 1:
            audio = np.repeat(audio, 2, axis=1)
        elif audio.shape[1] > 2:
            audio = audio[:, :2]

        mid = (audio[:, 0] + audio[:, 1]) * 0.5
        side = (audio[:, 0] - audio[:, 1]) * 0.5 * max(0.0, min(2.0, float(width)))
        widened = np.column_stack([mid + side, mid - side]).astype(np.float32)
        if abs(pan) > 1e-6:
            widened = self._equal_power_pan(widened, pan)
        peak = float(np.max(np.abs(widened))) if len(widened) else 0.0
        if peak > 0.999:
            widened *= 0.98 / peak
        output.parent.mkdir(parents=True, exist_ok=True)
        sf.write(output, widened, sr, subtype="PCM_24")
        return output, {
            "mode": "stereo",
            "pan": float(pan),
            "width": float(width),
            "sample_rate": sr,
            "audio_origin": "real_audio_dsp_spatial",
        }

    def immersive(
        self,
        source: str | Path,
        output: str | Path,
        *,
        mode: str = "binaural",
        azimuth_deg: float = 0.0,
        elevation_deg: float = 0.0,
        distance_m: float = 1.0,
    ) -> tuple[Path, dict]:
        if not self.command:
            raise RuntimeError(
                "True binaural/Ambisonic rendering requires AURA_SPATIAL_CMD connected to "
                "Spatial Audio Framework or another local HRTF/Ambisonics renderer."
            )
        source = Path(source).resolve()
        output = Path(output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        values = {
            "input": str(source), "output": str(output), "mode": mode,
            "azimuth": str(float(azimuth_deg)), "elevation": str(float(elevation_deg)),
            "distance": str(float(distance_m)),
        }
        argv = render_command_argv(self.command, values)
        subprocess.run(argv, check=True)
        if not output.exists() or output.stat().st_size < 1024:
            raise RuntimeError("Spatial renderer did not create valid audio")
        return output, {"mode": mode, "engine": "configured_local_spatial_renderer", **values}
