from __future__ import annotations

import json

import pytest
from PIL import Image

from aura_music_studio.professional_editor import EditorEffect, EditorMask, ProfessionalEditorStore
from aura_music_studio.professional_editor_renderer import EditorRenderUnsupported
from aura_music_studio.professional_image_compositor import AdvancedImageCompositor


def _project(tmp_path):
    project = tmp_path / "Artwork"
    source = project / "input" / "red.png"
    source.parent.mkdir(parents=True)
    Image.new("RGBA", (80, 80), (255, 0, 0, 255)).save(source)
    store = ProfessionalEditorStore(project)
    store.initialize("Artwork")
    sequence = store.create_sequence(kind="image", name="Cover", width=160, height=100)
    track = store.create_track(sequence.id, kind="image", name="Artwork")
    item = store.create_item(
        track.id,
        kind="image_layer",
        name="Red",
        source_ref="input/red.png",
        duration=1.0,
    )
    return project, store, sequence, track, item


def test_mask_effect_and_metadata_are_rendered_not_silently_ignored(tmp_path):
    project, store, sequence, _track, item = _project(tmp_path)
    store.add_effect("item", item.id, EditorEffect(type="grayscale", mix=1.0))
    store.add_mask(
        item.id,
        EditorMask(
            name="Left half",
            shape="rectangle",
            points=[(0.0, 0.0), (0.5, 1.0)],
            feather=0.0,
        ),
    )

    result = AdvancedImageCompositor(project).render_image_advanced(sequence.id, format="png")
    output = project / result.output_ref
    with Image.open(output) as rendered:
        rgba = rendered.convert("RGBA")
        # Source is centered at x=40..119. Mask retains only the source's left half x=40..79.
        visible = rgba.getpixel((55, 50))
        hidden = rgba.getpixel((105, 50))
        assert visible[3] > 240
        assert visible[0] == visible[1] == visible[2]  # grayscale effect actually rendered
        assert hidden[3] == 0

    metadata = json.loads((project / result.metadata_ref).read_text(encoding="utf-8"))
    assert metadata["advanced_compositor"] is True
    assert metadata["supports_masks"] is True
    assert metadata["supports_effects"] is True
    assert metadata["source_media_mutated"] is False


def test_numeric_keyframe_changes_exported_frame_position(tmp_path):
    project, store, sequence, _track, item = _project(tmp_path)
    store.set_item_keyframes(
        item.id,
        "transform.x",
        [
            {"time": 0.0, "value": -30.0, "interpolation": "linear"},
            {"time": 1.0, "value": 30.0, "interpolation": "linear"},
        ],
    )
    compositor = AdvancedImageCompositor(project)
    left = compositor.render_image_advanced(sequence.id, format="png", frame_time=0.0)
    right = compositor.render_image_advanced(sequence.id, format="png", frame_time=1.0)

    def centre_of_alpha(path):
        with Image.open(path) as opened:
            alpha = opened.convert("RGBA").getchannel("A")
            bbox = alpha.getbbox()
            assert bbox is not None
            return (bbox[0] + bbox[2]) / 2.0

    assert centre_of_alpha(project / right.output_ref) - centre_of_alpha(project / left.output_ref) > 50


def test_layer_blend_mode_is_rendered(tmp_path):
    project, store, sequence, track, item = _project(tmp_path)
    # Opaque white background gives multiply a deterministic red result.
    store.patch_sequence(sequence.id, {"background": "#ffffffff"})
    store.patch_item(item.id, {"blend_mode": "multiply"})
    result = AdvancedImageCompositor(project).render_image_advanced(sequence.id, format="png")
    with Image.open(project / result.output_ref) as rendered:
        pixel = rendered.convert("RGBA").getpixel((80, 50))
        assert pixel[0] > 240
        assert pixel[1] < 10
        assert pixel[2] < 10
        assert pixel[3] == 255


def test_unknown_effect_fails_closed(tmp_path):
    project, store, sequence, _track, item = _project(tmp_path)
    store.add_effect("item", item.id, EditorEffect(type="future_neural_magic", mix=1.0))
    with pytest.raises(EditorRenderUnsupported, match="Unsupported image effect"):
        AdvancedImageCompositor(project).render_image_advanced(sequence.id, format="png")


def test_source_image_is_never_rewritten(tmp_path):
    project, store, sequence, _track, item = _project(tmp_path)
    source = project / "input" / "red.png"
    before = source.read_bytes()
    store.patch_item(item.id, {"color": {"contrast": 1.4, "saturation": 0.5}})
    AdvancedImageCompositor(project).render_image_advanced(sequence.id, format="webp", quality=80)
    assert source.read_bytes() == before
