from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest
from PIL import Image, ImageChops, ImageStat

from aura_music_studio.professional_editor import EditorEffect, EditorKeyframe, ProfessionalEditorStore
from aura_music_studio.professional_editor_renderer import EditorRenderUnsupported
from aura_music_studio.professional_image_compositor import AdvancedImageCompositor
from aura_music_studio.professional_universal_image_compositor import (
    SUPPORTED_UNIVERSAL_IMAGE_EFFECTS,
    UniversalImageCompositor,
    _apply_universal_image_effect,
    _cinematic_filter,
    _hex_rgb,
)


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project(tmp_path):
    project = tmp_path / "UniversalImageProject"
    store = ProfessionalEditorStore(project)
    store.initialize("UniversalImageProject")
    sequence = store.create_sequence(
        kind="image",
        name="Universal Image",
        width=180,
        height=120,
    )
    track = store.create_track(sequence.id, kind="image", name="Artwork")
    source = project / "sources" / "universal_source.png"
    source.parent.mkdir(parents=True, exist_ok=True)

    yy, xx = np.mgrid[0:120, 0:180]
    rgba = np.zeros((120, 180, 4), dtype=np.uint8)
    rgba[..., 0] = np.clip(35 + xx * 1.1, 0, 255).astype(np.uint8)
    rgba[..., 1] = np.clip(25 + yy * 1.5, 0, 255).astype(np.uint8)
    rgba[..., 2] = np.clip(200 - xx * 0.7 + yy * 0.2, 0, 255).astype(np.uint8)
    rgba[..., 3] = np.clip(70 + xx, 0, 255).astype(np.uint8)
    Image.fromarray(rgba, mode="RGBA").save(source)

    item = store.create_item(
        track.id,
        kind="image_layer",
        name="Source",
        source_ref="sources/universal_source.png",
        start=0.0,
        duration=1.0,
    )
    return project, store, sequence, track, item, source


def test_universal_image_contract_helpers_are_bounded_and_fail_closed():
    assert SUPPORTED_UNIVERSAL_IMAGE_EFFECTS == {
        "image.filter.cinematic",
        "image.filter.duotone",
    }
    assert _hex_rgb("#abc", field="Colour") == (170, 187, 204)
    assert _hex_rgb("102030", field="Colour") == (16, 32, 48)
    with pytest.raises(EditorRenderUnsupported, match="must be a #RRGGBB or #RGB colour"):
        _hex_rgb("not-a-colour", field="Colour")

    source = Image.new("RGB", (24, 16), (30, 110, 220))
    assert ImageChops.difference(source, _cinematic_filter(source, 0)).getbbox() is None
    strong = _cinematic_filter(source, 99)
    assert strong.size == source.size
    assert strong.mode == "RGB"

    with pytest.raises(EditorRenderUnsupported, match="Unsupported universal image effect"):
        _apply_universal_image_effect(
            source.convert("RGBA"),
            {"type": "image.filter.future", "parameters": {}},
            0.0,
        )


def test_duotone_preserves_alpha_and_wet_dry_mix():
    image = Image.new("RGBA", (8, 4), (90, 150, 210, 73))
    full = _apply_universal_image_effect(
        image,
        {
            "type": "image.filter.duotone",
            "mix": 1.0,
            "parameters": {"shadow": "#000000", "highlight": "#ffcc00"},
        },
        0.0,
    )
    half = _apply_universal_image_effect(
        image,
        {
            "type": "image.filter.duotone",
            "mix": 0.5,
            "parameters": {"shadow": "#000000", "highlight": "#ffcc00"},
        },
        0.0,
    )
    assert full.getchannel("A").getextrema() == (73, 73)
    assert half.getchannel("A").getextrema() == (73, 73)
    # Pillow's RGBA ``getbbox`` can ignore non-alpha differences when the alpha-difference
    # band is all zero. Assert alpha preservation above and inspect RGB change explicitly.
    assert ImageChops.difference(image.convert("RGB"), full.convert("RGB")).getbbox() is not None
    assert ImageChops.difference(image.convert("RGB"), half.convert("RGB")).getbbox() is not None
    assert ImageChops.difference(full.convert("RGB"), half.convert("RGB")).getbbox() is not None


def test_cinematic_strength_keyframes_are_inspectable_at_frame_time():
    image = Image.new("RGBA", (20, 20), (40, 130, 220, 255))
    effect = EditorEffect(
        type="image.filter.cinematic",
        parameters={"strength": 0.0},
        keyframes={
            "strength": [
                EditorKeyframe(time=0.0, value=0.0, interpolation="linear"),
                EditorKeyframe(time=1.0, value=1.0, interpolation="linear"),
            ]
        },
    ).model_dump(mode="json")
    start = _apply_universal_image_effect(image, effect, 0.0)
    middle = _apply_universal_image_effect(image, effect, 0.5)
    end = _apply_universal_image_effect(image, effect, 1.0)
    assert ImageChops.difference(image, start).getbbox() is None
    assert ImageChops.difference(image.convert("RGB"), middle.convert("RGB")).getbbox() is not None
    assert ImageChops.difference(middle.convert("RGB"), end.convert("RGB")).getbbox() is not None


