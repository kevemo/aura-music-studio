from __future__ import annotations

import json
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
    return SimpleNamespace(
        state=SimpleNamespace(
            member=SimpleNamespace(user_id="member-1", plan=_Plan(set(features)))
        )
    )


class _Queue:
    def __init__(self, job=None):
        self.job = job
        self.calls = []

    def get(self, job_id: str, *, user_id: str | None = None):
        self.calls.append((job_id, user_id))
        return self.job


def _completed_job(project_name: str = "project") -> dict:
    return {
        "id": "job-1",
        "user_id": "member-1",
        "project_name": project_name,
        "job_type": "engineering:split",
        "status": "completed",
        "result_json": json.dumps(
            {
                "operation": "split",
                "stem_assets": {
                    "vocals": {"asset_id": "asset-v", "asset_ref": "input/assets/vocals.wav"},
                    "drums": {"asset_id": "asset-d", "asset_ref": "input/assets/drums.wav"},
                },
                "stems": {
                    "vocals": "work/separation/private/vocals.wav",
                    "drums": "work/separation/private/drums.wav",
                },
            }
        ),
    }


def test_split_job_import_requires_multitrack_before_job_or_project_lookup(monkeypatch):
    queue = _Queue(_completed_job())
    monkeypatch.setattr(api, "queue", queue)
    project_touched = False

    def forbidden_project(_name):
        nonlocal project_touched
        project_touched = True
        raise AssertionError("project lookup must not happen")

    monkeypatch.setattr(api, "_project", forbidden_project)

    with pytest.raises(HTTPException) as exc:
        api.import_completed_split_job_to_daw("secret", "job-1", _request())

    assert exc.value.status_code == 403
    assert queue.calls == []
    assert project_touched is False


def test_split_job_import_scopes_job_lookup_to_authenticated_user(monkeypatch):
    queue = _Queue(None)
    monkeypatch.setattr(api, "queue", queue)
    monkeypatch.setattr(api, "_project", lambda _name: (_ for _ in ()).throw(AssertionError("no project lookup")))

    with pytest.raises(HTTPException) as exc:
        api.import_completed_split_job_to_daw(
            "project", "job-404", _request(api.MULTITRACK_DAW)
        )

    assert exc.value.status_code == 404
    assert queue.calls == [("job-404", "member-1")]


def test_split_job_import_rejects_cross_project_job_before_project_lookup(monkeypatch):
    queue = _Queue(_completed_job("other-project"))
    monkeypatch.setattr(api, "queue", queue)
    touched = False

    def forbidden_project(_name):
        nonlocal touched
        touched = True
        raise AssertionError("project lookup must not happen")

    monkeypatch.setattr(api, "_project", forbidden_project)

    with pytest.raises(HTTPException) as exc:
        api.import_completed_split_job_to_daw(
            "project", "job-1", _request(api.MULTITRACK_DAW)
        )

    assert exc.value.status_code == 404
    assert touched is False


def test_split_job_request_uses_registered_asset_ids_not_output_paths():
    body = api._split_job_daw_request(_completed_job())

    assert [(item.asset_id, item.role) for item in body.assets] == [
        ("asset-v", "vocals"),
        ("asset-d", "drums"),
    ]
    serialized = body.model_dump(mode="json")
    assert "asset_ref" not in str(serialized)
    assert "work/separation" not in str(serialized)


def test_split_job_request_rejects_incomplete_or_non_split_results():
    incomplete = _completed_job()
    incomplete["status"] = "running"
    with pytest.raises(HTTPException) as exc:
        api._split_job_daw_request(incomplete)
    assert exc.value.status_code == 409

    wrong_type = _completed_job()
    wrong_type["job_type"] = "engineering:master"
    with pytest.raises(HTTPException) as exc:
        api._split_job_daw_request(wrong_type)
    assert exc.value.status_code == 400

    missing_assets = _completed_job()
    missing_assets["result_json"] = json.dumps({"operation": "split", "stem_assets": {}})
    with pytest.raises(HTTPException) as exc:
        api._split_job_daw_request(missing_assets)
    assert exc.value.status_code == 409


def test_completed_split_job_import_validates_assets_then_imports_without_paths(monkeypatch, tmp_path: Path):
    queue = _Queue(_completed_job())
    monkeypatch.setattr(api, "queue", queue)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(api, "_project", lambda name: project if name == "project" else None)

    observed = {}

    def fake_validate(actual_project, body):
        assert actual_project == project
        observed["assets"] = [(item.asset_id, item.role) for item in body.assets]
        return [(SimpleNamespace(id="asset-v"), 1.0, "vocals")]

    def fake_import(actual_project, validated, member, project_name):
        assert actual_project == project
        assert project_name == "project"
        assert member.user_id == "member-1"
        assert validated[0][0].id == "asset-v"
        return {
            "imported": [{"asset_id": "asset-v"}],
            "imported_count": 1,
            "already_present_asset_ids": [],
            "session": {"tracks": []},
            "atomic_validation": True,
            "source_paths_exposed": False,
        }

    monkeypatch.setattr(api, "_validated_daw_assets", fake_validate)
    monkeypatch.setattr(api, "_import_validated_daw_assets", fake_import)

    result = api.import_completed_split_job_to_daw(
        "project", "job-1", _request(api.MULTITRACK_DAW)
    )

    assert observed["assets"] == [("asset-v", "vocals"), ("asset-d", "drums")]
    assert result["source_job_id"] == "job-1"
    assert result["source_job_type"] == "engineering:split"
    assert result["job_result_paths_exposed"] is False
    assert "work/separation" not in str(result)
