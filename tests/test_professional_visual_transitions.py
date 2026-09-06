from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from aura_music_studio.professional_editor import ProfessionalEditorStore
from aura_music_studio.professional_editor_security_overlay import (
    install_professional_editor_patch_guard,
    professional_editor_router,
)
from aura_music_studio.professional_video_transition_compositor import (
    TransitionAwareGroupedVideoCompositor,
)
from aura_music_studio.professional_visual_transitions import (
    CreateVisualTransitionRequest,
    EditorVisualTransition,
    PatchVisualTransitionRequest,
    VISUAL_TRANSITIONS_KEY,
    create_visual_transition,
    delete_visual_transition,
    patch_visual_transition,
    transitions_from_sequence,
    validate_visual_transition,
)


def _editor(tmp_path: Path):
    store = ProfessionalEditorStore(tmp_path)
    store.initialize("transition-test")
    sequence = store.create_sequence(
        kind="video", name="Picture", width=64, height=64, fps=24.0, duration=4.0
    )
    track = store.create_track(sequence.id, kind="video", name="V1")
    left = store.create_item(
        track.id,
        kind="image_layer",
        name="Outgoing",
        source_ref="media/red.png",
        start=0.0,
        duration=2.0,
    )
    right = store.create_item(
        track.id,
        kind="image_layer",
        name="Incoming",
        source_ref="media/blue.png",
        start=1.0,
        duration=2.0,
    )
    return store, sequence, track, left, right


def _sequence(store: ProfessionalEditorStore, sequence_id: str) -> dict:
    return next(
        row for row in store.public_state()["branch"]["sequences"] if row["id"] == sequence_id
    )


def test_transition_requests_fail_closed_on_execution_adjacent_fields():
    for field, value in (
        ("ffmpeg_args", ["-vf", "evil"]),
        ("filter", "movie=/etc/passwd"),
        ("plugin", "example.module"),
        ("shell", "echo example"),
    ):
        with pytest.raises(ValidationError):
            CreateVisualTransitionRequest.model_validate(
                {"kind": "fade_in", "to_item_id": "item-1", field: value}
            )

    with pytest.raises(ValidationError):
        CreateVisualTransitionRequest.model_validate(
            {"kind": "fade_in", "to_item_id": "item-1", "easing": "smooth"}
        )


def test_cross_dissolve_persists_in_sequence_metadata_and_undo_redo(tmp_path):
    store, sequence, _track, left, right = _editor(tmp_path)
    transition = create_visual_transition(
        store,
        sequence.id,
        CreateVisualTransitionRequest(
            kind="cross_dissolve",
            from_item_id=left.id,
            to_item_id=right.id,
            duration=1.0,
        ),
        actor="Transition Test",
    )

    saved = transitions_from_sequence(_sequence(store, sequence.id))
    assert [row.id for row in saved] == [transition.id]
    assert _sequence(store, sequence.id)["metadata"][VISUAL_TRANSITIONS_KEY][0]["kind"] == "cross_dissolve"

    undone = store.undo()
    assert undone.operation == "patch_sequence"
    assert transitions_from_sequence(_sequence(store, sequence.id)) == []

    redone = store.redo()
    assert redone.operation == "patch_sequence"
    assert transitions_from_sequence(_sequence(store, sequence.id))[0].id == transition.id


def test_cross_dissolve_requires_adjacent_same_track_exact_authored_overlap(tmp_path):
    store, sequence, track, left, right = _editor(tmp_path)
    valid = EditorVisualTransition(
        kind="cross_dissolve",
        from_item_id=left.id,
        to_item_id=right.id,
        duration=1.0,
    )
    validate_visual_transition(store, sequence.id, valid)

    store.patch_item(right.id, {"start": 1.25})
    with pytest.raises(ValueError, match="clip overlap equal"):
        validate_visual_transition(store, sequence.id, valid)
    store.patch_item(right.id, {"start": 1.0})

    middle = store.create_item(
        track.id,
        kind="image_layer",
        name="Intervening",
        source_ref="media/red.png",
        start=0.5,
        duration=0.25,
    )
    project = store.load()
    branch = store._branch(project)
    live_track = store._track(branch, track.id)
    live_track.item_ids.remove(middle.id)
    live_track.item_ids.insert(1, middle.id)
    store.save(project)
    with pytest.raises(ValueError, match="adjacent"):
        validate_visual_transition(store, sequence.id, valid)


