from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import zipfile
from pathlib import Path

from .cloud_providers import ElevenMusicClient


VALID_MODES = {"two_stems", "four_stems", "six_stems", "detailed"}


class StemSeparator:
    def __init__(self, work_dir: Path):
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def separate(self, source: Path, mode: str = "six_stems") -> dict[str, Path]:
        if mode not in VALID_MODES:
            raise ValueError(f"Unknown stem mode {mode!r}; choose from {sorted(VALID_MODES)}")
        errors = []
        strategies = [self._custom, self._audio_separator, self._eleven, self._demucs]
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
        out = self.work_dir / f"custom_{mode}"
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
        # A configured RoFormer/MDX/UVR model is especially useful for detailed isolation.
        out = self.work_dir / f"roformer_{mode}"
        out.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            binary, str(source), "--model_filename", model,
            "--output_dir", str(out), "--output_format", "WAV",
        ], check=True)
        stems = self._collect(out)
        if mode == "two_stems" and "vocals" in stems:
            # Many two-stem models label accompaniment/instrumental explicitly.
            return {k: v for k, v in stems.items() if k in {"vocals", "instrumental", "other"}}
        return stems

    def _eleven(self, source: Path, mode: str) -> dict[str, Path]:
        if not os.getenv("ELEVENLABS_API_KEY"):
            return {}
        out = self.work_dir / f"eleven_{mode}"
        out.mkdir(parents=True, exist_ok=True)
        archive = out / "stems.zip"
        ElevenMusicClient().separate_stems(source, archive, six_stems=(mode in {"six_stems", "detailed"}))
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
        out = self.work_dir / f"demucs_{mode}"
        out.mkdir(parents=True, exist_ok=True)
        if mode == "two_stems":
            args = ["-n", "htdemucs", "--two-stems", "vocals"]
        elif mode == "four_stems":
            args = ["-n", "htdemucs"]
        else:
            args = ["-n", "htdemucs_6s"]
        subprocess.run(cmd + args + ["--float32", "-o", str(out), str(source)], check=True)
        stems = self._collect(out)
        if mode == "two_stems":
            # Demucs names the accompaniment no_vocals in two-stem mode.
            for p in out.rglob("*.wav"):
                if "no_vocals" in p.stem.lower():
                    stems["instrumental"] = p
        return stems

    @staticmethod
    def _collect(root: Path) -> dict[str, Path]:
        stems = {}
        aliases = {
            "no_vocals": "instrumental", "accompaniment": "instrumental", "instrumental": "instrumental",
            "vocals": "vocals", "drums": "drums", "bass": "bass", "guitar": "guitar", "piano": "piano",
            "other": "other", "keys": "keys", "keyboard": "keys", "strings": "strings", "synth": "synth",
            "percussion": "percussion", "brass": "brass", "woodwinds": "woodwinds",
        }
        for path in root.rglob("*.wav"):
            name = path.stem.lower()
            role = next((role for token, role in aliases.items() if token in name), None)
            if role:
                current = stems.get(role)
                if current is None or path.stat().st_size > current.stat().st_size:
                    stems[role] = path
        return stems
