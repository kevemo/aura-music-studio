from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal


class AudioRestorer:
    """Real-waveform cleanup with optional local neural restoration.

    If AURA_DENOISE_CMD is configured, Aura delegates to that local model/process. Otherwise
    it uses conservative deterministic DSP (DC/sub-rumble removal, optional mains hum notch,
    gentle de-essing/high-frequency control). The fallback never claims to be neural repair.
    """

    def __init__(self):
        self.command = (os.getenv("AURA_DENOISE_CMD") or "").strip()

    @staticmethod
    def _run_template(template: str, values: dict[str, str]) -> None:
        rendered = template
        for key, value in values.items():
            rendered = rendered.replace("{" + key + "}", shlex.quote(value))
        subprocess.run(rendered, shell=True, check=True)

    def clean(
        self,
        source: str | Path,
        output: str | Path,
        *,
        hum_hz: float | None = None,
        highpass_hz: float = 35.0,
        neural: bool = True,
    ) -> tuple[Path, dict]:
        source = Path(source).resolve()
        output = Path(output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        if not source.is_file():
            raise FileNotFoundError(source)

        if neural and self.command:
            self._run_template(
                self.command,
                {"input": str(source), "output": str(output), "hum_hz": str(hum_hz or 0)},
            )
            if not output.exists() or output.stat().st_size < 1024:
                raise RuntimeError("Configured neural denoise command did not produce valid audio")
            return output, {"engine": "configured_local_neural", "neural": True}

        audio, sr = sf.read(source, always_2d=True, dtype="float32")
        if len(audio) == 0:
            raise ValueError("Source audio is empty")

        # Remove DC and sub-rumble with a zero-phase high-pass filter.
        hp = max(10.0, min(float(highpass_hz), sr * 0.1))
        sos = signal.butter(2, hp, btype="highpass", fs=sr, output="sos")
        processed = signal.sosfiltfilt(sos, audio, axis=0).astype(np.float32)

        # Optional mains-hum removal. Use a narrow notch plus first harmonic when valid.
        notches = []
        if hum_hz and 20 <= hum_hz <= 120:
            for freq in (float(hum_hz), float(hum_hz) * 2):
                if freq < sr / 2 - 100:
                    b, a = signal.iirnotch(freq, Q=35.0, fs=sr)
                    processed = signal.filtfilt(b, a, processed, axis=0).astype(np.float32)
                    notches.append(freq)

        # Tame ultrasonic/very high-frequency garbage without dulling normal music.
        lp_hz = min(20_000.0, sr * 0.47)
        if lp_hz > 12_000:
            sos_lp = signal.butter(2, lp_hz, btype="lowpass", fs=sr, output="sos")
            processed = signal.sosfiltfilt(sos_lp, processed, axis=0).astype(np.float32)

        peak = float(np.max(np.abs(processed)))
        if peak > 0.999:
            processed *= 0.98 / peak
        sf.write(output, processed, sr, subtype="PCM_24")
        return output, {
            "engine": "local_deterministic_dsp",
            "neural": False,
            "sample_rate": sr,
            "highpass_hz": hp,
            "hum_notches_hz": notches,
            "note": "For spectral source-aware restoration, configure AURA_DENOISE_CMD with a local neural model.",
        }

    def diagnostics(self) -> dict:
        return {
            "neural_command_configured": bool(self.command),
            "ffmpeg": shutil.which("ffmpeg"),
            "fallback": "local_deterministic_dsp",
        }
