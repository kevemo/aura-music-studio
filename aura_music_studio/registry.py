from __future__ import annotations

import importlib.util
import os
import shutil
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Engine:
    id: str
    name: str
    kind: str
    capabilities: frozenset[str]
    quality_rank: int = 50
    reference_control: int = 0
    local: bool = False
    env_key: str | None = None
    command_env: str | None = None
    module: str | None = None
    notes: str = ""

    def available(self) -> bool:
        if self.env_key and not os.getenv(self.env_key):
            return False
        if self.command_env and not os.getenv(self.command_env):
            return False
        if self.module and importlib.util.find_spec(self.module) is None:
            return False
        return True


ENGINES: dict[str, Engine] = {
    "acestep_space": Engine(
        id="acestep_space", name="ACE-Step 1.5 Hosted", kind="music",
        capabilities=frozenset({
            "prompt_song", "lyrics_song", "instrumental", "cover", "repaint", "reference_audio",
            "metadata_control", "backing_track", "vocal2bgm", "quality_generation"
        }),
        quality_rank=92, reference_control=90,
        notes="Public hosted ACE-Step; availability depends on GPU queue.",
    ),
    "local_acestep": Engine(
        id="local_acestep", name="ACE-Step 1.5 Local", kind="music",
        capabilities=frozenset({
            "prompt_song", "lyrics_song", "instrumental", "cover", "repaint", "reference_audio",
            "extract", "add_track", "complete", "vocal2bgm", "metadata_control", "backing_track",
            "lora", "audio_understanding", "lyric_timestamps", "multitrack"
        }),
        quality_rank=98, reference_control=100, local=True, command_env="AURA_LOCAL_RENDER_CMD",
    ),
    "deapi": Engine(
        id="deapi", name="deAPI ACE-Step", kind="music",
        capabilities=frozenset({"prompt_song", "lyrics_song", "instrumental", "metadata_control"}),
        quality_rank=94, env_key="DEAPI_API_KEY",
    ),
    "muser": Engine(
        id="muser", name="The Muser", kind="orchestrator",
        capabilities=frozenset({
            "prompt_song", "lyrics_song", "instrumental", "notation", "best_of_n", "quality_generation",
            "backing_track", "voice_synthesis", "voice_convert", "audio_to_midi", "mix", "effects"
        }),
        quality_rank=96, reference_control=95, local=True, command_env="AURA_MUSER_CMD",
    ),
    "yue": Engine(
        id="yue", name="YuE", kind="music",
        capabilities=frozenset({
            "lyrics_song", "reference_audio", "voice_style_reference", "continuation", "lora", "multilingual_vocals"
        }),
        quality_rank=90, reference_control=75, local=True, command_env="AURA_YUE_CMD",
    ),
    "mureka": Engine(
        id="mureka", name="Mureka API", kind="music",
        capabilities=frozenset({
            "prompt_song", "lyrics_song", "instrumental", "lyrics_generate", "extend", "remix", "transcription",
            "stem_separation", "complementary_track", "soundtrack", "finetune"
        }),
        quality_rank=91, reference_control=75, env_key="MUREKA_API_KEY",
    ),
    "eleven_music": Engine(
        id="eleven_music", name="Eleven Music", kind="music",
        capabilities=frozenset({
            "prompt_song", "lyrics_song", "instrumental", "reference_audio", "section_edit", "multilingual_vocals"
        }),
        quality_rank=93, reference_control=70, env_key="ELEVENLABS_API_KEY",
    ),
    "diffsinger": Engine(
        id="diffsinger", name="DiffSinger", kind="voice",
        capabilities=frozenset({"voice_synthesis", "singing_synthesis", "harmony_vocals", "pitch_control", "expression_control"}),
        quality_rank=88, reference_control=100, local=True, command_env="AURA_DIFFSINGER_CMD",
    ),
    "seed_vc": Engine(
        id="seed_vc", name="Seed-VC", kind="voice",
        capabilities=frozenset({"voice_convert", "singing_voice_convert", "zero_shot_voice", "style_voice_convert"}),
        quality_rank=90, reference_control=95, local=True, command_env="AURA_SEEDVC_CMD",
    ),
    "applio": Engine(
        id="applio", name="Applio / RVC", kind="voice",
        capabilities=frozenset({"voice_convert", "singing_voice_convert", "voice_finetune"}),
        quality_rank=88, reference_control=98, local=True, command_env="AURA_RVC_CMD",
    ),
    "audio_separator": Engine(
        id="audio_separator", name="Audio Separator / UVR RoFormer", kind="separation",
        capabilities=frozenset({"stem_separation", "vocal_isolation", "karaoke", "dereverb", "denoise"}),
        quality_rank=97, local=True, module="audio_separator",
    ),
    "demucs": Engine(
        id="demucs", name="Demucs", kind="separation",
        capabilities=frozenset({"stem_separation", "six_stems"}),
        quality_rank=85, local=True, module="demucs",
    ),
    "basic_pitch": Engine(
        id="basic_pitch", name="Spotify Basic Pitch", kind="transcription",
        capabilities=frozenset({"audio_to_midi", "pitch_bend_transcription"}),
        quality_rank=84, local=True, module="basic_pitch",
    ),
    "matchering": Engine(
        id="matchering", name="Matchering", kind="mastering",
        capabilities=frozenset({"reference_mastering"}),
        quality_rank=90, local=True, module="matchering",
    ),
}


def available_engines() -> list[Engine]:
    return [engine for engine in ENGINES.values() if engine.available()]


def recommend_engine(
    capability: str,
    *,
    needs_reference: bool = False,
    prefer_local: bool = False,
    allowed: list[str] | None = None,
) -> list[Engine]:
    pool = [e for e in ENGINES.values() if capability in e.capabilities]
    if allowed:
        pool = [e for e in pool if e.id in allowed]
    pool = [e for e in pool if e.available()]

    def score(e: Engine) -> tuple[int, int, int]:
        locality = 20 if prefer_local and e.local else 0
        ref = e.reference_control if needs_reference else 0
        return (e.quality_rank + locality, ref, int(e.local))

    return sorted(pool, key=score, reverse=True)


def capability_report() -> dict:
    capabilities = sorted({c for e in ENGINES.values() for c in e.capabilities})
    return {
        "engines": {
            k: {
                "name": e.name,
                "kind": e.kind,
                "available": e.available(),
                "quality_rank": e.quality_rank,
                "reference_control": e.reference_control,
                "local": e.local,
                "capabilities": sorted(e.capabilities),
                "notes": e.notes,
            }
            for k, e in ENGINES.items()
        },
        "capabilities": {
            cap: [e.id for e in recommend_engine(cap)] for cap in capabilities
        },
    }
