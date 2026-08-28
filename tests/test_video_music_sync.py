from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from aura_music_studio import video_music_sync as sync


def _request():
    return SimpleNamespace(state=SimpleNamespace(member=SimpleNamespace(user_id="member-1", plan=SimpleNamespace(id="pro"))))


def test_lyric_and_section_markers_require_content():
    with pytest.raises(ValidationError):
        sync.SyncMarker(id="lyric-1", kind="lyric", time_seconds=1.0)
    with pytest.raises(ValidationError):
        sync.SyncMarker(id="section-1", kind="section", time_seconds=1.0)


def test_replace_map_sorts_and_rejects_duplicate_ids(monkeypatch):
    monkeypatch.setattr(sync, "_member_identity", lambda request: ("member-1", "pro"))
    written = {}
    monkeypatch.setattr(sync, "_write", lambda project, data: written.setdefault("data", {"schema_version": 1, "project_name": project, "updated_at": "now", **data}))
    body = sync.ReplaceSyncMapRequest(markers=[
        sync.SyncMarker(id="b", kind="beat", time_seconds=2.0),
        sync.SyncMarker(id="a", kind="beat", time_seconds=1.0),
    ])
    response = sync.replace_video_music_sync("demo", body, _request())
    assert [item["id"] for item in response["markers"]] == ["a", "b"]
    assert response["raw_filesystem_paths_exposed"] is False
    with pytest.raises(HTTPException) as exc:
        sync._validate_markers([{"id": "x"}, {"id": "x"}])
    assert exc.value.status_code == 400


def test_snap_plan_is_deterministic_and_does_not_mutate(monkeypatch):
    monkeypatch.setattr(sync, "_member_identity", lambda request: ("member-1", "pro"))
    monkeypatch.setattr(sync, "_read", lambda project: {"markers": [
        {"id": "beat-1", "kind": "beat", "time_seconds": 1.0},
        {"id": "lyric-1", "kind": "lyric", "time_seconds": 3.0, "text": "line"},
    ]})
    monkeypatch.setattr(sync, "read_timeline", lambda project: {"scenes": [
        {"id": "s1", "start_seconds": 0.9, "end_seconds": 3.1},
    ]})
    response = sync.plan_scene_boundary_snaps("demo", sync.SnapPlanRequest(max_distance_seconds=0.2), _request())
    assert [(x["boundary"], x["marker_id"]) for x in response["suggestions"]] == [("start", "beat-1"), ("end", "lyric-1")]
    assert response["timeline_mutated"] is False
    assert response["frame_accurate_renderer_sync_guaranteed"] is False


def test_apply_snaps_validates_candidate_before_atomic_write(monkeypatch):
    monkeypatch.setattr(sync, "_member_identity", lambda request: ("member-1", "pro"))
    monkeypatch.setattr(sync, "_read", lambda project: {"markers": [{"id": "beat-1", "kind": "beat", "time_seconds": 1.0}]})
    timeline = {"scenes": [{"id": "s1", "start_seconds": 0.0, "end_seconds": 2.0, "order": 0}]}
    monkeypatch.setattr(sync, "read_timeline", lambda project: timeline)
    checked = []
    monkeypatch.setattr(sync, "_validate_timeline", lambda scenes: checked.append([dict(x) for x in scenes]))
    monkeypatch.setattr(sync, "write_timeline", lambda project, data: data)
    body = sync.ApplySnapRequest(snaps=[sync.SceneBoundarySnap(scene_id="s1", boundary="start", marker_id="beat-1")])
    response = sync.apply_scene_boundary_snaps("demo", body, _request())
    assert checked and checked[0][0]["start_seconds"] == 1.0
    assert response["applied"][0]["from_seconds"] == 0.0
    assert response["applied"][0]["to_seconds"] == 1.0
    assert timeline["scenes"][0]["start_seconds"] == 0.0
    assert response["grants_esp_role_or_permission"] is False
    assert response["alters_billing_or_membership"] is False


def test_authentication_happens_before_project_lookup(monkeypatch):
    def deny(request):
        raise HTTPException(401, "auth first")
    monkeypatch.setattr(sync, "_member_identity", deny)
    monkeypatch.setattr(sync, "_read", lambda project: (_ for _ in ()).throw(AssertionError("project lookup must not occur")))
    app = FastAPI()
    app.include_router(sync.router)
    client = TestClient(app)
    response = client.get("/creative/projects/private/video-music-sync")
    assert response.status_code == 401
    assert response.json()["detail"] == "auth first"


def test_production_overlay_dispatches_sync_route_with_authentication():
    from aura_music_studio.creative_version_autopromotion import router as production_router
    app = FastAPI()
    app.include_router(production_router)
    client = TestClient(app)
    response = client.get("/creative/projects/demo/video-music-sync")
    assert response.status_code == 401
