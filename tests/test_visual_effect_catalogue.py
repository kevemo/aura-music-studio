from __future__ import annotations

import math

import pytest
from fastapi import Request
from PIL import Image

from aura_music_studio import visual_effect_catalogue as base
from aura_music_studio.professional_editor_security_overlay import (
    install_professional_editor_patch_guard,
    professional_editor_router,
)
from aura_music_studio.professional_image_compositor import _apply_effect
from aura_music_studio.visual_effect_catalogue_hardening import (
    _VIDEO_COLOUR_IDS,
    _validate_target_media,
    compile_effect_graph_hardened,
    install_visual_effect_catalogue_hardening,
    router as hardening_router,
    save_visual_effect_system_hardened,
)


class _Plan:
    id = "pro"

    def has(self, _feature: str) -> bool:
        return True


class _Member:
    plan = _Plan()
    user = {"display_name": "Visual Test"}
    user_id = "visual-test-user"


def _request() -> Request:
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    request.state.member = _Member()
    return request


def test_catalogue_contains_only_explicit_bounded_runtimes():
    assert base.EFFECTS
    assert all(spec.runtime in {"editor_effect", "item_color"} for spec in base.EFFECTS.values())
    assert all(spec.parameters is not None for spec in base.EFFECTS.values())
    assert not any("ffmpeg" in spec.runtime_type.lower() for spec in base.EFFECTS.values())
    assert not any("shell" in spec.runtime_type.lower() for spec in base.EFFECTS.values())


def test_image_catalogue_runtime_executes_real_pixels():
    spec = base.EFFECTS["image.filter.invert"]
    source = Image.new("RGBA", (2, 1), (255, 0, 0, 255))
    source.putpixel((1, 0), (0, 0, 255, 255))
    rendered = _apply_effect(
        source,
        {"type": spec.runtime_type, "enabled": True, "mix": 1.0, "parameters": {}},
        0.0,
    )
    assert rendered.getpixel((0, 0)) == (0, 255, 255, 255)
    assert rendered.getpixel((1, 0)) == (255, 255, 0, 255)


def test_parameter_validation_rejects_unknown_nonfinite_and_out_of_range_values():
    with pytest.raises(ValueError, match="Unsupported parameter"):
        base.normalize_effect_parameters("image.blur.gaussian", {"radius": 2.0, "command": "-vf evil"})
    with pytest.raises(ValueError, match="finite"):
        base.normalize_effect_parameters("image.blur.gaussian", {"radius": math.nan})
    with pytest.raises(ValueError, match="between"):
        base.normalize_effect_parameters("image.blur.gaussian", {"radius": 251.0})


def test_duotone_rejects_unbounded_colour_strings():
    with pytest.raises(ValueError, match="#RRGGBB"):
        base.normalize_effect_parameters(
            "image.filter.duotone",
            {"shadow": "url(file:///etc/passwd)", "highlight": "#ffffff"},
        )


def test_hardened_prompt_compiler_resolves_shared_colour_words_by_media_kind():
    image = compile_effect_graph_hardened("Increase the contrast", "image")
    video = compile_effect_graph_hardened("Increase the contrast", "video")
    assert image["nodes"][0]["effect_id"] == "image.color.contrast"
    assert video["nodes"][0]["effect_id"] == "video.color.contrast"
    assert video["nodes"][0]["runtime"] == "item_color"


def test_hardened_prompt_compiler_fails_closed_on_ambiguous_or_unknown_requests():
    with pytest.raises(ValueError, match="ambiguous"):
        compile_effect_graph_hardened("Add blur and sepia", "image")
    with pytest.raises(ValueError, match="No executable visual effect"):
        compile_effect_graph_hardened("Run my custom plugin from /tmp", "image")


def test_video_colour_catalogue_does_not_advertise_unexecuted_mix_or_keyframes():
    install_visual_effect_catalogue_hardening()
    assert all(base.EFFECTS[effect_id].supports_keyframes is False for effect_id in _VIDEO_COLOUR_IDS)
    with pytest.raises(ValueError, match="do not support effect mix"):
        compile_effect_graph_hardened("Increase video exposure", "video", mix=0.5)
    with pytest.raises(ValueError, match="does not support keyframes"):
        compile_effect_graph_hardened(
            "Increase video exposure",
            "video",
            keyframes={"value": [{"time": 0.0, "value": 0.1}]},
        )


def test_target_media_contract_rejects_cross_media_item_and_track_ids():
    state = {
        "branch": {
            "items": [
                {"id": "image-1", "kind": "image_layer"},
                {"id": "video-1", "kind": "video_clip"},
            ],
            "tracks": [
                {"id": "image-track", "kind": "image"},
                {"id": "video-track", "kind": "video"},
            ],
        }
    }
    image_spec = base.EFFECTS["image.filter.invert"]
    video_spec = base.EFFECTS["video.color.exposure"]
    assert _validate_target_media(state, "item", "image-1", image_spec)["kind"] == "image_layer"
    assert _validate_target_media(state, "item", "video-1", video_spec)["kind"] == "video_clip"
    assert _validate_target_media(state, "track", "image-track", image_spec)["kind"] == "image"
    with pytest.raises(ValueError, match="requires a video_clip"):
        _validate_target_media(state, "item", "image-1", video_spec)
    with pytest.raises(ValueError, match="requires a image_layer"):
        _validate_target_media(state, "item", "video-1", image_spec)
    with pytest.raises(ValueError, match="requires a image"):
        _validate_target_media(state, "track", "video-track", image_spec)


def test_saved_system_rebuilds_untrusted_runtime_fields_from_catalogue(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "_project", lambda _name: tmp_path)
    graph = compile_effect_graph_hardened("Add cinematic grade", "image")
    graph["nodes"][0]["runtime"] = "shell"
    graph["nodes"][0]["runtime_type"] = "evil --args"
    graph["arbitrary_code"] = True
    graph["arbitrary_ffmpeg_arguments"] = True
    response = save_visual_effect_system_hardened(
        "visual-project",
        base.SaveEffectSystemRequest(name="Safe cinematic", graph=graph),
        _request(),
    )
    saved = response["system"]["graph"]
    node = saved["nodes"][0]
    assert node["runtime"] == "editor_effect"
    assert node["runtime_type"] == "image.filter.cinematic"
    assert saved["arbitrary_code"] is False
    assert saved["arbitrary_ffmpeg_arguments"] is False
    assert saved["shell_execution"] is False
    assert (tmp_path / "visual_effect_systems.json").is_file()


def test_hardened_routes_precede_legacy_duplicate_signatures_for_final_deduplication():
    install_professional_editor_patch_guard()
    guarded = {
        (getattr(route, "path", None), frozenset(getattr(route, "methods", set()))): route.endpoint
        for route in hardening_router.routes
    }
    for signature, endpoint in guarded.items():
        matches = [
            route
            for route in professional_editor_router.routes
            if (getattr(route, "path", None), frozenset(getattr(route, "methods", set()))) == signature
        ]
        assert matches
        assert matches[0].endpoint is endpoint
