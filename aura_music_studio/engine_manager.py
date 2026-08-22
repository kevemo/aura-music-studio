from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass(frozen=True)
class EngineSpec:
    name: str
    repo: str | None
    purpose: str
    env_command: str | None = None
    install: str | None = None
    final_audio: bool = True


ENGINES = [
    EngineSpec(
        "ace-step-1.5", "https://github.com/ace-step/ACE-Step-1.5.git",
        "Primary open full-song real-audio generator: original songs, covers, repaint, remix, multitrack and audio understanding.",
        "AURA_LOCAL_RENDER_CMD",
    ),
    EngineSpec(
        "the-muser", "https://github.com/noah-chelednik/the-muser.git",
        "Agentic composition/orchestration layer combining notation, audio generation, vocals and validation.",
        "AURA_MUSER_CMD",
    ),
    EngineSpec(
        "yue", "https://github.com/multimodal-art-projection/YuE.git",
        "Lyrics-first full-song generation with vocal and instrumental accompaniment.",
        "AURA_YUE_CMD",
    ),
    EngineSpec(
        "diffsinger", "https://github.com/openvpi/DiffSinger.git",
        "Singing synthesis for scored lead/backing vocals and harmony rendering with approved voices/voicebanks.",
        "AURA_DIFFSINGER_CMD",
    ),
    EngineSpec(
        "seed-vc", "https://github.com/Plachtaa/seed-vc.git",
        "Consent-gated zero-shot voice conversion / singing timbre transfer.",
        "AURA_SEEDVC_CMD",
    ),
    EngineSpec(
        "basic-pitch", "https://github.com/spotify/basic-pitch.git",
        "Audio-to-MIDI transcription for editable control data; never a final-audio renderer.",
        install="pip install basic-pitch", final_audio=False,
    ),
    EngineSpec(
        "audio-separator", None,
        "RoFormer/UVR/MDX/Demucs stem separation with selectable models.",
        install="pip install audio-separator", final_audio=False,
    ),
    EngineSpec(
        "demucs", "https://github.com/facebookresearch/demucs.git",
        "Reliable multi-stem fallback separator.",
        install="pip install demucs", final_audio=False,
    ),
    EngineSpec(
        "matchering", "https://github.com/sergree/matchering.git",
        "Reference-based mastering.",
        install="pip install matchering", final_audio=False,
    ),
]


class EngineManager:
    def __init__(self, root: Path = Path("engines")):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def status(self) -> list[dict]:
        result = []
        for spec in ENGINES:
            path = self.root / spec.name
            configured = bool(spec.env_command and os.getenv(spec.env_command))
            installed = path.exists()
            if spec.name == "audio-separator":
                installed = shutil.which("audio-separator") is not None
            elif spec.name == "demucs":
                installed = shutil.which("demucs") is not None
            elif spec.name == "matchering":
                try:
                    import matchering  # noqa: F401
                    installed = True
                except Exception:
                    pass
            elif spec.name == "basic-pitch":
                try:
                    import basic_pitch  # noqa: F401
                    installed = True
                except Exception:
                    pass
            result.append({**asdict(spec), "path": str(path), "installed": installed, "command_configured": configured})
        return result

    def clone(self, name: str, *, update: bool = False) -> Path:
        spec = self._get(name)
        if not spec.repo:
            raise ValueError(f"{name} is installed as a package rather than cloned")
        if not shutil.which("git"):
            raise RuntimeError("git is required to clone local AI engines")
        target = self.root / spec.name
        if target.exists():
            if update and (target / ".git").exists():
                subprocess.run(["git", "-C", str(target), "pull", "--ff-only"], check=True)
            return target
        subprocess.run(["git", "clone", "--depth", "1", spec.repo, str(target)], check=True)
        return target

    def install_package(self, name: str) -> None:
        spec = self._get(name)
        if not spec.install:
            raise ValueError(f"No package install command is defined for {name}")
        subprocess.run(spec.install.split(), check=True)

    def bootstrap(self, *, clone_engines: bool = True, install_packages: bool = False) -> dict:
        actions = []
        for spec in ENGINES:
            try:
                if clone_engines and spec.repo:
                    path = self.clone(spec.name)
                    actions.append({"engine": spec.name, "action": "cloned_or_present", "path": str(path)})
                if install_packages and spec.install:
                    self.install_package(spec.name)
                    actions.append({"engine": spec.name, "action": "package_installed"})
            except Exception as exc:
                actions.append({"engine": spec.name, "action": "error", "error": f"{type(exc).__name__}: {exc}"})
        report = {"engines_root": str(self.root), "actions": actions, "status": self.status()}
        (self.root / ".aura-engines.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    @staticmethod
    def _get(name: str) -> EngineSpec:
        for spec in ENGINES:
            if spec.name == name:
                return spec
        raise KeyError(name)
