from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from aura_music_studio import professional_editor_api as api
from aura_music_studio.professional_editor import ProfessionalEditorStore
from aura_music_studio.professional_track_keyframe_authoring import set_track_keyframes


def _store(tmp_path: Path) -> tuple[ProfessionalEditorStore, str, str]:
    project = tmp_path / "TrackKeyframeAuthoringProject"
    store = ProfessionalEditorStore(project)
    store.initialize("TrackKeyframeAuthoringProject")
    sequence = store.create_sequence(
        kind="video",
        name="Automation",
        width=160,
        height=90,
        fps=10.0,
        duration=2.0,
    )
    track = store.create_track(sequence.id, kind="video", name="Picture")
    return store, sequence.id, track.id


def _track(store: ProfessionalEditorStore, track_id: str) -> dict:
    return next(row for row in store.public_state()["branch"]["tracks"] if row["id"] == track_id)


def test_track_keyframe_authoring_sorts_deduplicates_and_records_history(tmp_path):
    store, _sequence_id, track_id = _store(tmp_path)
    result = set_track_keyframes(
        store,
        track_id,
        "opacity",
        [
            {"time": 1.5, "value": 1.0, "interpolation": "linear"},
            {"time": 0.0, "value": 0.0, "interpolation": "hold"},
            {"time": 1.5, "value": 0.75, "interpolation": "smooth"},
        ],
        actor="Wave 10 Test",
    )

    points = result.keyframes["opacity"]
    assert [point.time for point in points] == [0.0, 1.5]
    assert [point.value for point in points] == [0.0, 0.75]
    assert [point.interpolation for point in points] == ["hold", "smooth"]

    state = store.public_state()["branch"]
    operation = state["operations"][-1]
    assert operation["operation"] == "set_keyframes"
    assert operation["target_type"] == "track"
    assert operation["target_id"] == track_id
    assert operation["metadata"] == {
        "parameter": "opacity",
        "keyframes": 3,
        "target": "track",
    }
    assert operation["actor"] == "Wave 10 Test"


def test_track_keyframe_authoring_participates_in_undo_redo(tmp_path):
    store, _sequence_id, track_id = _store(tmp_path)
    set_track_keyframes(
        store,
        track_id,
        "track.opacity",
        [
            {"time": 0.0, "value": 0.2},
            {"time": 1.0, "value": 0.9},
        ],
    )
    assert "track.opacity" in _track(store, track_id)["keyframes"]

    undone = store.undo()
    assert undone.operation == "set_keyframes"
    assert "track.opacity" not in _track(store, track_id)["keyframes"]

    redone = store.redo()
    assert redone.operation == "set_keyframes"
    assert [point["value"] for point in _track(store, track_id)["keyframes"]["track.opacity"]] == [0.2, 0.9]


def test_track_keyframe_authoring_rejects_invalid_path_or_locked_track(tmp_path):
    store, _sequence_id, track_id = _store(tmp_path)
    with pytest.raises(ValueError, match="valid keyframe parameter"):
        set_track_keyframes(store, track_id, "", [])

    store.patch_track(track_id, {"locked": True})
    with pytest.raises(PermissionError, match="Track is locked"):
        set_track_keyframes(store, track_id, "opacity", [{"time": 0.0, "value": 1.0}])


def test_public_editor_api_authors_track_keyframes_and_advertises_capability(tmp_path, monkeypatch):
    store, _sequence_id, track_id = _store(tmp_path)

    class _Plan:
        def has(self, feature: str) -> bool:
            return True

    member = SimpleNamespace(plan=_Plan(), user={"display_name": "Creator"})
    monkeypatch.setattr(api, "_member", lambda request: member)
    monkeypatch.setattr(api, "_store", lambda project_name: store)

    response = api.set_track_keyframes(
        "DemoProject",
        track_id,
        api.KeyframesRequest(
            parameter="opacity",
            keyframes=[
                {"time": 0.0, "value": 0.0, "interpolation": "linear"},
                {"time": 1.0, "value": 1.0, "interpolation": "smooth"},
            ],
        ),
        object(),
    )

    assert response["track"]["id"] == track_id
    assert [point["value"] for point in response["track"]["keyframes"]["opacity"]] == [0.0, 1.0]
    capabilities = response["editor"]["editor_capabilities"]
    assert capabilities["keyframe_targets"] == ["item", "track"]
    assert capabilities["video_track_keyframe_paths"] == ["opacity", "track.opacity"]


def test_public_track_keyframe_api_requires_pro(tmp_path, monkeypatch):
    store, _sequence_id, track_id = _store(tmp_path)

    class _BasicPlan:
        def has(self, feature: str) -> bool:
            return feature != api.AUTOMATION

    member = SimpleNamespace(plan=_BasicPlan(), user={"display_name": "Basic Creator"})
    monkeypatch.setattr(api, "_member", lambda request: member)
    monkeypatch.setattr(api, "_store", lambda project_name: store)

    with pytest.raises(HTTPException) as exc:
        api.set_track_keyframes(
            "DemoProject",
            track_id,
            api.KeyframesRequest(parameter="opacity", keyframes=[]),
            object(),
        )
    assert exc.value.status_code == 403


def test_router_exposes_track_keyframe_endpoint():
    routes = {(route.path, ",".join(sorted(route.methods or []))) for route in api.router.routes}
    assert (
        "/creative/projects/{project_name}/editor/tracks/{track_id}/keyframes",
        "POST",
    ) in routes
