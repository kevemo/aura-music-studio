from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import aura_music_studio.video_audio_analysis_ingestion as ingestion
from aura_music_studio.creative_project import CreativeElement, CreativeProjectStore


def _member_request():
    return SimpleNamespace(state=SimpleNamespace(member=SimpleNamespace(user_id="member-1")))


def test_analysis_markers_are_deterministic_and_source_bound():
    markers = ingestion._analysis_markers("el_audio", {"beat_times": [1.0, 0.5], "onset_times": [0.75], "tempo_bpm": 120})
    assert [item["time_seconds"] for item in markers] == [0.5, 0.75, 1.0]
    assert all(item["source_element_id"] == "el_audio" for item in markers)
    assert {item["kind"] for item in markers} == {"beat", "onset"}


def test_merge_replaces_only_generated_analysis_for_same_element():
    existing = [
        {"id": "analysis.el_audio.beat.00000", "kind": "beat", "time_seconds": 0.1, "end_seconds": None, "text": "", "section_label": "", "source_element_id": "el_audio"},
        {"id": "manual-beat", "kind": "beat", "time_seconds": 0.2, "end_seconds": None, "text": "", "section_label": "", "source_element_id": "el_audio"},
        {"id": "verse", "kind": "section", "time_seconds": 0.0, "end_seconds": 10.0, "text": "", "section_label": "Verse", "source_element_id": None},
    ]
    generated = ingestion._analysis_markers("el_audio", {"beat_times": [0.3], "onset_times": [], "tempo_bpm": 100})
    merged = ingestion._merge_analysis_markers(existing, generated, element_id="el_audio", replace_existing=True)
    ids = {item["id"] for item in merged}
    assert "analysis.el_audio.beat.00000" in ids
    assert "manual-beat" in ids
    assert "verse" in ids
    assert len(merged) == 3


def test_resolver_rejects_non_project_source(monkeypatch, tmp_path):
    project = tmp_path / "song"
    project.mkdir()
    store = CreativeProjectStore(project)
    manifest = store.initialize(project_name="song", title="Song")
    manifest.elements.append(CreativeElement(id="el_audio", kind="audio", label="Track", status="ready", source_ref="../outside.wav"))
    store.save(manifest)
    monkeypatch.setattr(ingestion, "_project_dir", lambda name: project)
    with pytest.raises(Exception) as exc:
        ingestion._resolve_project_audio("song", "el_audio")
    assert getattr(exc.value, "status_code", None) == 400


def test_endpoint_authenticates_before_project_lookup(monkeypatch):
    called = {"project": False}
    def deny(_request):
        from fastapi import HTTPException
        raise HTTPException(401, "auth")
    def project(_name):
        called["project"] = True
        raise AssertionError
    monkeypatch.setattr(ingestion, "_member_identity", deny)
    monkeypatch.setattr(ingestion, "_project_dir", project)
    with pytest.raises(Exception) as exc:
        ingestion.analyze_project_audio("song", ingestion.AnalyzeProjectAudioRequest(element_id="el_audio"), _member_request())
    assert getattr(exc.value, "status_code", None) == 401
    assert called["project"] is False


def test_router_dispatch_is_auth_gated():
    app = FastAPI()
    app.include_router(ingestion.router)
    response = TestClient(app).post("/creative/projects/song/video-music-sync/analyze-audio", json={"element_id": "el_audio"})
    assert response.status_code == 401
