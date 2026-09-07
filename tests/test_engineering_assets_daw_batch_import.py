from __future__ import annotations

import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import aura_music_studio.engineering_job_api as api


class _Plan:
    def __init__(self, allowed: set[str]):
        self.allowed = set(allowed)

    def has(self, feature: str) -> bool:
        return feature in self.allowed


def _request(*features: str):
    member = SimpleNamespace(user_id="member-1", plan=_Plan(set(features)))
    return SimpleNamespace(state=SimpleNamespace(member=member))


def _wav(path: Path, *, frames: int = 8000, rate: int = 8000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * frames)


def test_batch_import_requires_multitrack_before_project_lookup(monkeypatch):
    touched = False

    def forbidden_project(_name):
        nonlocal touched
        touched = True
        raise AssertionError("project lookup must not run for a denied tier")

    monkeypatch.setattr(api, "_project", forbidden_project)
    body = api.EngineeringAssetsDAWImportRequest(
        assets=[api.EngineeringAssetDAWImport(asset_id="stem-1", role="vocals")]
    )

    with pytest.raises(HTTPException) as exc:
        api.import_engineering_assets_to_daw("secret-project", body, _request())

    assert exc.value.status_code == 403
    assert touched is False


def test_validated_batch_rejects_duplicate_asset_ids_before_mutation(monkeypatch, tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    audio = project / "input" / "assets" / "vocals.wav"
    _wav(audio)
    record = SimpleNamespace(id="stem-1", name="vocals.wav", kind="audio", path="input/assets/vocals.wav")

    class Library:
        def get(self, asset_id):
            assert asset_id == "stem-1"
            return record

    monkeypatch.setattr(api, "AssetLibrary", lambda _project: Library())
    body = api.EngineeringAssetsDAWImportRequest(
        assets=[
            api.EngineeringAssetDAWImport(asset_id="stem-1", role="vocals"),
            api.EngineeringAssetDAWImport(asset_id="stem-1", role="vocals"),
        ]
    )

    with pytest.raises(HTTPException) as exc:
        api._validated_daw_assets(project, body)

    assert exc.value.status_code == 400
    assert "Duplicate asset_id" in exc.value.detail


def test_validated_batch_rejects_project_escape(monkeypatch, tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.wav"
    _wav(outside)
    record = SimpleNamespace(id="stem-1", name="outside.wav", kind="audio", path="../outside.wav")

    class Library:
        def get(self, _asset_id):
            return record

    monkeypatch.setattr(api, "AssetLibrary", lambda _project: Library())
    body = api.EngineeringAssetsDAWImportRequest(
        assets=[api.EngineeringAssetDAWImport(asset_id="stem-1", role="vocals")]
    )

    with pytest.raises(HTTPException) as exc:
        api._validated_daw_assets(project, body)

    assert exc.value.status_code == 400
    assert "outside the member project" in exc.value.detail


def test_validation_failure_occurs_before_session_load_or_mutation(monkeypatch, tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(api, "_project", lambda _name: project)
    monkeypatch.setattr(
        api,
        "_validated_daw_assets",
        lambda _project, _body: (_ for _ in ()).throw(HTTPException(400, "bad final stem")),
    )
    session_loaded = False

    def forbidden_load(*_args, **_kwargs):
        nonlocal session_loaded
        session_loaded = True
        raise AssertionError("session must not load before complete batch validation")

    monkeypatch.setattr(api, "load_session", forbidden_load)
    body = api.EngineeringAssetsDAWImportRequest(
        assets=[api.EngineeringAssetDAWImport(asset_id="stem-1", role="vocals")]
    )

    with pytest.raises(HTTPException) as exc:
        api.import_engineering_assets_to_daw(
            "project", body, _request(api.MULTITRACK_DAW)
        )

    assert exc.value.detail == "bad final stem"
    assert session_loaded is False


def test_batch_import_adds_aligned_tracks_and_is_idempotent(monkeypatch, tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    vocals = project / "input" / "assets" / "vocals.wav"
    drums = project / "input" / "assets" / "drums.wav"
    _wav(vocals, frames=8000)
    _wav(drums, frames=16000)
    records = {
        "stem-v": SimpleNamespace(id="stem-v", name="vocals.wav", kind="audio", path="input/assets/vocals.wav"),
        "stem-d": SimpleNamespace(id="stem-d", name="drums.wav", kind="audio", path="input/assets/drums.wav"),
    }

    class Library:
        def get(self, asset_id):
            return records[asset_id]

    monkeypatch.setattr(api, "_project", lambda _name: project)
    monkeypatch.setattr(api, "AssetLibrary", lambda _project: Library())
    body = api.EngineeringAssetsDAWImportRequest(
        assets=[
            api.EngineeringAssetDAWImport(asset_id="stem-v", role="vocals"),
            api.EngineeringAssetDAWImport(asset_id="stem-d", role="drums"),
        ]
    )
    request = _request(api.MULTITRACK_DAW)

    first = api.import_engineering_assets_to_daw("project", body, request)
    assert first["imported_count"] == 2
    assert {item["asset_id"] for item in first["imported"]} == {"stem-v", "stem-d"}
    assert {item["role"] for item in first["imported"]} == {"vocals", "drums"}
    assert first["atomic_validation"] is True
    assert first["source_paths_exposed"] is False
    assert str(tmp_path) not in str(first)

    session = api.load_session(project)
    clips = [clip for track in session.tracks for clip in track.clips]
    assert {clip.metadata.get("asset_id") for clip in clips} == {"stem-v", "stem-d"}
    assert all(clip.start == 0.0 for clip in clips)
    assert all(clip.metadata.get("engineering_asset") is True for clip in clips)

    second = api.import_engineering_assets_to_daw("project", body, request)
    assert second["imported_count"] == 0
    assert second["already_present_asset_ids"] == ["stem-d", "stem-v"]
    session_again = api.load_session(project)
    clips_again = [clip for track in session_again.tracks for clip in track.clips]
    assert len(clips_again) == 2


def test_batch_import_normalizes_unknown_roles_to_other(monkeypatch, tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    audio = project / "input" / "assets" / "stem.wav"
    _wav(audio)
    record = SimpleNamespace(id="stem-1", name="stem.wav", kind="audio", path="input/assets/stem.wav")

    class Library:
        def get(self, _asset_id):
            return record

    monkeypatch.setattr(api, "AssetLibrary", lambda _project: Library())
    body = api.EngineeringAssetsDAWImportRequest(
        assets=[api.EngineeringAssetDAWImport(asset_id="stem-1", role="../../owner")]
    )
    validated = api._validated_daw_assets(project, body)

    assert validated[0][2] == "other"
