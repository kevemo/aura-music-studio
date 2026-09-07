from __future__ import annotations

from pathlib import Path

import pytest

from aura_music_studio.aura_effect_system_automation import (
    clear_effect_system_mix_automation,
    effect_system_mix_automation,
    restore_effect_system_automation_revision,
    set_effect_system_mix_automation,
)
from aura_music_studio.daw import load_session
from aura_music_studio.session import Effect, StudioSession


FINGERPRINT = "a" * 64
SYSTEM_ID = "aura.test.system"
NODE_ID = "wet"
EFFECT_ID = f"system.{FINGERPRINT[:12]}.{NODE_ID}"


def _project(tmp_path: Path) -> tuple[Path, str]:
    project = tmp_path / "project"
    project.mkdir()
    session = StudioSession(name="Automation test")
    session.add_track("Master", "master")
    track = session.add_track("Vocal", "vocals")
    track.effects.append(Effect(id=EFFECT_ID, type="reverb", mix=0.5, parameters={"mix": 0.5}))
    track.metadata["effect_systems"] = [
        {
            "system_id": SYSTEM_ID,
            "name": "Test system",
            "version": 3,
            "fingerprint": FINGERPRINT,
            "effect_ids": [EFFECT_ID],
            "revision_before_apply": "prior",
        }
    ]
    session.save(project / "aura_session.json")
    return project, track.id


def test_set_mix_automation_uses_renderer_supported_fx_lane_and_revision(tmp_path: Path):
    project, track_id = _project(tmp_path)
    result = set_effect_system_mix_automation(
        project,
        track_id,
        SYSTEM_ID,
        NODE_ID,
        [{"time": 0, "value": 0.2}, {"time": 4.5, "value": 0.9}],
        interpolation="smooth",
    )

    assert result["updated"] is True
    assert result["parameter"] == f"fx:{EFFECT_ID}:mix"
    assert result["interpolation"] == "smooth"
    assert result["revision_id"]
    assert result["source_media_mutated"] is False

    session = load_session(project)
    track = session.find_track(track_id)
    assert len(track.automation) == 1
    lane = track.automation[0]
    assert lane.parameter == f"fx:{EFFECT_ID}:mix"
    assert [(point.time, point.value) for point in lane.points] == [(0.0, 0.2), (4.5, 0.9)]


def test_mix_automation_clamps_values_sorts_points_and_deduplicates_times(tmp_path: Path):
    project, track_id = _project(tmp_path)
    result = set_effect_system_mix_automation(
        project,
        track_id,
        SYSTEM_ID,
        NODE_ID,
        [
            {"time": 8, "value": 2.0},
            {"time": -2, "value": -1.0},
            {"time": 8, "value": 0.4},
        ],
    )
    assert result["points"] == [{"time": 0.0, "value": 0.0}, {"time": 8.0, "value": 0.4}]


def test_updating_same_node_replaces_lane_instead_of_duplicating_it(tmp_path: Path):
    project, track_id = _project(tmp_path)
    set_effect_system_mix_automation(project, track_id, SYSTEM_ID, NODE_ID, [{"time": 0, "value": 0.1}])
    set_effect_system_mix_automation(project, track_id, SYSTEM_ID, NODE_ID, [{"time": 1, "value": 0.8}])

    track = load_session(project).find_track(track_id)
    assert len(track.automation) == 1
    assert [(point.time, point.value) for point in track.automation[0].points] == [(1.0, 0.8)]


def test_get_reports_current_lane_without_mutating_project(tmp_path: Path):
    project, track_id = _project(tmp_path)
    before = (project / "aura_session.json").read_text(encoding="utf-8")
    state = effect_system_mix_automation(project, track_id, SYSTEM_ID, NODE_ID)
    after = (project / "aura_session.json").read_text(encoding="utf-8")

    assert state["exists"] is False
    assert state["system_version"] == 3
    assert state["system_fingerprint"] == FINGERPRINT
    assert state["source_media_mutated"] is False
    assert before == after


def test_clear_is_idempotent_and_clear_can_be_undone_by_revision_restore(tmp_path: Path):
    project, track_id = _project(tmp_path)
    applied = set_effect_system_mix_automation(
        project,
        track_id,
        SYSTEM_ID,
        NODE_ID,
        [{"time": 0, "value": 0.25}, {"time": 2, "value": 0.75}],
    )
    assert applied["revision_id"]

    cleared = clear_effect_system_mix_automation(project, track_id, SYSTEM_ID, NODE_ID)
    assert cleared["cleared"] is True
    assert load_session(project).find_track(track_id).automation == []

    restored = restore_effect_system_automation_revision(project, cleared["revision_id"])
    assert restored["effect_system_automation_undo"] is True
    lane = load_session(project).find_track(track_id).automation[0]
    assert [(point.time, point.value) for point in lane.points] == [(0.0, 0.25), (2.0, 0.75)]

    cleared_again = clear_effect_system_mix_automation(project, track_id, SYSTEM_ID, NODE_ID)
    assert cleared_again["cleared"] is True
    already = clear_effect_system_mix_automation(project, track_id, SYSTEM_ID, NODE_ID)
    assert already["cleared"] is False
    assert already["already_clear"] is True
    assert already["revision_id"] is None


def test_invalid_interpolation_and_non_finite_points_fail_before_mutation(tmp_path: Path):
    project, track_id = _project(tmp_path)
    before = (project / "aura_session.json").read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="Interpolation"):
        set_effect_system_mix_automation(
            project, track_id, SYSTEM_ID, NODE_ID, [{"time": 0, "value": 0.5}], interpolation="bezier"
        )
    with pytest.raises(ValueError, match="finite"):
        set_effect_system_mix_automation(
            project, track_id, SYSTEM_ID, NODE_ID, [{"time": float("inf"), "value": 0.5}]
        )

    assert (project / "aura_session.json").read_text(encoding="utf-8") == before


def test_missing_applied_node_or_removed_effect_fails_closed(tmp_path: Path):
    project, track_id = _project(tmp_path)
    before = (project / "aura_session.json").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="not found"):
        set_effect_system_mix_automation(project, track_id, SYSTEM_ID, "missing", [{"time": 0, "value": 0.5}])
    assert (project / "aura_session.json").read_text(encoding="utf-8") == before

    session = load_session(project)
    track = session.find_track(track_id)
    track.effects = []
    session.save(project / "aura_session.json")
    with pytest.raises(ValueError, match="no longer present"):
        set_effect_system_mix_automation(project, track_id, SYSTEM_ID, NODE_ID, [{"time": 0, "value": 0.5}])


def test_point_count_and_identifier_guards_are_bounded(tmp_path: Path):
    project, track_id = _project(tmp_path)
    with pytest.raises(ValueError, match="2000"):
        set_effect_system_mix_automation(
            project,
            track_id,
            SYSTEM_ID,
            NODE_ID,
            [{"time": index, "value": 0.5} for index in range(2001)],
        )
    with pytest.raises(ValueError, match="unsupported characters"):
        effect_system_mix_automation(project, track_id, "../../escape", NODE_ID)
