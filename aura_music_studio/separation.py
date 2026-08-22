from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import zipfile
from pathlib import Path

from .cloud_providers import ElevenMusicClient


class StemSeparator:
    def __init__(self, work_dir: Path):
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def separate(self, source: Path, mode: str = "six_stems") -> dict[str, Path]:
        errors = []
        strategies = [
            self._custom,
            self._audio_separator,
            self._eleven,
            self._demucs,
        ]
        for strategy in strategies:
            try:
                result = strategy(source, mode)
                if result:
                    return result
            except Exception as exc:
                errors.append(f"{strategy.__name__}: {type(exc).__name__}: {exc}")
        raise RuntimeError("No stem separator succeeded. " + " | ".join(errors))

    def _custom(self, source: Path, mode: str) -> dict[str, Path]:
        command = os.getenv("AURA_SEPARATOR_CMD")
        if not command:
            return {}
        out = self.work_dir / "custom"
        out.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.update({"AURA_SOURCE": str(source), "AURA_STEMS_DIR": str(out), "AURA_STEM_MODE": mode})
        subprocess.run(shlex.split(command), env=env, check=True)
        return self._collect(out)

    def _audio_separator(self, source: Path, mode: str) -> dict[str, Path]:
        binary = shutil.which("audio-separator")
        model = os.getenv("AURA_SEPARATOR_MODEL")
        if not binary or not model:
            return {}
        out = self.work_dir / "roformer"
        out.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            binary, str(source),
            "--model_filename", model,
            "--output_dir", str(out),
            "--output_format", "WAV",
        ], check=True)
        return self._collect(out)

    def _eleven(self, source: Path, mode: str) -> dict[str, Path]:
        if not os.getenv("ELEVENLABS_API_KEY"):
            return {}
        out = self.work_dir / "eleven"
        out.mkdir(parents=True, exist_ok=True)
        archive = out / "stems.zip"
        ElevenMusicClient().separate_stems(source, archive, six_stems=(mode != "two_stems"))
        with zipfile.ZipFile(archive) as z:
            z.extractall(out)
        return self._collect(out)

    def _demucs(self, source: Path, mode: str) -> dict[str, Path]:
        binary = shutil.which("demucs")
        if binary:
            cmd = [binary]
        else:
            try:
                import demucs  # noqa: F401
                cmd = ["python", "-m", "demucs"]
            except Exception:
                return {}
        model = "htdemucs_6s" if mode != "two_stems" else "htdemucs"
        out = self.work_dir / "demucs"
        out.mkdir(parents=True, exist_ok=True)
        subprocess.run(cmd + ["-n", model, "--float32", "-o", str(out), str(source)], check=True)
        return self._collect(out)

    @staticmethod
    def _collect(root: Path) -> dict[str, Path]:
        stems = {}
        for path in root.rglob("*.wav"):
            name = path.stem.lower()
            role = None
            for candidate in (
                "vocals", "instrumental", "drums", "bass", "guitar", "piano", "other", "keys", "strings", "synth"
            ):
                if candidate in name:
                    role = candidate
                    break
            if role:
                # Keep the largest candidate if a backend emits previews plus final outputs.
                current = stems.get(role)
                if current is None or path.stat().st_size > current.stat().st_size:
                    stems[role] = path
        return stems
