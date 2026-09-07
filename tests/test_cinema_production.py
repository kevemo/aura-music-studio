from __future__ import annotations

import pytest
from pydantic import ValidationError

from aura_music_studio.cinema_production import (
    CinemaProductionStore,
    ContinuityRequest,
    FilmPatchRequest,
    FilmRequest,
    SceneRequest,
    ShotPatchRequest,
    ShotRequest,
    StoryboardPanelRequest,
    router as cinema_router,
)
from aura_music_studio.professional_editor import ProfessionalEditorStore
from aura_music_studio.professional_editor_api import router as professional_editor_router
from aura_music_studio.professional_editor_security_overlay import install_professional_editor_patch_guard


def _editor(tmp_path):
    editor = ProfessionalEditorStore(tmp_path)
    editor.initialize("cinema-test")
    master = editor.create_sequence(
        kind="video",
        name="Feature Master",
        width=4096,
        height=2160,
        fps=24.0,
        duration=5400.0,
    )
    scene_sequence = editor.create_sequence(
        kind="video",
        name="Scene 1 Assembly",
        width=4096,
        height=2160,
        fps=24.0,
        duration=180.0,
    )
    shot_sequence = editor.create_sequence(
        kind="video",
        name="Shot 1A",
        width=4096,
        height=2160,
        fps=24.0,
        duration=8.0,
    )
    board = editor.create_sequence(
        kind="image",
        name="Storyboard 1A",
        width=1920,
        height=1080,
        fps=1.0,
        duration=1.0,
    )
    return editor, master, scene_sequence, shot_sequence, board


def _hierarchy(store: CinemaProductionStore, master, scene_sequence, shot_sequence):
    film = store.create_film(
        FilmRequest(
            title="Starbound",
            format="feature",
            logline="A purposeful journey across impossible skies.",
            master_sequence_id=master.id,
        )
    )
    scene = store.create_scene(
        film.id,
        SceneRequest(
            scene_number=1,
            slugline="EXT. STARS - NIGHT",
            script_text="The horizon opens into a field of stars.",
            editor_sequence_id=scene_sequence.id,
        ),
    )
    shot = store.create_shot(
        scene.id,
        ShotRequest(
            shot_number=1,
            title="Opening Wide",
            description="Reveal the star field.",
            editor_sequence_id=shot_sequence.id,
            shot_size="extreme_wide",
            camera_movement="dolly",
            lens_mm=24.0,
            continuity=ContinuityRequest(
                lighting="Blue-white starlight from camera left",
                screen_direction="Travel remains left to right",
            ),
        ),
    )
    return film, scene, shot


def test_cinema_hierarchy_persists_inside_existing_project(tmp_path):
    _, master, scene_sequence, shot_sequence, board = _editor(tmp_path)
    store = CinemaProductionStore(tmp_path)
    film, scene, shot = _hierarchy(store, master, scene_sequence, shot_sequence)
    panel = store.create_storyboard_panel(
        shot.id,
        StoryboardPanelRequest(
            panel_number=1,
            caption="Opening composition",
            action="Camera advances through the star field.",
            image_sequence_id=board.id,
        ),
    )

    saved = store.load()
    assert store.path == tmp_path / "work" / "cinema_production.json"
    assert saved.films[0].scene_ids == [scene.id]
    assert saved.scenes[0].shot_ids == [shot.id]
    assert saved.shots[0].storyboard_panel_ids == [panel.id]
    assert saved.shots[0].status == "storyboarded"
    assert saved.shots[0].continuity.screen_direction == "Travel remains left to right"
    assert saved.storyboard_panels[0].image_sequence_id == board.id
    assert saved.films[0].master_sequence_id == master.id


def test_cinema_editor_references_enforce_media_kind(tmp_path):
    _, master, scene_sequence, shot_sequence, board = _editor(tmp_path)
    store = CinemaProductionStore(tmp_path)

    with pytest.raises(ValueError, match="requires a video editor sequence"):
        store.create_film(FilmRequest(title="Wrong Master", master_sequence_id=board.id))

    film, scene, shot = _hierarchy(store, master, scene_sequence, shot_sequence)
    with pytest.raises(ValueError, match="requires a image editor sequence"):
        store.create_storyboard_panel(
            shot.id,
            StoryboardPanelRequest(panel_number=1, image_sequence_id=master.id),
        )

    assert film.id
    assert scene.id


