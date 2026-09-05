from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .command_templates import render_command_argv


class NeuralToneProcessor:
    """Adapter for Neural Amp Modeler or another approved local neural tone engine."""

    def __init__(self):
        self.command = (os.getenv("AURA_NAM_CMD") or "").strip()

    @staticmethod
    def _render(template: str, values: dict[str, str]) -> None:
        argv = render_command_argv(template, values)
        subprocess.run(argv, check=True)

    def process(
        self,
        source: str | Path,
        model: str | Path,
        output: str | Path,
        *,
        input_gain_db: float = 0.0,
        output_gain_db: float = 0.0,
    ) -> tuple[Path, dict]:
        if not self.command:
            raise RuntimeError("AURA_NAM_CMD is not configured. Neural amp rendering cannot be faked with generic EQ.")
        source = Path(source).resolve()
        model = Path(model).resolve()
        output = Path(output).resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        if not model.is_file():
            raise FileNotFoundError(model)
        output.parent.mkdir(parents=True, exist_ok=True)
        self._render(
            self.command,
            {
                "input": str(source),
                "model": str(model),
                "output": str(output),
                "input_gain_db": str(float(input_gain_db)),
                "output_gain_db": str(float(output_gain_db)),
            },
        )
        if not output.exists() or output.stat().st_size < 1024:
            raise RuntimeError("Neural amp command completed without producing valid audio")
        return output, {
            "engine": "neural_amp_modeler_or_compatible",
            "model": str(model),
            "input_gain_db": float(input_gain_db),
            "output_gain_db": float(output_gain_db),
            "audio_origin": "real_audio_neural_tone_processing",
        }

    def diagnostics(self) -> dict:
        return {"configured": bool(self.command), "command_env": "AURA_NAM_CMD"}
