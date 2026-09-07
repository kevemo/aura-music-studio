from __future__ import annotations

from copy import deepcopy

RECORDED_AUDIO_ROLES = frozenset(
    {
        "vocals",
        "backing_vocals",
        "drums",
        "bass",
        "guitar",
        "piano",
        "keyboard",
        "strings",
        "percussion",
        "brass",
        "woodwinds",
        "other",
    }
)

RESTORATION_PRESETS = {
    "electrical_hum_50hz": {
        "title": "Electrical Hum Cleanup · 50 Hz",
        "roles": set(RECORDED_AUDIO_ROLES),
        "effects": [
            ("highpass", {"hz": 30}),
            ("notch", {"frequency_hz": 50, "width_octaves": 0.04}),
            ("notch", {"frequency_hz": 100, "width_octaves": 0.04}),
            ("notch", {"frequency_hz": 150, "width_octaves": 0.04}),
        ],
    },
    "electrical_hum_60hz": {
        "title": "Electrical Hum Cleanup · 60 Hz",
        "roles": set(RECORDED_AUDIO_ROLES),
        "effects": [
            ("highpass", {"hz": 30}),
            ("notch", {"frequency_hz": 60, "width_octaves": 0.04}),
            ("notch", {"frequency_hz": 120, "width_octaves": 0.04}),
            ("notch", {"frequency_hz": 180, "width_octaves": 0.04}),
        ],
    },
    "voice_noise_floor_cleanup": {
        "title": "Voice Noise-Floor Cleanup",
        "roles": {"vocals", "backing_vocals", "other"},
        "effects": [
            ("highpass", {"hz": 75}),
            (
                "expander",
                {
                    "threshold_db": -44,
                    "ratio": 2.2,
                    "attack_ms": 18,
                    "release_ms": 240,
                    "range_db": -16,
                },
            ),
            ("deesser", {"frequency_hz": 6500, "reduction_db": 3.5}),
            ("low_shelf", {"gain_db": -1.0, "frequency_hz": 180, "width": 0.7}),
            ("high_shelf", {"gain_db": 0.75, "frequency_hz": 9000, "width": 0.6}),
        ],
    },
}

_installed = False


def install_audio_restoration_presets() -> None:
    """Register bounded, executable restoration chains in the canonical DAW FX Lab.

    The chains are composed only from effect types already compiled by
    ``effects.compile_ffmpeg_chain``. They never accept arbitrary FFmpeg text or external plugin
    binaries. Existing preset ids are treated as owned contracts and are never overwritten with
    different semantics.
    """
    global _installed
    if _installed:
        return

    from . import daw_fx_lab

    for preset_id, preset in RESTORATION_PRESETS.items():
        existing = daw_fx_lab.PRESETS.get(preset_id)
        if existing is not None and existing != preset:
            raise RuntimeError(
                f"Audio restoration preset collision for {preset_id!r}; refusing to overwrite "
                "another preset contract"
            )

        for effect_type, _ in preset["effects"]:
            if effect_type not in daw_fx_lab.DEFAULTS or effect_type not in daw_fx_lab.BOUNDS:
                raise RuntimeError(
                    f"Audio restoration preset {preset_id!r} depends on unregistered FX "
                    f"{effect_type!r}"
                )

        daw_fx_lab.PRESETS[preset_id] = deepcopy(preset)

    _installed = True


__all__ = [
    "RECORDED_AUDIO_ROLES",
    "RESTORATION_PRESETS",
    "install_audio_restoration_presets",
]
