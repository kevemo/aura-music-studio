from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class EngineSpec:
    name: str
    repo: str | None
    purpose: str
    env_command: str | None = None
    install: str | None = None
    final_audio: bool = True
    category: str = "audio"
    deployment: str = "local"
    maturity: str = "production_candidate"
    license_note: str = "Verify repository/model licence before public deployment."
    default_bootstrap: bool = False


ENGINES = [
    EngineSpec(
        "ace-step-1.5", "https://github.com/ace-step/ACE-Step-1.5.git",
        "Primary open full-song real-audio generator: text/lyrics-to-song, covers, repaint, remix, Vocal2BGM, audio understanding, multitrack Lego/Complete and LoRA personalisation.",
        "AURA_LOCAL_RENDER_CMD",
        category="generation", license_note="Code is MIT; model/checkpoint licences must also be reviewed.", default_bootstrap=True,
    ),
    EngineSpec(
        "the-muser", "https://github.com/noah-chelednik/the-muser.git",
        "Agentic composition/orchestration layer combining notation, audio generation, vocals and validation.",
        "AURA_MUSER_CMD", category="orchestration", maturity="experimental",
    ),
    EngineSpec(
        "yue", "https://github.com/multimodal-art-projection/YuE.git",
        "Lyrics-first full-song generation with singing and instrumental accompaniment.",
        "AURA_YUE_CMD", category="generation", maturity="advanced_optional",
    ),
    EngineSpec(
        "diffrhythm", "https://github.com/ASLP-lab/DiffRhythm.git",
        "Fast end-to-end music generation alternative for model routing and best-of-N diversity.",
        "AURA_DIFFRHYTHM_CMD", category="generation", maturity="optional",
    ),
    EngineSpec(
        "audiocraft", "https://github.com/facebookresearch/audiocraft.git",
        "MusicGen/AudioGen research stack for melody-guided music, loops, textures and audio research workflows.",
        "AURA_AUDIOCRAFT_CMD", category="generation", maturity="optional",
        license_note="Repository/model licences are not equivalent to unrestricted commercial-use licensing; review before enabling for paid outputs.",
    ),
    EngineSpec(
        "stable-audio-tools", "https://github.com/Stability-AI/stable-audio-tools.git",
        "Local generative audio toolkit for samples, textures, effects and structural audio experiments.",
        "AURA_STABLE_AUDIO_CMD", category="generation", maturity="optional",
        license_note="Review the exact model licence separately from the code licence before commercial use.",
    ),
    EngineSpec(
        "diffsinger", "https://github.com/openvpi/DiffSinger.git",
        "Singing synthesis for scored lead/backing vocals and harmony rendering with approved voicebanks.",
        "AURA_DIFFSINGER_CMD", category="vocals", default_bootstrap=True,
    ),
    EngineSpec(
        "seed-vc", "https://github.com/Plachtaa/seed-vc.git",
        "Consent-gated zero-shot voice and singing voice conversion/timbre transfer.",
        "AURA_SEEDVC_CMD", category="vocals", default_bootstrap=True,
        license_note="Only use voice references with documented permission; preserve model-specific licence requirements.",
    ),
    EngineSpec(
        "rvc", "https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI.git",
        "Optional consent-gated trained voice-conversion backend for approved custom singer profiles.",
        "AURA_RVC_CMD", category="vocals", maturity="optional",
        license_note="Never expose unrestricted third-party/personality cloning; require rights/consent records for every profile.",
    ),
    EngineSpec(
        "basic-pitch", "https://github.com/spotify/basic-pitch.git",
        "Audio-to-MIDI transcription for editable pitch/control data; never a final-audio renderer.",
        install="pip install basic-pitch", final_audio=False, category="analysis", default_bootstrap=True,
    ),
    EngineSpec(
        "whisper-cpp", "https://github.com/ggml-org/whisper.cpp.git",
        "Offline speech-to-text, VAD and real-time microphone transcription for spoken Aura control.",
        "AURA_STT_CMD", final_audio=False, category="speech", maturity="production_candidate",
        license_note="MIT code; downloaded Whisper model files must be tracked in the model licence ledger.", default_bootstrap=True,
    ),
    EngineSpec(
        "piper-tts", "https://github.com/OHF-Voice/piper1-gpl.git",
        "Offline text-to-speech engine for Aura's spoken responses and accessibility mode.",
        "AURA_TTS_CMD", category="speech", maturity="optional", deployment="separate_process",
        license_note="Piper current repository is GPL-3.0 and individual voices can have their own licences. Run as a separable service/process and review every chosen voice licence.",
    ),
    EngineSpec(
        "audio-separator", None,
        "RoFormer/UVR/MDX/Demucs source separation with selectable models for vocals, instruments and karaoke workflows.",
        install="pip install audio-separator", final_audio=False, category="separation", default_bootstrap=True,
    ),
    EngineSpec(
        "demucs", "https://github.com/facebookresearch/demucs.git",
        "Reliable multi-stem fallback separator.",
        install="pip install demucs", final_audio=False, category="separation", default_bootstrap=True,
    ),
    EngineSpec(
        "matchering", "https://github.com/sergree/matchering.git",
        "Local reference-based mastering.",
        install="pip install matchering", final_audio=False, category="mastering", default_bootstrap=True,
    ),
    EngineSpec(
        "pedalboard", "https://github.com/spotify/pedalboard.git",
        "Local programmable DSP/VST host for EQ, compression, reverb, delay, limiting and effect chains.",
        install="pip install pedalboard", final_audio=False, category="mixing", default_bootstrap=True,
    ),
    EngineSpec(
        "phaselimiter", "https://github.com/ai-mastering/phaselimiter.git",
        "Local MIT true-peak limiter/automatic-mastering fallback.",
        "AURA_PHASELIMITER_CMD", final_audio=False, category="mastering", maturity="inactive_optional",
        license_note="MIT, but upstream is currently inactive; never make this the sole mastering path.",
    ),
    EngineSpec(
        "neural-amp-modeler", "https://github.com/sdatkinson/NeuralAmpModelerCore.git",
        "Neural amp/cabinet tone modelling for realistic generated or recorded guitar/bass processing.",
        "AURA_NAM_CMD", final_audio=False, category="tone", maturity="production_candidate",
        license_note="Core DSP library is MIT; individual downloaded .nam captures may have separate usage terms.",
    ),
    EngineSpec(
        "spatial-audio-framework", "https://github.com/leomccormack/Spatial_Audio_Framework.git",
        "Ambisonics, binaural/HRTF, VBAP, room and spatial rendering for immersive/binaural masters.",
        "AURA_SPATIAL_CMD", final_audio=False, category="spatial", maturity="advanced_optional",
        license_note="Core modules are ISC; enabling some optional GPLv2 modules changes licensing obligations. Aura must track which modules are compiled.",
    ),
]


