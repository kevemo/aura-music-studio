from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from aura_music_studio.plans import get_plan
from aura_music_studio import video_verified_captions as subject


def _request(plan_id: str = "pro"):
    return SimpleNamespace(
        state=SimpleNamespace(
            member=SimpleNamespace(user_id="member-1", plan=get_plan(plan_id))
        )
    )


def _sync(markers):
    return {
        "schema_version": 1,
        "project_name": "song-a",
        "updated_at": "now",
        "markers": markers,
    }


def test_caption_builder_uses_only_verified_song_dna_bridge_markers(monkeypatch):
    monkeypatch.setattr(subject, "read_sync", lambda _project: _sync([
        {
            "id": "songdna.lyric.verified",
            "kind": "lyric",
            "time_seconds": 1.25,
            "end_seconds": 2.5,
            "text": "  Verified\n lyric  ",
            "section_label": "",
            "source_element_id": "song-dna-verified-lyrics",
        },
        {
            "id": "manual-lyric",
            "kind": "lyric",
            "time_seconds": 3.0,
            "end_seconds": 4.0,
            "text": "Manual lyric",
            "section_label": "",
            "source_element_id": None,
        },
        {
            "id": "beat",
            "kind": "beat",
            "time_seconds": 4.5,
            "end_seconds": None,
            "text": "",
            "section_label": "",
            "source_element_id": None,
        },
    ]))

    cues, evidence = subject._verified_caption_cues("song-a")

    assert cues == [{
        "id": "songdna.lyric.verified",
        "start_seconds": 1.25,
        "end_seconds": 2.5,
        "text": "Verified lyric",
    }]
    assert evidence["verified_timing_only"] is True
    assert evidence["estimated_timing_used"] is False


def test_caption_builder_requires_complete_positive_ranges(monkeypatch):
    monkeypatch.setattr(subject, "read_sync", lambda _project: _sync([
        {
            "id": "missing-end",
            "kind": "lyric",
            "time_seconds": 1.0,
            "end_seconds": None,
            "text": "No end",
            "source_element_id": "song-dna-verified-lyrics",
        },
        {
            "id": "bad-range",
            "kind": "lyric",
            "time_seconds": 2.0,
            "end_seconds": 2.0,
            "text": "Zero duration",
            "source_element_id": "song-dna-verified-lyrics",
        },
    ]))

    with pytest.raises(HTTPException) as exc:
        subject._verified_caption_cues("song-a")

    assert exc.value.status_code == 409


def test_srt_and_vtt_serialization_is_deterministic():
    cues = [{"id": "a", "start_seconds": 1.234, "end_seconds": 62.5, "text": "Hello world"}]

    assert subject._render_srt(cues) == "1\n00:00:01,234 --> 00:01:02,500\nHello world\n"
    assert subject._render_vtt(cues) == "WEBVTT\n\n00:00:01.234 --> 00:01:02.500\nHello world\n"


def test_basic_is_rejected_before_sync_or_project_lookup(monkeypatch):
    monkeypatch.setattr(subject, "read_sync", lambda _project: (_ for _ in ()).throw(AssertionError("sync lookup must not run")))

    with pytest.raises(HTTPException) as exc:
        subject.preview_verified_captions("secret-project", _request("base"))

    assert exc.value.status_code == 403


def test_pro_preview_exposes_no_paths_or_role_mutation(monkeypatch):
    monkeypatch.setattr(subject, "_require_pro_video_sync", lambda _request: object())
    monkeypatch.setattr(subject, "_verified_caption_cues", lambda _project: ([{
        "id": "a", "start_seconds": 1.0, "end_seconds": 2.0, "text": "Lyric"
    }], {
        "source": "song-dna-verified-lyrics",
        "verified_timing_only": True,
        "estimated_timing_used": False,
        "cue_count": 1,
        "skipped_without_end": 0,
        "skipped_invalid_range": 0,
    }))

    result = subject.preview_verified_captions("song-a", _request("pro"))

    assert result["raw_filesystem_paths_exposed"] is False
    assert result["grants_esp_role_or_permission"] is False
    assert result["alters_billing_membership_or_creation_coins"] is False


def test_export_has_download_safety_headers(monkeypatch):
    monkeypatch.setattr(subject, "_require_pro_video_sync", lambda _request: object())
    monkeypatch.setattr(subject, "_verified_caption_cues", lambda _project: ([{
        "id": "a", "start_seconds": 1.0, "end_seconds": 2.0, "text": "Lyric"
    }], {}))

    response = subject.export_verified_captions("song-a", "srt", _request("pro"))

    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"] == 'attachment; filename="pulsar-verified-lyrics.srt"'
    assert b"00:00:01,000 --> 00:00:02,000" in response.body


def test_route_requires_authentication_through_production_sync_mount():
    from aura_music_studio.video_lyric_sync_ingestion import router as production_router

    app = FastAPI()
    app.include_router(production_router)
    response = TestClient(app).get("/creative/projects/example/video-captions/verified")
    assert response.status_code == 401
