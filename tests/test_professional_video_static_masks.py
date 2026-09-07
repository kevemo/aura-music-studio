from __future__ import annotations

import hashlib
import json
import shutil
import subprocess

import pytest
from PIL import Image

from aura_music_studio.professional_editor import EditorMask, ProfessionalEditorStore
from aura_music_studio.professional_editor_renderer import EditorRenderUnsupported
from aura_music_studio.professional_video_effects_compositor import VideoItemEffectsCompositor


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_red_source(project):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg required for real static-mask test")
    source = project / "sources" / "red.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=160x90:r=10:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )
    return source


def _masked_project(tmp_path):
    project = tmp_path / "MaskedVideoProject"
    store = ProfessionalEditorStore(project)
    store.initialize("MaskedVideoProject")
    sequence = store.create_sequence(kind="video", name="Mask", width=160, height=90, fps=10.0, duration=1.0)
    track = store.create_track(sequence.id, kind="video", name="Picture")
    source = _make_red_source(project)
    item = store.create_item(
        track.id,
        kind="video_clip",
        name="Red clip",
        source_ref="sources/red.mp4",
        start=0.0,
        duration=1.0,
    )
    store.add_mask(
        item.id,
        EditorMask(
            name="Center ellipse",
            shape="ellipse",
            mode="add",
            points=[(0.25, 0.2), (0.75, 0.8)],
            feather=0.0,
            expansion=0.0,
        ),
    )
    return project, store, sequence, item, source


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg required for real static-mask test")
def test_static_ellipse_mask_survives_alpha_composition_and_source_is_unchanged(tmp_path):
    project, _store, sequence, _item, source = _masked_project(tmp_path)
    before_sha = _sha256(source)

    result = VideoItemEffectsCompositor(project).render_video_advanced(sequence.id)
    output = project / result.output_ref
    assert output.is_file() and output.stat().st_size > 0
    assert result.renderer == "ffmpeg-video-item-effects-mask-compositor"
    assert _sha256(source) == before_sha

    metadata = json.loads((project / result.metadata_ref).read_text(encoding="utf-8"))
    assert metadata["supports_static_video_masks"] is True
    assert metadata["supports_mask_feather_expansion"] is True
    assert metadata["tracked_or_keyframed_masks_fail_closed"] is True
    assert metadata["transient_mask_derivatives"] == 1
    assert metadata["source_refs"] == ["sources/red.mp4"]
    assert metadata["source_media_mutated"] is False

    transient_parent = project / "work" / "editor_video_effects"
    assert not transient_parent.exists() or not any(transient_parent.iterdir())

    frame = tmp_path / "masked_frame.png"
    subprocess.run(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "0.4",
            "-i",
            str(output),
            "-frames:v",
            "1",
            str(frame),
        ],
        check=True,
    )
    with Image.open(frame) as opened:
        rgb = opened.convert("RGB")
        center = rgb.getpixel((80, 45))
        corner = rgb.getpixel((10, 10))
    assert center[0] > 120 and center[0] > center[1] + 60 and center[0] > center[2] + 60
    assert max(corner) < 40


def test_tracked_mask_and_mask_with_crop_remain_fail_closed(tmp_path):
    project = tmp_path / "MaskTruthBoundary"
    store = ProfessionalEditorStore(project)
    store.initialize("MaskTruthBoundary")
    sequence = store.create_sequence(kind="video", name="Mask", width=160, height=90, fps=10.0, duration=1.0)
    track = store.create_track(sequence.id, kind="video", name="Picture")
    item = store.create_item(
        track.id,
        kind="video_clip",
        name="Clip",
        source_ref="sources/not-needed-for-validation.mp4",
        duration=1.0,
    )
    tracked = store.add_mask(
        item.id,
        EditorMask(
            name="Tracked",
            shape="rectangle",
            points=[(0.1, 0.1), (0.9, 0.9)],
            tracking={"mode": "planar"},
        ),
    )
    with pytest.raises(EditorRenderUnsupported, match="Tracked video masks"):
        VideoItemEffectsCompositor(project).render_video_advanced(sequence.id)

    project_data = store.load()
    branch = store._branch(project_data)
    stored_item = store._item(branch, item.id)
    stored_item.masks = [mask for mask in stored_item.masks if mask.id != tracked.id]
    stored_item.masks.append(
        EditorMask(name="Static", shape="rectangle", points=[(0.1, 0.1), (0.9, 0.9)])
    )
    stored_item.crop["left"] = 0.1
    store.save(project_data)
    with pytest.raises(EditorRenderUnsupported, match="combined with crop"):
        VideoItemEffectsCompositor(project).render_video_advanced(sequence.id)
