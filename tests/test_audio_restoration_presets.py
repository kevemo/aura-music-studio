from __future__ import annotations

from aura_music_studio import daw_fx_lab
from aura_music_studio.audio_fx_expansion import (
    ADVANCED_EXPANDED_TYPES,
    STANDARD_EXPANDED_TYPES,
    install_audio_fx_expansion,
)
from aura_music_studio.audio_restoration_presets import (
    RESTORATION_PRESETS,
    install_audio_restoration_presets,
)
from aura_music_studio.effects import compile_ffmpeg_chain
from aura_music_studio.session import Effect


def _compile_preset(preset_id: str) -> str:
    preset = daw_fx_lab.PRESETS[preset_id]
    effects = [
        Effect(type=kind, parameters=daw_fx_lab.normalize_parameters(kind, params))
        for kind, params in preset["effects"]
    ]
    return compile_ffmpeg_chain(effects)


def test_audio_fx_expansion_registers_only_executable_types():
    install_audio_fx_expansion()

    assert STANDARD_EXPANDED_TYPES <= daw_fx_lab.STANDARD_TYPES
    assert ADVANCED_EXPANDED_TYPES <= daw_fx_lab.ADVANCED_TYPES

    for kind in sorted(STANDARD_EXPANDED_TYPES | ADVANCED_EXPANDED_TYPES):
        params = daw_fx_lab.normalize_parameters(kind, {})
        chain = compile_ffmpeg_chain([Effect(type=kind, parameters=params)])
        assert chain


def test_expanded_fx_bounds_clamp_before_real_audio_compilation():
    install_audio_fx_expansion()

    notch = daw_fx_lab.normalize_parameters(
        "notch", {"frequency_hz": 99999, "width_octaves": -3}
    )
    assert notch == {"frequency_hz": 20000.0, "width_octaves": 0.01}

    expander = daw_fx_lab.normalize_parameters(
        "expander",
        {
            "threshold_db": -999,
            "ratio": 500,
            "attack_ms": -2,
            "release_ms": 99999,
            "range_db": -999,
        },
    )
    assert expander == {
        "threshold_db": -80.0,
        "ratio": 20.0,
        "attack_ms": 0.1,
        "release_ms": 3000.0,
        "range_db": -60.0,
    }
    assert "agate=" in compile_ffmpeg_chain([Effect(type="expander", parameters=expander)])


def test_50hz_hum_cleanup_is_a_real_harmonic_notch_chain():
    install_audio_fx_expansion()
    install_audio_restoration_presets()

    chain = _compile_preset("electrical_hum_50hz")
    assert "highpass=" in chain
    assert chain.count("bandreject=") == 3
    assert "bandreject=f=50.00" in chain
    assert "bandreject=f=100.00" in chain
    assert "bandreject=f=150.00" in chain


def test_60hz_hum_cleanup_is_a_real_harmonic_notch_chain():
    install_audio_fx_expansion()
    install_audio_restoration_presets()

    chain = _compile_preset("electrical_hum_60hz")
    assert "highpass=" in chain
    assert chain.count("bandreject=") == 3
    assert "bandreject=f=60.00" in chain
    assert "bandreject=f=120.00" in chain
    assert "bandreject=f=180.00" in chain


def test_voice_cleanup_is_truthfully_pro_tier_and_executable():
    install_audio_fx_expansion()
    install_audio_restoration_presets()

    preset = daw_fx_lab.PRESETS["voice_noise_floor_cleanup"]
    kinds = {kind for kind, _ in preset["effects"]}
    assert "expander" in kinds
    assert "expander" in daw_fx_lab.ADVANCED_TYPES

    chain = _compile_preset("voice_noise_floor_cleanup")
    assert "highpass=" in chain
    assert "agate=" in chain
    assert "equalizer=" in chain
    assert "bass=" in chain
    assert "treble=" in chain


def test_restoration_presets_do_not_overwrite_existing_contracts():
    install_audio_fx_expansion()
    install_audio_restoration_presets()

    for preset_id, preset in RESTORATION_PRESETS.items():
        assert daw_fx_lab.PRESETS[preset_id] == preset
