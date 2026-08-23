from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ModelPolicy:
    id: str
    component: str
    role: str
    source: str
    core_or_optional: str
    code_license: str
    model_license: str
    commercial_use: str
    consent_required: bool = False
    redistribution: str = "review upstream terms"
    pinned_revision: str | None = None
    notes: str = ""


CATALOG: tuple[ModelPolicy, ...] = (
    ModelPolicy(
        id="ace-step-1.5",
        component="ACE-Step 1.5",
        role="primary full-song / cover / repaint / track generation",
        source="https://github.com/ace-step/ACE-Step-1.5",
        core_or_optional="core",
        code_license="MIT (upstream repository)",
        model_license="check the exact downloaded checkpoint/model card at install time",
        commercial_use="allowed only when the selected checkpoint licence permits the intended use",
        pinned_revision="14c0211d5a0653b0f63e27686f4c3f151b4d8629",
        notes="Pinned worker source; model weights remain separately licensed assets and are not committed to ESP GitHub.",
    ),
    ModelPolicy(
        id="yue",
        component="YuE",
        role="optional heavy lyrics-first full-song generation",
        source="https://github.com/multimodal-art-projection/YuE",
        core_or_optional="optional self-hosted renderer",
        code_license="Apache-2.0 (upstream announcement/repository at pinned revision)",
        model_license="track the exact YuE stage-1/stage-2/xcodec model cards separately at deployment",
        commercial_use="upstream explicitly encourages creators to use and monetize generated outputs subject to its stated attribution/model terms; ESP still performs per-model deployment review",
        pinned_revision="9f1394bae1d8d218fea750c1413c2d9d731c7310",
        notes="YuE upstream requests credit as YuE by HKUST/M-A-P. Heavy GPU requirements make it optional rather than ESP's default renderer.",
    ),
    ModelPolicy(
        id="the-muser",
        component="The Muser",
        role="agentic music orchestration / best-of-N generation",
        source="https://github.com/noah-chelednik/the-muser",
        core_or_optional="experimental optional",
        code_license="review current upstream licence",
        model_license="inherits licences of its configured component models",
        commercial_use="not assumed; each dependency must be reviewed",
    ),
    ModelPolicy(
        id="diffsinger",
        component="DiffSinger",
        role="scored singing synthesis / harmonies",
        source="https://github.com/openvpi/DiffSinger",
        core_or_optional="optional vocal",
        code_license="review current upstream licence",
        model_license="voicebank/model specific",
        commercial_use="depends on the selected voicebank/model licence",
        consent_required=True,
        notes="Aura additionally requires an approved voice profile when a member-specific voice is used.",
    ),
    ModelPolicy(
        id="seed-vc",
        component="Seed-VC",
        role="zero-shot singing/speech voice conversion",
        source="https://github.com/Plachtaa/seed-vc",
        core_or_optional="optional pro vocal",
        code_license="review current upstream licence",
        model_license="review checkpoint terms",
        commercial_use="not assumed; deploy only after licence review",
        consent_required=True,
    ),
    ModelPolicy(
        id="rvc",
        component="RVC-compatible converter",
        role="trained singing voice conversion fallback",
        source="configured by deployment",
        core_or_optional="optional pro vocal",
        code_license="implementation specific",
        model_license="voice model specific",
        commercial_use="not assumed",
        consent_required=True,
    ),
    ModelPolicy(
        id="audio-separator",
        component="audio-separator / UVR-compatible models",
        role="advanced stem separation",
        source="https://github.com/nomadkaraoke/python-audio-separator",
        core_or_optional="optional pro engineering",
        code_license="review current upstream licence",
        model_license="separator-model specific",
        commercial_use="check selected model licence",
    ),
    ModelPolicy(
        id="demucs",
        component="Demucs",
        role="stem separation fallback",
        source="https://github.com/facebookresearch/demucs",
        core_or_optional="engineering fallback",
        code_license="MIT (archived upstream repository)",
        model_license="review distributed model terms",
        commercial_use="review model terms for deployment",
    ),
    ModelPolicy(
        id="matchering",
        component="Matchering",
        role="reference-based mastering",
        source="https://github.com/sergree/matchering",
        core_or_optional="optional mastering",
        code_license="GPL-3.0 upstream; isolate appropriately if distribution obligations matter",
        model_license="not applicable",
        commercial_use="software use can be commercial; distribution must comply with GPL",
    ),
    ModelPolicy(
        id="pedalboard",
        component="Spotify Pedalboard",
        role="programmatic DSP / plugin host",
        source="https://github.com/spotify/pedalboard",
        core_or_optional="engineering",
        code_license="GPL-3.0 upstream at time of catalogue entry; verify current release",
        model_license="plugin-specific when external VSTs are loaded",
        commercial_use="verify distribution obligations and every third-party plugin licence",
    ),
    ModelPolicy(
        id="nam",
        component="Neural Amp Modeler",
        role="neural guitar/bass amp tone",
        source="https://github.com/sdatkinson/NeuralAmpModelerCore",
        core_or_optional="optional pro engineering",
        code_license="review current upstream licence",
        model_license="individual .nam capture specific",
        commercial_use="depends on capture licence; Aura stores the member rights attestation",
    ),
    ModelPolicy(
        id="whisper-cpp",
        component="whisper.cpp",
        role="offline speech-to-text for Aura",
        source="https://github.com/ggerganov/whisper.cpp",
        core_or_optional="optional speech",
        code_license="MIT upstream",
        model_license="Whisper model terms apply",
        commercial_use="verify selected model terms",
    ),
    ModelPolicy(
        id="ollama-model",
        component="Ollama local reasoning model",
        role="lyrics / producer reasoning",
        source="private Ollama service",
        core_or_optional="optional local intelligence",
        code_license="Ollama/runtime terms plus selected model terms",
        model_license="selected model specific",
        commercial_use="must be checked per selected model; Aura does not assume qwen/other model rights",
        notes="AURA_OLLAMA_MODEL is configurable so ESP can choose a commercially suitable model.",
    ),
)


def public_catalog() -> list[dict]:
    return [asdict(item) for item in CATALOG]


def policy_for(component_id: str) -> ModelPolicy:
    for item in CATALOG:
        if item.id == component_id:
            return item
    raise KeyError(component_id)
