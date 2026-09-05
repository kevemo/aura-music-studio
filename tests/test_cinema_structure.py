from __future__ import annotations

import pytest
from pydantic import ValidationError

from aura_music_studio.cinema_production import (
    CinemaProductionStore,
    FilmRequest,
    SceneRequest,
    ShotRequest,
)
from aura_music_studio.cinema_structure import (
    ActRequest,
    CharacterRequest,
    CinemaStructureStore,
    CutStateRequest,
    DeliveryVariantRequest,
    NarrativeSequenceRequest,
    RightsRequest,
    SceneAssignmentRequest,
    TakeRequest,
    router as cinema_structure_router,
)
from aura_music_studio.professional_editor import ProfessionalEditorStore
from aura_music_studio.professional_editor_api import router as professional_editor_router
from aura_music_studio.professional_editor_security_overlay import install_professional_editor_patch_guard


def _project(tmp_path):
    editor = ProfessionalEditorStore(tmp_path)
    editor.initialize("cinema-structure-test")
    master = editor.create_sequence(
        kind="video",
        name="Feature Master",
        width=4096,
        height=2160,
        fps=24.0,
        duration=5400.0,
    )
    take_a = editor.create_sequence(
        kind="video",
        name="Take A",
        width=4096,
        height=2160,
        fps=24.0,
        duration=9.0,
    )
    take_b = editor.create_sequence(
        kind="video",
        name="Take B",
        width=4096,
        height=2160,
        fps=24.0,
        duration=10.0,
    )
    board = editor.create_sequence(
        kind="image",
        name="Board",
        width=1920,
        height=1080,
        fps=1.0,
        duration=1.0,
    )
    production = CinemaProductionStore(tmp_path)
    film = production.create_film(
        FilmRequest(title="Starbound", format="feature", master_sequence_id=master.id)
    )
    scene = production.create_scene(
        film.id,
        SceneRequest(scene_number=1, slugline="EXT. STARS - NIGHT"),
    )
    shot = production.create_shot(
        scene.id,
        ShotRequest(shot_number=1, title="Opening Wide", shot_size="extreme_wide"),
    )
    return production, film, scene, shot, master, take_a, take_b, board


def test_acts_sequences_and_scene_assignment_reference_canonical_cinema(tmp_path):
    _, film, scene, _, _, _, _, _ = _project(tmp_path)
    store = CinemaStructureStore(tmp_path)
    act = store.create_act(film.id, ActRequest(act_number=1, title="Departure"))
    sequence = store.create_sequence(
        act.id,
        NarrativeSequenceRequest(sequence_number=1, title="The Signal"),
    )
    sequence = store.assign_scene(sequence.id, scene.id)

    saved = store.load()
    assert store.path == tmp_path / "work" / "cinema_structure.json"
    assert saved.acts[0].sequence_ids == [sequence.id]
    assert saved.narrative_sequences[0].scene_ids == [scene.id]
    assert saved.narrative_sequences[0].film_id == film.id


def test_scene_cannot_be_assigned_to_multiple_narrative_sequences(tmp_path):
    _, film, scene, _, _, _, _, _ = _project(tmp_path)
    store = CinemaStructureStore(tmp_path)
    act = store.create_act(film.id, ActRequest(act_number=1, title="Act One"))
    first = store.create_sequence(act.id, NarrativeSequenceRequest(sequence_number=1, title="First"))
    second = store.create_sequence(act.id, NarrativeSequenceRequest(sequence_number=2, title="Second"))
    store.assign_scene(first.id, scene.id)
    with pytest.raises(ValueError, match="cannot belong to multiple"):
        store.assign_scene(second.id, scene.id)