def test_real_image_designer_export_executes_universal_filters_without_mutating_source(tmp_path):
    project, store, sequence, _track, item, source = _project(tmp_path)
    before_sha = _sha256(source)
    store.add_effect(
        "item",
        item.id,
        EditorEffect(
            type="image.filter.cinematic",
            mix=1.0,
            parameters={"strength": 0.82},
        ),
    )
    store.add_effect(
        "item",
        item.id,
        EditorEffect(
            type="image.filter.duotone",
            mix=0.35,
            parameters={"shadow": "#111827", "highlight": "#f2c86f"},
        ),
    )

    result = UniversalImageCompositor(project).render_image_advanced(sequence.id, format="png", frame_time=0.75)
    output = project / result.output_ref
    assert output.is_file() and output.stat().st_size > 0
    assert output.suffix == ".png"
    assert result.renderer == "pillow-universal-image-compositor"
    assert _sha256(source) == before_sha

    metadata = json.loads((project / result.metadata_ref).read_text(encoding="utf-8"))
    assert metadata["universal_image_compositor"] is True
    assert metadata["universal_image_effect_contracts_executed"] == [
        "image.filter.cinematic",
        "image.filter.duotone",
    ]
    assert metadata["universal_image_effect_instances_executed"] == 2
    assert metadata["universal_image_effect_scopes_executed"] == ["item"]
    assert metadata["supported_universal_image_effect_scopes"] == ["item", "track"]
    assert metadata["source_media_mutated"] is False
    assert metadata["source_refs"] == ["sources/universal_source.png"]

    with Image.open(source) as left, Image.open(output) as right:
        left_rgb = left.convert("RGB")
        right_rgb = right.convert("RGB")
        assert ImageChops.difference(left_rgb, right_rgb).getbbox() is not None
        left_mean = sum(ImageStat.Stat(left_rgb).mean) / 3.0
        right_mean = sum(ImageStat.Stat(right_rgb).mean) / 3.0
    assert abs(left_mean - right_mean) > 0.5


def test_universal_image_compositor_remains_additive_to_advanced_layer_renderer():
    assert issubclass(UniversalImageCompositor, AdvancedImageCompositor)


def test_universal_image_renderer_fails_closed_for_unknown_namespaced_filter(tmp_path):
    project, store, sequence, _track, item, _source = _project(tmp_path)
    store.add_effect("item", item.id, EditorEffect(type="image.filter.future", parameters={}))
    with pytest.raises(EditorRenderUnsupported, match="image.filter.future"):
        UniversalImageCompositor(project).render_image_advanced(sequence.id)


def test_universal_image_renderer_executes_whole_track_contracts_without_mutating_source(tmp_path):
    project, store, sequence, track, _item, source = _project(tmp_path)
    before_sha = _sha256(source)
    baseline = UniversalImageCompositor(project).render_image_advanced(sequence.id)
    baseline_path = project / baseline.output_ref

    store.add_effect(
        "track",
        track.id,
        EditorEffect(type="image.filter.cinematic", parameters={"strength": 0.85}),
    )
    store.add_effect(
        "track",
        track.id,
        EditorEffect(
            type="image.filter.duotone",
            mix=0.4,
            parameters={"shadow": "#09111d", "highlight": "#ffd77a"},
        ),
    )
    result = UniversalImageCompositor(project).render_image_advanced(sequence.id, frame_time=0.5)
    output = project / result.output_ref

    assert _sha256(source) == before_sha
    with Image.open(baseline_path) as left, Image.open(output) as right:
        assert ImageChops.difference(left.convert("RGB"), right.convert("RGB")).getbbox() is not None

    metadata = json.loads((project / result.metadata_ref).read_text(encoding="utf-8"))
    assert metadata["universal_image_effect_contracts_executed"] == [
        "image.filter.cinematic",
        "image.filter.duotone",
    ]
    assert metadata["universal_image_effect_instances_executed"] == 2
    assert metadata["universal_image_effect_scopes_executed"] == ["track"]
    assert metadata["supported_universal_image_effect_scopes"] == ["item", "track"]
    assert metadata["source_media_mutated"] is False


def test_universal_image_renderer_tracks_item_and_track_effect_scope_evidence(tmp_path):
    project, store, sequence, track, item, _source = _project(tmp_path)
    store.add_effect(
        "item",
        item.id,
        EditorEffect(type="image.filter.cinematic", parameters={"strength": 0.4}),
    )
    store.add_effect(
        "track",
        track.id,
        EditorEffect(
            type="image.filter.duotone",
            parameters={"shadow": "#000000", "highlight": "#ffffff"},
        ),
    )
    result = UniversalImageCompositor(project).render_image_advanced(sequence.id)
    metadata = json.loads((project / result.metadata_ref).read_text(encoding="utf-8"))
    assert metadata["universal_image_effect_instances_executed"] == 2
    assert metadata["universal_image_effect_scopes_executed"] == ["item", "track"]


def test_universal_image_renderer_fails_closed_for_unknown_track_filter(tmp_path):
    project, store, sequence, track, _item, _source = _project(tmp_path)
    store.add_effect("track", track.id, EditorEffect(type="image.filter.future", parameters={}))
    with pytest.raises(EditorRenderUnsupported, match="image.filter.future"):
        UniversalImageCompositor(project).render_image_advanced(sequence.id)


def test_editor_render_api_routes_image_exports_through_universal_image_compositor():
    import inspect

    from aura_music_studio import professional_editor_render_api

    source = inspect.getsource(professional_editor_render_api)
    assert "UniversalImageCompositor" in source
    assert "image_renderer = UniversalImageCompositor" in source
