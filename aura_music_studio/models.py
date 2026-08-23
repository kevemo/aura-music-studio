from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Section(BaseModel):
    name: str
    start_measure: int
    end_measure: int
    energy: float = Field(default=0.7, ge=0.0, le=1.0)


class RendererConfig(BaseModel):
    # Independence-first: self-hosted engines are preferred. Public Spaces are opt-in rather than
    # an automatic dependency, and authenticated hosted providers are fallbacks only when configured.
    preferred: list[str] = Field(default_factory=lambda: [
        "acestep_api", "local_acestep", "muser", "yue", "deapi", "eleven_music", "mureka"
    ])
    model: str = "acestep-v15-xl-turbo"
    cover_strength: float = Field(default=0.78, ge=0.0, le=1.0)
    duration_limit_seconds: int = 300
    max_attempts_per_host: int = 3
    retry_seconds: int = 45
    quality_retries: int = 2
    minimum_quality_score: float = Field(default=0.55, ge=0.0, le=1.0)
    require_real_audio: bool = True
    allow_symbolic_guide_as_final: bool = False


class MixConfig(BaseModel):
    mastering_preset: Literal["streaming", "pop", "rock", "acoustic", "ballad", "electronic", "hiphop", "cinematic", "karaoke"] = "streaming"
    mastering_reference: str | None = None
    target_lufs: float = -14.0
    true_peak_db: float = -1.0
    vocal_space: bool = True
    backing_vocals_db: float = -8.0
    lead_guitar_db: float = -9.0
    separation_mode: Literal["two_stems", "six_stems"] = "six_stems"
    export_mp3: bool = True
    export_wav: bool = True
    export_flac: bool = False
    export_stems: bool = True
    export_translation_report: bool = True


class ProductionSpec(BaseModel):
    realistic_drums: bool = True
    fingered_bass: bool = True
    acoustic_guitar: bool = True
    electric_rhythm_guitars: bool = True
    piano: bool = True
    synths: bool = True
    strings: bool = True
    percussion: bool = True
    original_single_note_countermelody: bool = True
    wordless_backing_harmonies: bool = True
    leave_center_for_lead_vocal: bool = True


class ProjectManifest(BaseModel):
    project_name: str
    title: str
    mode: Literal["original", "cover", "remix", "backing_track"] = "original"
    rights_confirmed: bool = False
    tempo_bpm: float | None = None
    meter: str = "4/4"
    key: str | None = None
    tuning: str | None = None
    total_measures: int | None = None
    target_duration_seconds: int | None = Field(default=None, ge=3, le=600)
    sections: list[Section] = Field(default_factory=list)
    reference_audio: str | None = None
    score_file: str | None = None
    midi_file: str | None = None
    musicxml_file: str | None = None
    lyrics_file: str | None = None
    guide_file: str | None = None
    guide_command: str | None = None
    prompt: str = ""
    negative_prompt: str = ""
    project_dna: dict = Field(default_factory=dict)
    renderer: RendererConfig = Field(default_factory=RendererConfig)
    production: ProductionSpec = Field(default_factory=ProductionSpec)
    mix: MixConfig = Field(default_factory=MixConfig)

    @model_validator(mode="after")
    def validate_rights(self):
        if self.mode in {"cover", "remix", "backing_track"} and not self.rights_confirmed:
            raise ValueError(
                "rights_confirmed must be true for cover/remix/backing-track projects. Aura only processes source material you have the right to use."
            )
        if self.renderer.require_real_audio and self.renderer.allow_symbolic_guide_as_final:
            raise ValueError("Real-audio mode cannot allow a symbolic/MIDI guide to become the final master.")
        return self


class AnalysisResult(BaseModel):
    tempo_bpm: float | None = None
    key: str | None = None
    duration_seconds: float | None = None
    sample_rate: int | None = None
    beats: list[float] = Field(default_factory=list)
    source: str = "manifest"
    notes: list[str] = Field(default_factory=list)


class ArrangementPlan(BaseModel):
    project_name: str
    tempo_bpm: float
    key: str | None = None
    meter: str = "4/4"
    sections: list[Section] = Field(default_factory=list)
    instrument_brief: dict[str, str] = Field(default_factory=dict)
    backing_vocal_brief: str = ""
    countermelody_brief: str = ""
    render_prompt: str = ""
    negative_prompt: str = ""


class RenderResult(BaseModel):
    renderer: str
    audio_path: Path
    audio_origin: Literal["neural", "recorded", "hybrid", "symbolic_guide"] = "neural"
    is_final_quality: bool = True
    metadata: dict = Field(default_factory=dict)