def test_character_visual_identity_is_safe_canonical_reference_only(tmp_path):
    _, film, _, _, _, _, _, _ = _project(tmp_path)
    store = CinemaStructureStore(tmp_path)
    character = store.create_character(
        film.id,
        CharacterRequest(
            character_key="captain-lyra",
            name="Captain Lyra",
            visual_identity_asset_id="asset.visual.lyra.v1",
            wardrobe_baseline="Gold flight jacket",
            performance_baseline="Restrained and focused",
        ),
    )
    assert character.visual_identity_asset_id == "asset.visual.lyra.v1"

    with pytest.raises(ValueError, match="canonical identifier"):
        store.create_character(
            film.id,
            CharacterRequest(
                character_key="unsafe",
                name="Unsafe",
                visual_identity_asset_id="../../private/model.glb",
            ),
        )
    with pytest.raises(ValueError, match="canonical identifier"):
        store.create_character(
            film.id,
            CharacterRequest(
                character_key="url",
                name="URL",
                visual_identity_asset_id="https://example.invalid/model.glb",
            ),
        )


def test_takes_are_video_sequence_bound_and_selected_take_is_unique(tmp_path):
    _, _, _, shot, _, take_a, take_b, board = _project(tmp_path)
    store = CinemaStructureStore(tmp_path)
    first = store.create_take(
        shot.id,
        TakeRequest(
            take_number=1,
            editor_sequence_id=take_a.id,
            source_asset_id="asset.video.take1",
            status="selected",
            rating=4,
        ),
    )
    second = store.create_take(
        shot.id,
        TakeRequest(
            take_number=2,
            editor_sequence_id=take_b.id,
            source_asset_id="asset.video.take2",
            status="captured",
            rating=5,
        ),
    )
    selected = store.select_take(second.id)
    saved = store.load()
    statuses = {row.id: row.status for row in saved.takes}
    assert selected.status == "selected"
    assert statuses[second.id] == "selected"
    assert statuses[first.id] == "circle"

    with pytest.raises(ValueError, match="requires a video editor sequence"):
        store.create_take(
            shot.id,
            TakeRequest(take_number=3, editor_sequence_id=board.id),
        )


def test_duplicate_act_sequence_and_take_numbers_fail(tmp_path):
    _, film, _, shot, _, take_a, _, _ = _project(tmp_path)
    store = CinemaStructureStore(tmp_path)
    act = store.create_act(film.id, ActRequest(act_number=1, title="One"))
    with pytest.raises(ValueError, match="act number already exists"):
        store.create_act(film.id, ActRequest(act_number=1, title="Duplicate"))
    store.create_sequence(act.id, NarrativeSequenceRequest(sequence_number=1, title="One"))
    with pytest.raises(ValueError, match="sequence number already exists"):
        store.create_sequence(act.id, NarrativeSequenceRequest(sequence_number=1, title="Duplicate"))
    store.create_take(shot.id, TakeRequest(take_number=1, editor_sequence_id=take_a.id))
    with pytest.raises(ValueError, match="take number already exists"):
        store.create_take(shot.id, TakeRequest(take_number=1))


def test_picture_lock_targets_master_and_requires_explicit_unlock(tmp_path):
    _, film, _, _, master, take_a, _, _ = _project(tmp_path)
    store = CinemaStructureStore(tmp_path)
    with pytest.raises(ValueError, match="must target the film master sequence"):
        store.set_cut_state(
            film.id,
            CutStateRequest(state="picture_locked", editor_sequence_id=take_a.id),
        )

    locked = store.set_cut_state(
        film.id,
        CutStateRequest(state="picture_locked", editor_sequence_id=master.id, notes="Approved picture"),
    )
    assert locked.picture_locked_at
    with pytest.raises(ValueError, match="unlock_picture=true"):
        store.set_cut_state(
            film.id,
            CutStateRequest(state="fine_cut", editor_sequence_id=master.id),
        )
    rollback = store.set_cut_state(
        film.id,
        CutStateRequest(state="fine_cut", editor_sequence_id=master.id, unlock_picture=True),
    )
    assert rollback.state == "fine_cut"


