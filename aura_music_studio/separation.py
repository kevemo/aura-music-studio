from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

from .cloud_providers import ElevenMusicClient


VALID_MODES = {"two_stems", "four_stems", "six_stems", "detailed"}
_MAX_ARCHIVE_FILES = 64
_MAX_ARCHIVE_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024


def _safe_extract_stem_archive(archive: Path, destination: Path) -> None:
    """Extract a provider stem archive without allowing it to escape destination.

    Provider responses are untrusted input. Reject traversal/absolute paths, Windows
    drive-like names, links and unexpectedly large archives before writing a member.
    """
    root = destination.resolve()
    root.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive) as z:
        members = z.infolist()
        if len(members) > _MAX_ARCHIVE_FILES:
            raise ValueError("Stem archive contains too many entries")
        if sum(max(0, info.file_size) for info in members) > _MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ValueError("Stem archive is too large when extracted")

        planned: list[tuple[zipfile.ZipInfo, Path]] = []
        for info in members:
            normalized = info.filename.replace("\\", "/")
            member = PurePosixPath(normalized)
            parts = member.parts
            if (
                not normalized
                or member.is_absolute()
                or ".." in parts
                or (parts and ":" in parts[0])
            ):
                raise ValueError("Unsafe path in stem archive")

            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(unix_mode):
                raise ValueError("Links are not allowed in stem archives")

            target = (root / Path(*parts)).resolve()
            if target != root and root not in target.parents:
                raise ValueError("Stem archive entry escapes extraction directory")
            planned.append((info, target))

        # Validate every member first so a malicious later member cannot leave a
        # partially extracted provider response behind.
        for info, target in planned:
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info, "r") as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink)


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
        extracted = out / "extracted"
        ElevenMusicClient().separate_stems(source, archive, six_stems=(mode in {"six_stems", "detailed"}))
        if extracted.exists():
            shutil.rmtree(extracted)
        _safe_extract_stem_archive(archive, extracted)
        return self._collect(extracted)

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
