from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from aura_music_studio.plans import get_plan
from aura_music_studio import video_lyric_sync_ingestion as subject


def _request(plan_id: str = "pro"):
    return SimpleNamespace(
        state=SimpleNamespace(
            member=SimpleNamespace(user_id="member-1", plan=get_plan(plan_id))
        )
    )


def test_verified_marker_builder_excludes_estimated_and_unaligned(monkeypatch):
    lines = [
        SimpleNamespace(
            id="verified-line",
            order=0,
            text="Verified lyric",
            start_seconds=1.25,
            end_seconds=2.5,
            metadata={"alignment_state": "verified"},
        ),
        SimpleNamespace(
            id="forced-line",
            order=1,
            text="Forced lyric",
            start_seconds=3.0,
            end_seconds=4.0,
            metadata={"alignment_state": "forced_aligned"},
        ),
        SimpleNamespace(
            id="estimate-line",
            order=2,
            text="Estimate only",
            start_seconds=5.0,
            end_seconds=6.0,
            metadata={"alignment_state": "estimated"},
        ),
        SimpleNamespace(
            id="unaligned-line",
            order=3,
            text="No timing",
            start_seconds=None,
            end_seconds=None,
            metadata={},
        ),
    ]
    dna = SimpleNamespace(version=7, lyric_lines=lines)
    monkeypatch.setattr(subject, "_project_dir", lambda _name: object())
    monkeypatch.setattr(subject, "SongDNAStore", lambda _project: SimpleNamespace(load=lambda: dna))

    markers, evidence = subject._verified_lyric_markers("song-a")

    assert [item["text"] for item in markers] == ["Verified lyric", "Forced lyric"]
    assert all(item["source_element_id"] == "song-dna-verified-lyrics" for item in markers)
    assert evidence["song_dna_version"] == 7
    assert evidence["skipped_unverified"] == 2


def test_import_replaces_only_bridge_generated_lyrics_and_preserves_manual_markers(monkeypatch):
    monkeypatch.setattr(subject, "_member_identity", lambda _request: "member-1")
    generated = [{
        "id": "songdna.lyric.new",
        "kind": "lyric",
        "time_seconds": 2.0,
        "end_seconds": 3.0,
        "text": "New verified lyric",
        "section_label": "",
        "source_element_id": "song-dna-verified-lyrics",
    }]
    monkeypatch.setattr(
        subject,
        "_verified_lyric_markers",
        lambda _project: (generated, {"song_dna_version": 2, "eligible_alignment_states": ["forced_aligned", "verified"], "skipped_unverified": 0, "skipped_untimed": 0}),
    )
    existing = {
        "schema_version": 1,
        "project_name": "song-a",
        "updated_at": "old",
        "markers": [
            {"id": "manual-beat", "kind": "beat", "time_seconds": 1.0, "end_seconds": None, "text": "", "section_label": "", "source_element_id": None},
            {"id": "manual-lyric", "kind": "lyric", "time_seconds": 1.5, "end_seconds": 1.9, "text": "Keep me", "section_label": "", "source_element_id": None},
            {"id": "songdna.lyric.old", "kind": "lyric", "time_seconds": 1.8, "end_seconds": 2.0, "text": "Old generated", "section_label": "", "source_element_id": "song-dna-verified-lyrics"},
        ],
    }
    monkeypatch.setattr(subject, "read_sync", lambda _project: existing)
    written = {}

    def fake_write(project, payload):
        written["project"] = project
        written["payload"] = payload
        return {"schema_version": 1, "project_name": project, "updated_at": "new", **payload}

    monkeypatch.setattr(subject, "write_sync", fake_write)

    result = subject.import_verified_lyrics("song-a", _request("pro"))

    ids = [item["id"] for item in written["payload"]["markers"]]
    assert ids == ["manual-beat", "manual-lyric", "songdna.lyric.new"]
    assert result["replaced_generated_markers"] == 1
    assert result["preserved_existing_markers"] == 2
    assert result["estimated_timings_imported"] is False
    assert result["alters_billing_membership_or_creation_coins"] is False


def test_basic_is_rejected_before_project_or_song_dna_lookup(monkeypatch):
    monkeypatch.setattr(subject, "_member_identity", lambda _request: "member-1")
    monkeypatch.setattr(subject, "_project_dir", lambda _name: (_ for _ in ()).throw(AssertionError("project lookup must not run")))

    with pytest.raises(HTTPException) as exc:
        subject.import_verified_lyrics("secret-project", _request("base"))

    assert exc.value.status_code == 403


def test_no_verified_lines_fails_without_mutating_sync_map(monkeypatch):
    monkeypatch.setattr(subject, "_member_identity", lambda _request: "member-1")
    monkeypatch.setattr(
        subject,
        "_verified_lyric_markers",
        lambda _project: ([], {"song_dna_version": 1, "eligible_alignment_states": ["forced_aligned", "verified"], "skipped_unverified": 4, "skipped_untimed": 0}),
    )
    monkeypatch.setattr(subject, "read_sync", lambda _project: (_ for _ in ()).throw(AssertionError("sync read must not run")))

    with pytest.raises(HTTPException) as exc:
        subject.import_verified_lyrics("song-a", _request("pro"))

    assert exc.value.status_code == 409


def test_route_is_mounted_in_creative_aggregate_and_requires_authentication():
    from aura_music_studio.creative_version_autopromotion import router

    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).post(
        "/creative/projects/example/video-music-sync/import-verified-lyrics"
    )
    assert response.status_code == 401
