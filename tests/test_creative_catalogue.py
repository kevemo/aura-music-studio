from __future__ import annotations

import pytest

from aura_music_studio.creative_catalogue import (
    CATALOGUE_ITEMS,
    get_catalogue_item,
    public_catalogue,
    search_catalogue,
)


def test_catalogue_ids_are_stable_unique_and_prices_follow_effect_bands():
    ids = [item.id for item in CATALOGUE_ITEMS]
    assert len(ids) == len(set(ids))
    assert all(item.id.startswith(f"{item.studio}.") for item in CATALOGUE_ITEMS)
    expected_prices = {"core": 0, "silver": 200, "gold": 500}
    assert all(item.ccc_price == expected_prices[item.entitlement] for item in CATALOGUE_ITEMS)


def test_search_filters_by_query_studio_and_entitlement():
    assert get_catalogue_item("music.fx.compressor") in search_catalogue("dynamics", studio="music")
    silver = search_catalogue(studio="music", entitlement="silver")
    assert silver
    assert all(item.entitlement == "silver" for item in silver)
    assert search_catalogue("not-a-real-effect") == []


def test_runtime_parameters_are_bounded_before_compilation():
    compressor = get_catalogue_item("music.fx.compressor")
    effect = compressor.build_effect(
        {
            "threshold_db": -999,
            "ratio": 999,
            "attack_ms": -10,
            "release_ms": 99999,
        }
    )
    assert effect.parameters == {
        "threshold_db": -60.0,
        "ratio": 20.0,
        "attack_ms": 0.1,
        "release_ms": 2000.0,
    }
    chain = compressor.preview_filter_chain(effect.parameters)
    assert chain.startswith("acompressor=")
    assert "threshold=-60.0dB" in chain
    assert "ratio=20.0" in chain


def test_unknown_parameters_are_rejected_instead_of_silently_ignored():
    gain = get_catalogue_item("music.fx.gain")
    with pytest.raises(ValueError, match="Unsupported parameters"):
        gain.build_effect({"db": 2, "secret_knob": 1})


def test_catalogue_entries_are_backed_by_non_empty_real_renderer_chains():
    for item in CATALOGUE_ITEMS:
        assert item.status == "BACKEND_FUNCTIONAL"
        assert item.runtime == "ffmpeg_audio"
        assert item.preview_filter_chain(), item.id


def test_public_catalogue_serializes_parameter_contracts_without_runtime_objects():
    rows = public_catalogue("stereo", studio="music")
    assert rows
    row = next(item for item in rows if item["id"] == "music.fx.stereo_width")
    assert row["ccc_price"] == 500
    assert row["entitlement"] == "gold"
    assert row["parameters"][0]["id"] == "width"
    assert row["parameters"][0]["minimum"] == 0.0
    assert row["parameters"][0]["maximum"] == 2.0


def test_unknown_catalogue_id_is_explicit():
    with pytest.raises(KeyError, match="Unknown creative catalogue item"):
        get_catalogue_item("video.fx.does-not-exist")
