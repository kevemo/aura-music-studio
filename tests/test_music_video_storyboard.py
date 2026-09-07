from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from aura_music_studio import music_video_storyboard as storyboard


def _request():
    return SimpleNamespace(state=SimpleNamespace(member=SimpleNamespace(user_id="member-1", plan=SimpleNamespace(id="pro"))))


def _sync():
    return {
        "markers": [
            {"id": "sec-1", "kind": "section", "time_seconds": 0.0, "section_label": "Verse"},
            {"id": "lyric-1", "kind": "lyric", "time_seconds": 1.0, "text": "First line"},
            {"id": "sec-2", "kind": "section", "time_seconds": 4.0, "end_seconds": 8.0, "section_label": "Chorus"},
            {"id": "lyric-2", "kind": "lyric", "time_seconds": 5.0, "text": "Big chorus"},
        ]
    }


def test_storyboard_plan_uses_sections_lyrics_and_does_not_mutate(monkeypatch):
    monkeypatch.setattr(storyboard, "_member_identity", lambda request: ("member-1", "pro"))
    monkeypatch.setattr(storyboard, "read_sync_map", lambda project: _sync())
    plan = storyboard.plan_music_video_storyboard(
        "demo",
        storyboard.StoryboardPlanRequest(visual_direction="Cosmic purple and gold performance imagery."),
        _request(),
    )
    assert [(x["start_seconds"], x["end_seconds"]) for x in plan["scenes"]] == [(0.0, 4.0), (4.0, 8.0)]
    assert "First line" in plan["scenes"][0]["description"]
    assert plan["scenes"][0]["id"] == "mv-01"
    assert plan["timeline_mutated"] is False
    assert plan["renderer_invoked"] is False
    assert plan["grants_esp_role_or_permission"] is False
    assert plan["alters_billing_or_membership"] is False


def test_final_section_requires_explicit_end(monkeypatch):
    monkeypatch.setattr(storyboard, "read_sync_map", lambda project: {"markers": [{"id": "sec", "kind": "section", "time_seconds": 2.0, "section_label": "Outro"}]})
    with pytest.raises(HTTPException) as exc:
        storyboard._build_storyboard("demo", storyboard.StoryboardPlanRequest())
    assert exc.value.status_code == 400
    plan = storyboard._build_storyboard("demo", storyboard.StoryboardPlanRequest(project_end_seconds=7.5))
    assert plan["scenes"][0]["end_seconds"] == 7.5


def test_apply_refuses_to_overwrite_existing_timeline_without_explicit_replace(monkeypatch):
    monkeypatch.setattr(storyboard, "_member_identity", lambda request: ("member-1", "pro"))
    monkeypatch.setattr(storyboard, "read_timeline", lambda project: {"scenes": [{"id": "existing"}]})
    with pytest.raises(HTTPException) as exc:
        storyboard.apply_music_video_storyboard("demo", storyboard.StoryboardApplyRequest(), _request())
    assert exc.value.status_code == 409


def test_apply_validates_candidate_before_write(monkeypatch):
    monkeypatch.setattr(storyboard, "_member_identity", lambda request: ("member-1", "pro"))
    monkeypatch.setattr(storyboard, "read_timeline", lambda project: {"scenes": []})
    monkeypatch.setattr(storyboard, "read_sync_map", lambda project: _sync())
    checked = []
    written = []
    monkeypatch.setattr(storyboard, "_validate_timeline", lambda scenes: checked.append([dict(x) for x in scenes]))
    monkeypatch.setattr(storyboard, "write_timeline", lambda project, data: written.append(data) or data)
    result = storyboard.apply_music_video_storyboard("demo", storyboard.StoryboardApplyRequest(), _request())
    assert len(checked) >= 2
    assert written and written[0]["scenes"][0]["label"] == "Verse"
    assert result["timeline_mutated"] is True
    assert result["existing_timeline_replaced"] is False
    assert result["renderer_invoked"] is False


def test_authentication_happens_before_project_lookup(monkeypatch):
    def deny(request):
        raise HTTPException(401, "auth first")
    monkeypatch.setattr(storyboard, "_member_identity", deny)
    monkeypatch.setattr(storyboard, "read_sync_map", lambda project: (_ for _ in ()).throw(AssertionError("lookup must not occur")))
    app = FastAPI()
    app.include_router(storyboard.router)
    response = TestClient(app).post("/creative/projects/private/music-video/storyboard-plan", json={})
    assert response.status_code == 401
    assert response.json()["detail"] == "auth first"


def test_production_overlay_dispatches_storyboard_route_with_authentication():
    from aura_music_studio.creative_version_autopromotion import router as production_router
    app = FastAPI()
    app.include_router(production_router)
    response = TestClient(app).post("/creative/projects/demo/music-video/storyboard-plan", json={})
    assert response.status_code == 401
