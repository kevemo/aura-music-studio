from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from aura_music_studio.professional_editor import ProfessionalEditorStore
from aura_music_studio.professional_editor_api import router as professional_editor_router


def _video_editor(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    media = project / "input" / "source.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"immutable-source-media")

    store = ProfessionalEditorStore(project)
    store.initialize("editor-test")
    sequence = store.create_sequence(
        kind="video",
        name="Main Video",
        width=1920,
        height=1080,
        fps=24.0,
        duration=20.0,
    )
    track = store.create_track(sequence.id, kind="video", name="Video 1", role="picture")
    item = store.create_item(
        track.id,
        kind="video_clip",
        name="Opening Shot",
        source_ref="input/source.mp4",
        start=0.0,
        duration=8.0,
    )
    return store, media, sequence, track, item


def test_editor_is_non_destructive_and_split_is_undoable(tmp_path: Path):
    store, media, _sequence, _track, item = _video_editor(tmp_path)
    original = media.read_bytes()

    edited = store.patch_item(
        item.id,
        {
            "transform": {"x": 120.0, "rotation": 15.0},
            "crop": {"left": 0.1},
            "color": {"exposure": 0.5, "saturation": 1.2},
        },
    )
    assert edited.transform["x"] == 120.0
    assert edited.crop["left"] == 0.1

    left, right = store.split_item(item.id, 4.0)
    assert left.duration == 4.0
    assert right.start == 4.0
    assert right.source_in == 4.0
    assert media.read_bytes() == original

    store.undo()
    state = store.public_state()["branch"]
    restored = [value for value in state["items"] if value["id"] == item.id]
    assert len(restored) == 1
    assert restored[0]["duration"] == 8.0
    assert all(value["id"] != right.id for value in state["items"])
    assert media.read_bytes() == original

    store.redo()
    state = store.public_state()["branch"]
    assert any(value["id"] == right.id for value in state["items"])
    assert media.read_bytes() == original
    assert store.public_state()["source_media_mutated"] is False


def test_editor_branches_are_metadata_only_and_comparable(tmp_path: Path):
    store, media, _sequence, _track, item = _video_editor(tmp_path)
    main_branch = store.public_state()["active_branch_id"]
    original = media.read_bytes()

    alternate = store.create_branch("Vertical cut")
    store.patch_item(item.id, {"transform": {"x": 250.0}, "opacity": 0.75})
    comparison = store.compare_branches(main_branch, alternate.id)

    changed = {entry["id"]: entry["fields"] for entry in comparison["items"]["changed"]}
    assert item.id in changed
    assert "transform" in changed[item.id]
    assert "opacity" in changed[item.id]
    assert comparison["media_files_duplicated"] is False
    assert media.read_bytes() == original

    store.checkout_branch(main_branch)
    main_item = store.load_item(item.id)
    assert main_item.transform["x"] == 0.0
    assert main_item.opacity == 1.0


def test_locked_tracks_fail_closed_and_invalid_crop_is_rejected(tmp_path: Path):
    store, _media, _sequence, track, item = _video_editor(tmp_path)
    store.patch_track(track.id, {"locked": True})

    with pytest.raises(PermissionError):
        store.patch_item(item.id, {"opacity": 0.5})

    store.patch_track(track.id, {"locked": False})
    with pytest.raises(ValueError, match="Horizontal crop"):
        store.patch_item(item.id, {"crop": {"left": 0.6, "right": 0.4}})


def test_professional_editor_routes_are_mounted_in_production_app():
    editor_paths = {route.path for route in professional_editor_router.routes if hasattr(route, "path")}
    expected = {
        "/creative/projects/{project_name}/editor/initialize",
        "/creative/projects/{project_name}/editor",
        "/creative/projects/{project_name}/editor/undo",
        "/creative/projects/{project_name}/editor/redo",
    }
    assert expected <= editor_paths

    # FastAPI >=0.137 keeps included routers as lazy _IncludedRouter nodes instead of flattening
    # child routes into app.routes. Import the production entrypoint in a clean process and use
    # the generated OpenAPI path table, which proves these routes are actually exposed by the
    # production application while remaining independent of pytest app-startup order.
    probe = (
        "import json; from app import app; "
        "print('EDITOR_PATHS=' + json.dumps(sorted(app.openapi().get('paths', {}).keys())))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    marker = next(
        line for line in completed.stdout.splitlines() if line.startswith("EDITOR_PATHS=")
    )
    production_paths = set(json.loads(marker.split("=", 1)[1]))
    assert expected <= production_paths