def test_cinema_hierarchy_rejects_duplicate_ordinals(tmp_path):
    _, master, scene_sequence, shot_sequence, board = _editor(tmp_path)
    store = CinemaProductionStore(tmp_path)
    film, scene, shot = _hierarchy(store, master, scene_sequence, shot_sequence)

    with pytest.raises(ValueError, match="scene number already exists"):
        store.create_scene(
            film.id,
            SceneRequest(scene_number=1, slugline="INT. DUPLICATE - DAY"),
        )

    with pytest.raises(ValueError, match="shot number already exists"):
        store.create_shot(scene.id, ShotRequest(shot_number=1, title="Duplicate Shot"))

    store.create_storyboard_panel(
        shot.id,
        StoryboardPanelRequest(panel_number=1, image_sequence_id=board.id),
    )
    with pytest.raises(ValueError, match="storyboard panel number already exists"):
        store.create_storyboard_panel(
            shot.id,
            StoryboardPanelRequest(panel_number=1, caption="Duplicate"),
        )


def test_cinema_request_models_reject_undeclared_execution_fields():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        FilmRequest(title="Unsafe Film", ffmpeg_args=["-vf", "scale=1280:720"])

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SceneRequest(scene_number=1, slugline="INT. SAFE - DAY", plugin="example.module")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ShotPatchRequest(shell="echo example")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ShotRequest(
            shot_number=1,
            continuity={"wardrobe": "Blue coat", "command": "example"},
        )


def test_shot_patch_updates_bounded_continuity_and_editor_reference(tmp_path):
    _, master, scene_sequence, shot_sequence, _ = _editor(tmp_path)
    store = CinemaProductionStore(tmp_path)
    _, _, shot = _hierarchy(store, master, scene_sequence, shot_sequence)

    updated = store.patch_shot(
        shot.id,
        ShotPatchRequest(
            status="approved",
            camera_movement="gimbal",
            continuity=ContinuityRequest(
                wardrobe="Gold jacket",
                props="One sealed envelope",
                performance="Hold eye line just right of camera",
            ),
        ),
    )
    assert updated.status == "approved"
    assert updated.camera_movement == "gimbal"
    assert updated.continuity.wardrobe == "Gold jacket"
    assert updated.continuity.props == "One sealed envelope"


def test_shared_skies_cinema_handoff_is_prepared_only_and_exposes_no_host_path(tmp_path):
    _, master, scene_sequence, shot_sequence, _ = _editor(tmp_path)
    store = CinemaProductionStore(tmp_path)
    film, _, _ = _hierarchy(store, master, scene_sequence, shot_sequence)

    with pytest.raises(ValueError, match="delivery_ready"):
        store.handoff_manifest(film.id)

    film = store.patch_film(film.id, FilmPatchRequest(status="delivery_ready"))
    handoff = store.handoff_manifest(film.id)
    assert handoff["authority"] == "chat5_shared_skies_transport"
    assert handoff["state"] == "prepared_not_transmitted"
    assert handoff["transmission_requested"] is False
    assert handoff["transport_credentials_present"] is False
    assert handoff["editor_source"]["project_dir_exposed"] is False
    assert "path" not in handoff["editor_source"]
    assert handoff["production_summary"]["metadata_only"] is True
    assert handoff["production_summary"]["scene_count"] == 1
    assert handoff["production_summary"]["shot_count"] == 1
    assert film.master_sequence_id == master.id


def test_delivery_ready_film_requires_master_sequence(tmp_path):
    _editor(tmp_path)
    store = CinemaProductionStore(tmp_path)
    film = store.create_film(FilmRequest(title="No Master", status="delivery_ready"))
    with pytest.raises(ValueError, match="requires a master video sequence"):
        store.handoff_manifest(film.id)


def test_cinema_routes_install_once_into_production_editor_family():
    install_professional_editor_patch_guard()
    install_professional_editor_patch_guard()
    for candidate in cinema_router.routes:
        signature = (
            getattr(candidate, "path", None),
            frozenset(getattr(candidate, "methods", set())),
            getattr(candidate, "endpoint", None),
        )
        matches = [
            route
            for route in professional_editor_router.routes
            if (
                getattr(route, "path", None),
                frozenset(getattr(route, "methods", set())),
                getattr(route, "endpoint", None),
            )
            == signature
        ]
        assert len(matches) == 1
