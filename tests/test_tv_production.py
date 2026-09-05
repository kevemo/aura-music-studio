from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from aura_music_studio.professional_editor import ProfessionalEditorStore
from aura_music_studio.professional_editor_api import router as professional_editor_router
from aura_music_studio.professional_editor_security_overlay import install_professional_editor_patch_guard
from aura_music_studio.tv_production import (
    EpisodePatchRequest,
    EpisodeRequest,
    GraphicsPackageRequest,
    ProgrammeRequest,
    SeriesRequest,
    TVProductionStore,
    router as tv_router,
)


def _editor(tmp_path):
    editor = ProfessionalEditorStore(tmp_path)
    editor.initialize("tv-test")
    video = editor.create_sequence(
        kind="video",
        name="Episode Master",
        width=1920,
        height=1080,
        fps=25.0,
        duration=1800.0,
    )
    intro = editor.create_sequence(
        kind="video",
        name="Programme Intro",
        width=1920,
        height=1080,
        fps=25.0,
        duration=12.0,
    )
    image = editor.create_sequence(
        kind="image",
        name="Lower Third",
        width=1920,
        height=1080,
        fps=1.0,
        duration=1.0,
    )
    return editor, video, intro, image


def _programme_series(store: TVProductionStore):
    programme = store.create_programme(ProgrammeRequest(title="Shared Skies Tonight"))
    series = store.create_series(
        programme.id,
        SeriesRequest(title="Season One", season_number=1),
    )
    return programme, series


def test_tv_production_persists_inside_existing_project_and_relations(tmp_path):
    _, video, intro, lower_third = _editor(tmp_path)
    store = TVProductionStore(tmp_path)
    package = store.create_graphics_package(
        GraphicsPackageRequest(
            name="Nightly Package",
            intro_sequence_id=intro.id,
            lower_third_sequence_ids=[lower_third.id],
        )
    )
    programme = store.create_programme(
        ProgrammeRequest(
            title="Shared Skies Tonight",
            synopsis="Purposeful media magazine programme",
            default_graphics_package_id=package.id,
        )
    )
    series = store.create_series(
        programme.id,
        SeriesRequest(title="Season One", season_number=1),
    )
    episode = store.create_episode(
        EpisodeRequest(
            programme_id=programme.id,
            series_id=series.id,
            title="Opening Night",
            episode_number=1,
            editor_sequence_id=video.id,
            scheduled_at="2026-09-10T20:00:00+01:00",
        )
    )

    saved = store.load()
    assert store.path == tmp_path / "work" / "tv_production.json"
    assert saved.programmes[0].series_ids == [series.id]
    assert saved.series[0].episode_ids == [episode.id]
    assert saved.episodes[0].graphics_package_id == package.id
    assert saved.episodes[0].scheduled_at.endswith("+01:00")


def test_episode_requires_video_editor_sequence(tmp_path):
    _, _, _, image = _editor(tmp_path)
    store = TVProductionStore(tmp_path)
    programme, series = _programme_series(store)

    with pytest.raises(ValueError, match="require video editor sequences"):
        store.create_episode(
            EpisodeRequest(
                programme_id=programme.id,
                series_id=series.id,
                title="Wrong Source",
                episode_number=1,
                editor_sequence_id=image.id,
            )
        )


def test_graphics_package_allows_image_lower_third_but_rejects_image_intro(tmp_path):
    _, _, _, image = _editor(tmp_path)
    store = TVProductionStore(tmp_path)
    package = store.create_graphics_package(
        GraphicsPackageRequest(name="Safe Graphics", lower_third_sequence_ids=[image.id])
    )
    assert package.lower_third_sequence_ids == [image.id]

    with pytest.raises(ValueError, match="require video editor sequences"):
        store.create_graphics_package(
            GraphicsPackageRequest(name="Unsafe Intro", intro_sequence_id=image.id)
        )


def test_episode_schedule_requires_timezone_offset():
    with pytest.raises(ValidationError, match="timezone offset"):
        EpisodeRequest(
            programme_id="tvprog_1",
            series_id="tvseries_1",
            title="Naive Schedule",
            episode_number=1,
            editor_sequence_id="seq_1",
            scheduled_at=datetime(2026, 9, 10, 20, 0, 0),
        )


def test_tv_request_models_reject_undeclared_execution_fields():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EpisodeRequest(
            programme_id="tvprog_1",
            series_id="tvseries_1",
            title="Unsafe Request",
            episode_number=1,
            editor_sequence_id="seq_1",
            ffmpeg_args=["-vf", "scale=1280:720"],
        )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        GraphicsPackageRequest(name="Unsafe Package", plugin="example.module")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EpisodePatchRequest(shell="echo example")


def test_shared_skies_handoff_is_prepared_only_and_exposes_no_host_path(tmp_path):
    _, video, _, _ = _editor(tmp_path)
    store = TVProductionStore(tmp_path)
    programme, series = _programme_series(store)
    episode = store.create_episode(
        EpisodeRequest(
            programme_id=programme.id,
            series_id=series.id,
            title="Ready Episode",
            episode_number=1,
            editor_sequence_id=video.id,
        )
    )

    with pytest.raises(ValueError, match="delivery_ready"):
        store.handoff_manifest(episode.id)

    episode = store.patch_episode(
        episode.id,
        EpisodePatchRequest(status="delivery_ready"),
    )
    handoff = store.handoff_manifest(episode.id)
    assert handoff["authority"] == "chat5_shared_skies_transport"
    assert handoff["state"] == "prepared_not_transmitted"
    assert handoff["transmission_requested"] is False
    assert handoff["transport_credentials_present"] is False
    assert handoff["editor_source"]["project_dir_exposed"] is False
    assert "path" not in handoff["editor_source"]
    assert handoff["scheduling"]["transport_schedule_created"] is False


def test_tv_routes_install_once_into_production_editor_family():
    install_professional_editor_patch_guard()
    install_professional_editor_patch_guard()
    for candidate in tv_router.routes:
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
