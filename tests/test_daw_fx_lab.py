from __future__ import annotations

import pytest

from aura_music_studio.daw_fx_lab import ADVANCED_TYPES, PRESETS, STANDARD_TYPES, normalize_parameters, router
from aura_music_studio.effects import compile_ffmpeg_chain
from aura_music_studio.session import Effect


def test_fx_parameters_are_clamped_to_render_bounds():
    params = normalize_parameters(
        "compressor",
        {"threshold_db": -200, "ratio": 500, "attack_ms": -5, "release_ms": 99999, "unknown": 12},
    )
    assert params["threshold_db"] == -60
    assert params["ratio"] == 20
    assert params["attack_ms"] == 0.1
    assert params["release_ms"] == 3000
    assert "unknown" not in params


def test_fx_parameters_reject_non_numeric_values():
    with pytest.raises(ValueError, match="must be numeric"):
        normalize_parameters("reverb", {"mix": "very wet"})


def test_vocal_polish_compiles_to_real_ffmpeg_audio_chain():
    preset = PRESETS["vocal_polish"]
    chain = compile_ffmpeg_chain(
        [Effect(type=kind, parameters=normalize_parameters(kind, params)) for kind, params in preset["effects"]]
    )
    assert "highpass=" in chain
    assert "acompressor=" in chain
    assert "equalizer=" in chain


def test_every_exposed_fx_type_has_defaults_and_compiles_without_symbolic_output():
    for kind in sorted(STANDARD_TYPES | ADVANCED_TYPES):
        params = normalize_parameters(kind, {})
        chain = compile_ffmpeg_chain([Effect(type=kind, parameters=params)])
        assert isinstance(chain, str)
        assert chain


def test_fx_lab_routes_cover_real_chain_lifecycle():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/fx-lab" in paths
    assert "/projects/{project_name}/daw/fx/catalog" in paths
    assert "/projects/{project_name}/daw/tracks/{track_id}/fx" in paths
    assert "/projects/{project_name}/daw/tracks/{track_id}/fx/{effect_id}" in paths
    assert "/projects/{project_name}/daw/tracks/{track_id}/fx/order" in paths
    assert "/projects/{project_name}/daw/tracks/{track_id}/fx/preset" in paths
    assert "/daw" not in paths
