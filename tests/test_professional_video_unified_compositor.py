from __future__ import annotations

import hashlib
import json
import shutil

import pytest
from PIL import Image

from aura_music_studio.professional_editor import EditorEffect, EditorMask, ProfessionalEditorStore
from aura_music_studio.professional_video_unified_compositor import UnifiedAdvancedVideoCompositor


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_png(path, size, colour):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, colour).save(path, format="PNG")
    return path


def _effect_blend_project(tmp_path):
    project = tmp_path / "UnifiedVideoProject"
    store = ProfessionalEditorStore(project)
    store.initialize("UnifiedVideoProject")
    sequence = store.create_sequence(kind="video", name="Unified", width=64, height=64, fps=10.0, duration=1.0)
    track = store.create_track(sequence.id, kind="video", name="Picture")

    bottom_source = _write_png(project / "sources" / "bottom.png", (64, 64), (180, 110, 70, 255))
    top_source = _write_png(project / "sources" / "top.png", (24, 24), (70, 220, 120, 255))

    store.create_item(
        track.id,
        kind="image_layer",
        name="Bottom",
        source_ref="sources/bottom.png",
        duration=1.0,
    )
    top = store.create_item(
        track.id,
        kind="image_layer",
        name="Effect blend",
        source_ref="sources/top.png",
        duration=1.0,
    )
    store.add_effect("item", top.id, EditorEffect(type="grayscale", mix=1.0))
    store.patch_item(top.id, {"blend_mode": "multiply"})
    return project, store, sequence, top, bottom_source, top_source


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for unified Video render test")
def test_effect_and_blend_render_through_one_non_destructive_stack(tmp_path):
    project, _store, sequence, _top, bottom_source, top_source = _effect_blend_project(tmp_path)
    before_bottom = _sha256(bottom_source)
    before_top = _sha256(top_source)

    result = UnifiedAdvancedVideoCompositor(project).render_video_advanced(sequence.id)
    output = project / result.output_ref
    assert output.is_file() and output.stat().st_size > 0
    assert _sha256(bottom_source) == before_bottom
    assert _sha256(top_source) == before_top

    metadata = json.loads((project / result.metadata_ref).read_text(encoding="utf-8"))
    assert metadata["advanced_video_compositor"] is True
    assert metadata["video_item_effects_compositor"] is True
    assert "multiply" in metadata["supports_item_blend_modes"]
    assert "grayscale" in metadata["supported_video_item_effects"]
    assert metadata["transient_derivatives"] == 1
    assert metadata["transient_derivatives_ephemeral"] is True
    assert metadata["source_refs"] == ["sources/bottom.png", "sources/top.png"]
    assert metadata["source_media_mutated"] is False

    transient_parent = project / "work" / "editor_video_effects"
    assert not transient_parent.exists() or not any(transient_parent.iterdir())


def test_static_mask_and_blend_pass_unified_validation(tmp_path):
    project = tmp_path / "MaskBlendValidation"
    store = ProfessionalEditorStore(project)
    store.initialize("MaskBlendValidation")
    sequence = store.create_sequence(kind="video", name="Mask blend", width=64, height=64, fps=10.0, duration=1.0)
    track = store.create_track(sequence.id, kind="video", name="Picture")
    item = store.create_item(
        track.id,
        kind="image_layer",
        name="Masked screen",
        source_ref="sources/not-needed-for-validation.png",
        duration=1.0,
    )
    store.add_mask(
        item.id,
        EditorMask(name="Ellipse", shape="ellipse", points=[(0.2, 0.2), (0.8, 0.8)]),
    )
    store.patch_item(item.id, {"blend_mode": "screen"})

    compositor = UnifiedAdvancedVideoCompositor(project)
    compositor._validate_effect_state(store.public_state(), sequence.id)