def test_cross_dissolve_rejects_different_tracks_and_duplicate_edges(tmp_path):
    store, sequence, _track, left, right = _editor(tmp_path)
    other_track = store.create_track(sequence.id, kind="video", name="V2")
    other = store.create_item(
        other_track.id,
        kind="image_layer",
        name="Other",
        source_ref="media/blue.png",
        start=1.0,
        duration=2.0,
    )
    with pytest.raises(ValueError, match="same video track"):
        validate_visual_transition(
            store,
            sequence.id,
            EditorVisualTransition(
                kind="cross_dissolve",
                from_item_id=left.id,
                to_item_id=other.id,
                duration=1.0,
            ),
        )

    first = create_visual_transition(
        store,
        sequence.id,
        CreateVisualTransitionRequest(
            kind="cross_dissolve",
            from_item_id=left.id,
            to_item_id=right.id,
            duration=1.0,
        ),
        actor="Transition Test",
    )
    with pytest.raises(ValueError, match="active incoming transition"):
        create_visual_transition(
            store,
            sequence.id,
            CreateVisualTransitionRequest(kind="fade_in", to_item_id=right.id, duration=0.5),
            actor="Transition Test",
        )
    assert first.id


def test_transition_patch_delete_and_malformed_metadata_fail_closed(tmp_path):
    store, sequence, _track, _left, right = _editor(tmp_path)
    transition = create_visual_transition(
        store,
        sequence.id,
        CreateVisualTransitionRequest(kind="fade_in", to_item_id=right.id, duration=0.5),
        actor="Transition Test",
    )
    updated = patch_visual_transition(
        store,
        sequence.id,
        transition.id,
        PatchVisualTransitionRequest(duration=0.75),
        actor="Transition Test",
    )
    assert updated.duration == 0.75
    removed = delete_visual_transition(
        store, sequence.id, transition.id, actor="Transition Test"
    )
    assert removed.id == transition.id
    assert transitions_from_sequence(_sequence(store, sequence.id)) == []

    store.patch_sequence(
        sequence.id,
        {"metadata": {VISUAL_TRANSITIONS_KEY: [{"kind": "custom_shader", "duration": 1.0}]}},
    )
    with pytest.raises(ValidationError):
        transitions_from_sequence(_sequence(store, sequence.id))


def test_transition_renderer_executes_real_alpha_fade_without_mutating_source(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is not installed")

    media = tmp_path / "media"
    media.mkdir(parents=True)
    source = media / "red.png"
    Image.new("RGBA", (64, 64), (255, 0, 0, 255)).save(source)
    original = source.read_bytes()

    store = ProfessionalEditorStore(tmp_path)
    store.initialize("transition-render-test")
    sequence = store.create_sequence(
        kind="video", name="Picture", width=64, height=64, fps=24.0, duration=2.0
    )
    track = store.create_track(sequence.id, kind="video", name="V1")
    item = store.create_item(
        track.id,
        kind="image_layer",
        name="Red",
        source_ref="media/red.png",
        start=0.0,
        duration=2.0,
    )

    renderer = TransitionAwareGroupedVideoCompositor(tmp_path)
    state_item = next(
        row for row in store.public_state()["branch"]["items"] if row["id"] == item.id
    )
    derived_root = tmp_path / "work" / "transition-test-derivative"
    relative = renderer._derive_transition_media(state_item, [("in", 1.0)], derived_root)
    derived = tmp_path / relative
    assert derived.is_file()

    def alpha_at(second: float) -> float:
        completed = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{second:.3f}",
                "-i",
                str(derived),
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgba",
                "pipe:1",
            ],
            capture_output=True,
            timeout=30,
            check=True,
        )
        pixels = completed.stdout
        assert len(pixels) == 64 * 64 * 4
        return sum(pixels[3::4]) / (64 * 64 * 255.0)

    early = alpha_at(0.05)
    late = alpha_at(0.95)
    assert early < 0.25
    assert late > 0.75
    assert late > early + 0.5
    assert source.read_bytes() == original


def test_transition_routes_and_renderers_install_idempotently():
    install_professional_editor_patch_guard()
    install_professional_editor_patch_guard()

    paths = [
        getattr(route, "path", "")
        for route in professional_editor_router.routes
        if "/visual-transitions" in getattr(route, "path", "")
    ]
    assert paths.count(
        "/creative/projects/{project_name}/editor/sequences/{sequence_id}/visual-transitions"
    ) == 2  # GET + POST
    assert paths.count(
        "/creative/projects/{project_name}/editor/sequences/{sequence_id}/visual-transitions/{transition_id}"
    ) == 2  # PATCH + DELETE

    import aura_music_studio.professional_editor_render_api as render_api
    import aura_music_studio.professional_editor_render_jobs as render_jobs

    from aura_music_studio.professional_video_transition_compositor import (
        TransitionAwareGroupedVideoCompositor,
        TransitionAwareUniversalVisualVideoCompositor,
    )

    assert render_api.UniversalVisualVideoCompositor is TransitionAwareUniversalVisualVideoCompositor
    assert render_jobs.GroupedUnifiedAdvancedVideoCompositor is TransitionAwareGroupedVideoCompositor
