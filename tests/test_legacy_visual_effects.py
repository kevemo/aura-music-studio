from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from aura_music_studio import visual_effect_catalogue as catalogue
from aura_music_studio.legacy_visual_effects import (
    LEGACY_VISUAL_EFFECT_IDS,
    apply_legacy_visual_effect,
    install_legacy_visual_effects,
    legacy_visual_effect_provenance,
)
from aura_music_studio.visual_effect_catalogue_hardening import compile_effect_graph_hardened


def setup_module() -> None:
    install_legacy_visual_effects()


def _brightest_column(image: Image.Image) -> int:
    array = np.asarray(image.convert("RGB"), dtype=np.float32)
    return int(array.mean(axis=(0, 2)).argmax())


def test_recovered_effects_register_without_arbitrary_execution_surfaces():
    install_legacy_visual_effects()
    assert LEGACY_VISUAL_EFFECT_IDS <= set(catalogue.EFFECTS)
    assert {catalogue.EFFECTS[effect_id].runtime for effect_id in LEGACY_VISUAL_EFFECT_IDS} == {"editor_effect"}
    runtime_types = {catalogue.EFFECTS[effect_id].runtime_type for effect_id in LEGACY_VISUAL_EFFECT_IDS}
    assert runtime_types == {"glow", "bloom", "light_sweep"}
    assert not any(
        unsafe in runtime_type.lower()
        for runtime_type in runtime_types
        for unsafe in ("shader", "javascript", "python", "eval", "shell", "ffmpeg", "plugin")
    )


def test_glow_executes_real_pixels_and_preserves_alpha():
    source = Image.new("RGBA", (9, 3), (0, 0, 0, 128))
    source.putpixel((4, 1), (255, 255, 255, 128))
    rendered = apply_legacy_visual_effect(
        source,
        {
            "type": "glow",
            "enabled": True,
            "mix": 1.0,
            "parameters": {"radius": 2.0, "intensity": 1.0},
        },
        0.0,
    )
    assert rendered.getpixel((3, 1))[0] > 0
    assert rendered.getpixel((5, 1))[0] > 0
    assert rendered.getchannel("A").getextrema() == (128, 128)


def test_bloom_thresholds_highlights_and_changes_neighboring_pixels():
    source = Image.new("RGBA", (11, 3), (12, 12, 12, 255))
    source.putpixel((5, 1), (255, 245, 220, 255))
    rendered = apply_legacy_visual_effect(
        source,
        {
            "type": "bloom",
            "enabled": True,
            "mix": 1.0,
            "parameters": {"threshold": 0.6, "radius": 2.5, "intensity": 1.5},
        },
        0.0,
    )
    assert rendered.getpixel((4, 1))[0] > source.getpixel((4, 1))[0]
    assert rendered.getpixel((6, 1))[0] > source.getpixel((6, 1))[0]


def test_shimmer_executes_bounded_light_sweep_pixels():
    source = Image.new("RGBA", (31, 5), (24, 24, 24, 255))
    rendered = apply_legacy_visual_effect(
        source,
        {
            "type": "light_sweep",
            "enabled": True,
            "mix": 1.0,
            "parameters": {
                "position": 0.5,
                "width": 0.12,
                "angle": 0.0,
                "intensity": 0.8,
                "color": "#ffffff",
            },
        },
        0.0,
    )
    centre = rendered.getpixel((15, 2))[0]
    edge = rendered.getpixel((0, 2))[0]
    assert centre > edge
    assert centre > source.getpixel((15, 2))[0]


def test_shimmer_uses_existing_numeric_keyframe_runtime_for_motion():
    source = Image.new("RGBA", (41, 5), (16, 16, 16, 255))
    effect = {
        "type": "light_sweep",
        "enabled": True,
        "mix": 1.0,
        "parameters": {
            "position": 0.2,
            "width": 0.08,
            "angle": 0.0,
            "intensity": 1.0,
            "color": "#ffffff",
        },
        "keyframes": {
            "position": [
                {"time": 0.0, "value": 0.2, "interpolation": "linear"},
                {"time": 1.0, "value": 0.8, "interpolation": "linear"},
            ]
        },
    }
    left = apply_legacy_visual_effect(source, effect, 0.0)
    right = apply_legacy_visual_effect(source, effect, 1.0)
    assert _brightest_column(left) < _brightest_column(right)


def test_hardened_effect_creator_resolves_rewritten_legacy_visual_concepts():
    glow = compile_effect_graph_hardened("Add a soft glow", "image")
    bloom = compile_effect_graph_hardened("Add bloom", "image")
    shimmer = compile_effect_graph_hardened("Add a shimmer", "image")
    assert glow["nodes"][0]["effect_id"] == "image.light.glow"
    assert bloom["nodes"][0]["effect_id"] == "image.light.bloom"
    assert shimmer["nodes"][0]["effect_id"] == "image.light.shimmer"
    assert all(graph["executable"] is True for graph in (glow, bloom, shimmer))


def test_recovered_effect_parameter_contracts_fail_closed():
    with pytest.raises(ValueError, match="Unsupported parameter"):
        catalogue.normalize_effect_parameters("image.light.glow", {"radius": 4.0, "shader": "custom"})
    with pytest.raises(ValueError, match="between"):
        catalogue.normalize_effect_parameters("image.light.bloom", {"threshold": 1.5})
    with pytest.raises(ValueError, match="#RRGGBB"):
        catalogue.normalize_effect_parameters("image.light.shimmer", {"color": "url(file:///tmp/x)"})


def test_legacy_provenance_is_reference_only_and_does_not_expose_source_code():
    glow_sources = legacy_visual_effect_provenance("image.light.glow")
    assert glow_sources
    assert any("aura.model.glow.shader.js" in source for source in glow_sources)
    assert all("eval(" not in source and "exec(" not in source for source in glow_sources)
