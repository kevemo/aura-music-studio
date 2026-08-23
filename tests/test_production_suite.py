from aura_music_studio.autotune import AutoTuneSettings
from aura_music_studio.effects import compile_ffmpeg_chain
from aura_music_studio.fx_presets import PRESETS
from aura_music_studio.instrument_catalog import DEFAULT_BY_GENRE, get_type
from aura_music_studio.mastering import PRESETS as MASTER_PRESETS
from aura_music_studio.plans import (
    ADVANCED_AUTOTUNE,
    ADVANCED_FX,
    ADVANCED_INSTRUMENT_SELECTOR,
    ALBUM_MASTERING,
    AUTOMIX,
    BASIC_AUTOTUNE,
    BASIC_FX,
    BASIC_MASTERING,
    BASIC_STEM_SPLITTER,
    BUILD_AROUND_UPLOAD,
    FULL_TRACK,
    STANDARD_AUTOTUNE,
    STANDARD_FX,
    STEM_SPLITTER,
    get_plan,
)
from aura_music_studio.session import Effect


def test_tier_progression_for_production_suite():
    free = get_plan("free")
    base = get_plan("base")
    pro = get_plan("pro")

    assert free.has(BASIC_FX)
    assert free.has(BASIC_AUTOTUNE)
    assert free.has(BASIC_MASTERING)
    assert not free.has(FULL_TRACK)
    assert not free.has(BUILD_AROUND_UPLOAD)

    assert base.has(FULL_TRACK)
    assert base.has(BUILD_AROUND_UPLOAD)
    assert base.has(STANDARD_FX)
    assert base.has(STANDARD_AUTOTUNE)
    assert base.has(AUTOMIX)
    assert base.has(BASIC_STEM_SPLITTER)
    assert not base.has(ADVANCED_FX)
    assert not base.has(STEM_SPLITTER)

    assert pro.has(ADVANCED_FX)
    assert pro.has(ADVANCED_AUTOTUNE)
    assert pro.has(ADVANCED_INSTRUMENT_SELECTOR)
    assert pro.has(STEM_SPLITTER)
    assert pro.has(ALBUM_MASTERING)


def test_automatic_genre_defaults_never_require_pro_instrument_types():
    for genre, selections in DEFAULT_BY_GENRE.items():
        assert selections, genre
        for family, type_id in selections:
            item = get_type(family, type_id)
            assert not item.pro_only, f"{genre} default unexpectedly requires Pro: {family}/{type_id}"


def test_fx_preset_ids_unique_and_effect_models_valid():
    ids = [preset.id for preset in PRESETS]
    assert len(ids) == len(set(ids))
    assert len(PRESETS) >= 20
    tiers = {preset.tier for preset in PRESETS}
    assert tiers == {"free", "base", "pro"}
    for preset in PRESETS:
        assert preset.effects
        for effect in preset.effects:
            assert isinstance(effect, Effect)


def test_expanded_dsp_compiles_common_pedal_and_vocal_processors():
    chain = compile_ffmpeg_chain([
        Effect(type="highpass", parameters={"hz": 80}),
        Effect(type="deesser", parameters={"frequency_hz": 6500, "reduction_db": 4}),
        Effect(type="chorus", parameters={"rate_hz": .7}),
        Effect(type="flanger", parameters={"rate_hz": .4}),
        Effect(type="phaser", parameters={"rate_hz": .3}),
        Effect(type="tremolo", parameters={"rate_hz": 4.5, "depth": .5}),
        Effect(type="doubler", parameters={"delay_ms": 20, "mix": .15}),
    ])
    assert "highpass=" in chain
    assert "equalizer=" in chain
    assert "chorus=" in chain
    assert "flanger=" in chain
    assert "aphaser=" in chain
    assert "tremolo=" in chain
    assert "aecho=" in chain


def test_mastering_catalog_has_broad_character_set():
    expected = {"universal", "streaming", "punch", "clarity", "warm", "natural", "spatial", "cinematic", "tape", "pop", "rock", "acoustic", "electronic", "hiphop", "karaoke"}
    assert expected.issubset(MASTER_PRESETS)
    assert len(MASTER_PRESETS) >= 20


def test_aura_tune_modes_and_limits_validate():
    natural = AutoTuneSettings(mode="natural", strength=.6, key="C", scale="major")
    custom = AutoTuneSettings(mode="custom", custom_pitch_classes=[0, 2, 4, 7, 9], strength=1.0)
    assert natural.mode == "natural"
    assert custom.mode == "custom"
    assert custom.custom_pitch_classes == [0, 2, 4, 7, 9]