def test_delivery_variants_validate_video_and_custom_aspect_ratio(tmp_path):
    _, film, _, _, master, _, _, board = _project(tmp_path)
    store = CinemaStructureStore(tmp_path)
    variant = store.create_delivery_variant(
        film.id,
        DeliveryVariantRequest(
            purpose="trailer",
            label="Vertical Trailer",
            editor_sequence_id=master.id,
            aspect_ratio="9:16",
            captions=True,
        ),
    )
    assert variant.purpose == "trailer"
    assert variant.aspect_ratio == "9:16"

    with pytest.raises(ValueError, match="requires a video editor sequence"):
        store.create_delivery_variant(
            film.id,
            DeliveryVariantRequest(
                purpose="teaser",
                label="Wrong Media",
                editor_sequence_id=board.id,
            ),
        )
    with pytest.raises(ValueError, match="Custom aspect ratio"):
        store.create_delivery_variant(
            film.id,
            DeliveryVariantRequest(
                purpose="alternate",
                label="Custom Missing",
                editor_sequence_id=master.id,
                aspect_ratio="custom",
            ),
        )


def test_rights_state_is_computed_and_readiness_fails_closed(tmp_path):
    _, film, _, _, master, _, _, _ = _project(tmp_path)
    store = CinemaStructureStore(tmp_path)
    blocked = store.add_rights_record(
        film.id,
        RightsRequest(
            resource_type="visual_likeness",
            asset_id="asset.likeness.person1",
            rights_state="owned",
            consent_state="pending",
            evidence_ref="consent.case.123",
        ),
    )
    assert blocked.commercial_use_allowed is False
    readiness = store.readiness(film.id)
    assert readiness["commercial_rights_clear_from_recorded_evidence"] is False
    assert readiness["rights_blockers"][0]["asset_id"] == "asset.likeness.person1"
    assert readiness["truth"] == "planning_readiness_only_not_distribution_clearance"

    cleared = store.add_rights_record(
        film.id,
        RightsRequest(
            resource_type="visual_likeness",
            asset_id="asset.likeness.person1",
            rights_state="licensed",
            consent_state="granted",
            evidence_ref="consent.case.124",
        ),
    )
    assert cleared.commercial_use_allowed is True
    store.set_cut_state(film.id, CutStateRequest(state="picture_locked", editor_sequence_id=master.id))
    readiness = store.readiness(film.id)
    assert readiness["commercial_rights_clear_from_recorded_evidence"] is True
    assert readiness["picture_locked"] is True


def test_shot_list_reports_selected_take_without_duplicate_shot_objects(tmp_path):
    _, film, scene, shot, _, take_a, _, _ = _project(tmp_path)
    store = CinemaStructureStore(tmp_path)
    store.create_take(
        shot.id,
        TakeRequest(take_number=1, editor_sequence_id=take_a.id, status="selected"),
    )
    result = store.shot_list(film.id)
    assert result["count"] == 1
    assert result["shots"][0]["scene_id"] == scene.id
    assert result["shots"][0]["shot_id"] == shot.id
    assert result["shots"][0]["selected_take_id"] is not None


def test_structure_requests_reject_execution_and_raw_voice_model_fields():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TakeRequest(take_number=1, ffmpeg_args=["-vf", "scale=1:1"])
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CharacterRequest(
            character_key="hero",
            name="Hero",
            raw_voice_embedding="secret",
        )
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RightsRequest(
            resource_type="video",
            asset_id="asset.video.1",
            rights_state="owned",
            shell="echo nope",
        )


def test_scene_assignment_request_is_strict():
    request = SceneAssignmentRequest(scene_id="scene_123")
    assert request.scene_id == "scene_123"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SceneAssignmentRequest(scene_id="scene_123", path="/tmp/private")


def test_cinema_structure_routes_install_once_into_production_editor_family():
    install_professional_editor_patch_guard()
    install_professional_editor_patch_guard()
    for candidate in cinema_structure_router.routes:
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
