from __future__ import annotations

from dataclasses import dataclass

from .session import Effect


@dataclass(frozen=True)
class FxPreset:
    id: str
    name: str
    category: str
    description: str
    tier: str
    effects: tuple[Effect, ...]


def E(kind: str, **params) -> Effect:
    return Effect(type=kind, parameters=params)


PRESETS: tuple[FxPreset, ...] = (
    FxPreset("vocal_clean", "Clean Vocal", "vocal", "Tidy, controlled vocal with light polish.", "free", (
        E("highpass", hz=80), E("deesser", frequency_hz=6500, reduction_db=3), E("compressor", threshold_db=-20, ratio=2.2, attack_ms=18, release_ms=150), E("limiter", ceiling_db=-1.2))),
    FxPreset("vocal_natural_tune_chain", "Natural Pop Vocal", "vocal", "Open modern vocal chain for use with Aura Tune Natural.", "free", (
        E("highpass", hz=75), E("eq", low_db=-1, mid_db=1.2, mid_hz=2600, high_db=1.5), E("compressor", threshold_db=-18, ratio=2.5), E("reverb", predelay_ms=28, mix=.12))),
    FxPreset("vocal_warm", "Warm Vocal", "vocal", "Smooth intimate vocal with gentle saturation.", "base", (
        E("highpass", hz=70), E("saturation", drive=1.25), E("deesser", frequency_hz=6200, reduction_db=3.5), E("compressor", threshold_db=-20, ratio=2.1), E("reverb", predelay_ms=35, mix=.14))),
    FxPreset("vocal_radio", "Radio Vocal", "vocal", "Forward, compressed and bright lead vocal.", "base", (
        E("highpass", hz=90), E("eq", low_db=-1.5, mid_db=2.0, mid_hz=3000, high_db=2.0), E("compressor", threshold_db=-21, ratio=3.2, attack_ms=8, release_ms=110), E("deesser", frequency_hz=7000, reduction_db=4.5), E("limiter", ceiling_db=-1.0))),
    FxPreset("vocal_wide", "Wide Double Vocal", "vocal", "Double-tracked width and short ambience.", "base", (
        E("highpass", hz=85), E("compressor", threshold_db=-19, ratio=2.4), E("doubler", delay_ms=21, mix=.15, width=1.35), E("reverb", predelay_ms=24, mix=.11))),
    FxPreset("vocal_slap", "Slapback Vocal", "vocal", "Short vintage slap delay with a controlled front vocal.", "base", (
        E("highpass", hz=85), E("compressor", threshold_db=-20, ratio=2.4), E("delay", delay_ms=115, feedback=.14), E("saturation", drive=1.2))),
    FxPreset("vocal_air", "Air & Presence", "vocal", "Crisp high-end presence while controlling sibilance.", "pro", (
        E("highpass", hz=75), E("deesser", frequency_hz=7200, reduction_db=4), E("exciter", amount=2.8, frequency_hz=7600), E("compressor", threshold_db=-19, ratio=2.2), E("reverb", predelay_ms=30, mix=.1))),
    FxPreset("vocal_dream", "Dream Vocal", "vocal", "Wide chorus, delay and atmospheric ambience.", "pro", (
        E("highpass", hz=90), E("chorus", delay_ms=17, decay=.28, rate_hz=.55, depth=2.8), E("delay", delay_ms=360, feedback=.24), E("reverb", predelay_ms=55, mix=.28), E("stereo_width", width=1.45))),
    FxPreset("vocal_lofi", "Lo-Fi Vocal", "vocal", "Narrower, darker saturated vocal texture.", "pro", (
        E("highpass", hz=140), E("lowpass", hz=9000), E("saturation", drive=1.8), E("delay", delay_ms=180, feedback=.12))),

    FxPreset("guitar_clean", "Clean Combo", "guitar", "Bright clean amp-style guitar polish.", "free", (
        E("highpass", hz=70), E("eq", low_db=-1, mid_db=.7, mid_hz=1900, high_db=1.2), E("compressor", threshold_db=-20, ratio=2.0), E("reverb", predelay_ms=25, mix=.09))),
    FxPreset("guitar_acoustic", "Acoustic Polish", "guitar", "Controlled low end, presence and natural room.", "base", (
        E("highpass", hz=75), E("eq", low_db=-1.5, mid_db=.8, mid_hz=2200, high_db=1.1), E("compressor", threshold_db=-22, ratio=1.8), E("reverb", predelay_ms=18, mix=.1))),
    FxPreset("guitar_blues", "Blues Crunch", "guitar", "Touch-sensitive crunch with warm room tone.", "base", (
        E("highpass", hz=65), E("distortion", drive=1.8), E("eq", low_db=.4, mid_db=1.6, mid_hz=1600, high_db=-.4), E("reverb", predelay_ms=22, mix=.08))),
    FxPreset("guitar_chorus", "80s Clean Chorus", "guitar", "Wide clean chorus with controlled delay.", "base", (
        E("compressor", threshold_db=-20, ratio=2), E("chorus", delay_ms=18, decay=.35, rate_hz=.7, depth=2.5), E("delay", delay_ms=290, feedback=.16), E("reverb", predelay_ms=28, mix=.12))),
    FxPreset("guitar_arena", "Arena Lead", "guitar", "Saturated lead tone with width, delay and ambience.", "pro", (
        E("highpass", hz=75), E("distortion", drive=2.8), E("eq", low_db=-.5, mid_db=1.8, mid_hz=1400, high_db=1.0), E("delay", delay_ms=330, feedback=.24), E("reverb", predelay_ms=42, mix=.18), E("stereo_width", width=1.25))),
    FxPreset("guitar_ambient", "Ambient Guitar", "guitar", "Modulated wide guitar with long spatial tail.", "pro", (
        E("chorus", delay_ms=22, decay=.4, rate_hz=.35, depth=4), E("delay", delay_ms=480, feedback=.32), E("reverb", predelay_ms=65, mix=.34), E("stereo_width", width=1.55))),
    FxPreset("guitar_phaser", "Vintage Phaser", "guitar", "Slow sweeping phaser pedal sound.", "pro", (
        E("phaser", rate_hz=.34, decay=.45), E("saturation", drive=1.2), E("reverb", predelay_ms=20, mix=.08))),
    FxPreset("guitar_tremolo", "Tube Tremolo", "guitar", "Rhythmic amp-style tremolo.", "pro", (
        E("tremolo", rate_hz=4.2, depth=.55), E("saturation", drive=1.15), E("reverb", predelay_ms=18, mix=.08))),
    FxPreset("guitar_flanger", "Jet Flanger", "guitar", "Sweeping flanged guitar effect.", "pro", (
        E("flanger", delay_ms=2.2, depth_ms=3.2, feedback=18, rate_hz=.42), E("stereo_width", width=1.25))),

    FxPreset("bass_clean", "Clean Bass", "bass", "Controlled clean bass with preserved transient detail.", "free", (
        E("highpass", hz=35), E("lowpass", hz=9000), E("compressor", threshold_db=-20, ratio=3.0, attack_ms=24, release_ms=140), E("limiter", ceiling_db=-1.2))),
    FxPreset("bass_punch", "Punch Bass", "bass", "Dense modern bass with stronger mid definition.", "base", (
        E("highpass", hz=32), E("eq", low_db=1.2, mid_db=1.4, mid_hz=950, high_db=-.5), E("compressor", threshold_db=-22, ratio=4.0, attack_ms=18, release_ms=120), E("saturation", drive=1.25))),
    FxPreset("bass_grit", "Bass Grit", "bass", "Parallel-style gritty bass character.", "pro", (
        E("highpass", hz=35), E("distortion", drive=1.9), E("eq", low_db=.4, mid_db=1.6, mid_hz=1200, high_db=-1.2), E("compressor", threshold_db=-20, ratio=3.5))),
    FxPreset("bass_synthwide", "Wide Synth Bass", "bass", "Controlled synth-bass width above the sub foundation.", "pro", (
        E("lowpass", hz=12000), E("saturation", drive=1.3), E("chorus", delay_ms=14, decay=.22, rate_hz=.45, depth=1.8), E("stereo_width", width=1.18))),

    FxPreset("drums_tight", "Tight Drums", "drums", "Punchy controlled kit bus.", "base", (
        E("highpass", hz=28), E("compressor", threshold_db=-16, ratio=2.8, attack_ms=28, release_ms=130), E("eq", low_db=.6, mid_db=-.6, mid_hz=420, high_db=.7), E("limiter", ceiling_db=-1.0))),
    FxPreset("drums_room", "Live Room Drums", "drums", "Open acoustic room around the kit.", "pro", (
        E("compressor", threshold_db=-18, ratio=2.2, attack_ms=30, release_ms=180), E("reverb", predelay_ms=14, mix=.16), E("stereo_width", width=1.2))),
    FxPreset("drums_crush", "Parallel Crush", "drums", "Aggressive compressed drum texture.", "pro", (
        E("compressor", threshold_db=-28, ratio=8, attack_ms=4, release_ms=90), E("saturation", drive=1.45), E("gain", db=-2))),

    FxPreset("creative_lofi", "Lo-Fi Tape", "creative", "Dark saturated lo-fi texture.", "base", (
        E("highpass", hz=80), E("lowpass", hz=11000), E("saturation", drive=1.5), E("tremolo", rate_hz=.7, depth=.08))),
    FxPreset("creative_wide", "Wide Shimmer", "creative", "Bright width and atmospheric tail.", "pro", (
        E("exciter", amount=2.4, frequency_hz=7800), E("chorus", delay_ms=20, decay=.35, rate_hz=.3, depth=3), E("reverb", predelay_ms=60, mix=.28), E("stereo_width", width=1.55))),
    FxPreset("creative_phone", "Telephone", "creative", "Band-limited phone/radio tone.", "base", (
        E("highpass", hz=320), E("lowpass", hz=3600), E("compressor", threshold_db=-24, ratio=4), E("saturation", drive=1.25))),
    FxPreset("creative_space", "Deep Space", "creative", "Slow modulation, delay and wide ambience.", "pro", (
        E("phaser", rate_hz=.18, decay=.5), E("delay", delay_ms=620, feedback=.38), E("reverb", predelay_ms=80, mix=.38), E("stereo_width", width=1.7))),
)


def get_preset(preset_id: str) -> FxPreset:
    for preset in PRESETS:
        if preset.id == preset_id:
            return preset
    raise KeyError(preset_id)


def public_presets() -> list[dict]:
    return [
        {
            "id": p.id,
            "name": p.name,
            "category": p.category,
            "description": p.description,
            "tier": p.tier,
            "effect_count": len(p.effects),
            "effects": [e.model_dump() for e in p.effects],
        }
        for p in PRESETS
    ]