class EngineManager:
    def __init__(self, root: Path = Path("engines")):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _module_present(module: str) -> bool:
        try:
            return importlib.util.find_spec(module) is not None
        except Exception:
            return False

    def status(self) -> list[dict]:
        result = []
        for spec in ENGINES:
            path = self.root / spec.name
            configured = bool(spec.env_command and os.getenv(spec.env_command))
            installed = path.exists()
            if spec.name == "audio-separator":
                installed = shutil.which("audio-separator") is not None
            elif spec.name == "demucs":
                installed = shutil.which("demucs") is not None or self._module_present("demucs")
            elif spec.name == "matchering":
                installed = self._module_present("matchering")
            elif spec.name == "basic-pitch":
                installed = self._module_present("basic_pitch")
            elif spec.name == "pedalboard":
                installed = self._module_present("pedalboard")
            elif spec.name == "piper-tts":
                installed = self._module_present("piper") or path.exists()
            elif spec.name == "whisper-cpp":
                installed = path.exists() or shutil.which("whisper-cli") is not None
            result.append({
                **asdict(spec),
                "path": str(path),
                "installed": installed,
                "command_configured": configured,
            })
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

    def bootstrap(
        self,
        *,
        clone_engines: bool = True,
        install_packages: bool = False,
        include_optional: bool = False,
    ) -> dict:
        """Bootstrap only the curated native stack unless include_optional is requested.

        Large/experimental/licence-sensitive repositories stay discoverable in the registry but are
        not cloned automatically. This prevents a one-command install from silently pulling every
        research model or GPL component into a commercial deployment.
        """
        actions = []
        selected = [s for s in ENGINES if include_optional or s.default_bootstrap]
        for spec in selected:
            try:
                if clone_engines and spec.repo:
                    path = self.clone(spec.name)
                    actions.append({"engine": spec.name, "action": "cloned_or_present", "path": str(path)})
                if install_packages and spec.install:
                    self.install_package(spec.name)
                    actions.append({"engine": spec.name, "action": "package_installed"})
            except Exception as exc:
                actions.append({"engine": spec.name, "action": "error", "error": f"{type(exc).__name__}: {exc}"})
        report = {
            "engines_root": str(self.root),
            "include_optional": include_optional,
            "actions": actions,
            "status": self.status(),
        }
        (self.root / ".aura-engines.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    @staticmethod
    def _get(name: str) -> EngineSpec:
        for spec in ENGINES:
            if spec.name == name:
                return spec
        raise KeyError(name)
