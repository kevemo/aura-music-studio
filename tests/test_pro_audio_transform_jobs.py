from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from aura_music_studio import engineering_job_api
from aura_music_studio.engineering_jobs import EngineeringJobRequest, run_engineering_job
from aura_music_studio.plans import get_plan


def _request(plan_id: str):
    member = SimpleNamespace(user_id=f"user-{plan_id}", plan=get_plan(plan_id))
    return SimpleNamespace(state=SimpleNamespace(member=member))


def test_basic_cover_is_denied_before_project_lookup(monkeypatch):
    touched = False

    def forbidden_lookup(name):
        nonlocal touched
        touched = True
        raise AssertionError("project lookup must not happen for denied tier")

    monkeypatch.setattr(engineering_job_api, "_project", forbidden_lookup)
    body = EngineeringJobRequest(operation="cover", asset_id="asset-1", transform_prompt="Turn this into a cinematic rock arrangement")
    with pytest.raises(HTTPException) as exc:
        engineering_job_api.submit_engineering_job("secret-project", body, _request("base"))
    assert exc.value.status_code == 403
    assert touched is False


def test_basic_repaint_is_denied_before_project_lookup(monkeypatch):
    touched = False

    def forbidden_lookup(name):
        nonlocal touched
        touched = True
        raise AssertionError("project lookup must not happen for denied tier")

    monkeypatch.setattr(engineering_job_api, "_project", forbidden_lookup)
    body = EngineeringJobRequest(operation="repaint", asset_id="asset-1", transform_prompt="Replace this section with a stronger chorus", repaint_start=20, repaint_end=40)
    with pytest.raises(HTTPException) as exc:
        engineering_job_api.submit_engineering_job("secret-project", body, _request("base"))
    assert exc.value.status_code == 403
    assert touched is False


def test_repaint_requires_bounded_forward_region():
    with pytest.raises(ValidationError):
        EngineeringJobRequest(operation="repaint", asset_id="asset-1", transform_prompt="Regenerate this phrase", repaint_start=30, repaint_end=20)
    with pytest.raises(ValidationError):
        EngineeringJobRequest(operation="repaint", asset_id="asset-1", transform_prompt="Regenerate this long section", repaint_start=0, repaint_end=121)


def test_transform_request_exposes_no_raw_source_path_fields():
    body = EngineeringJobRequest(operation="cover", asset_id="project-owned-asset", transform_prompt="Create a soulful acoustic reinterpretation")
    payload = body.model_dump(mode="json")
    assert payload["asset_id"] == "project-owned-asset"
    assert "src_audio" not in payload
    assert "reference_audio" not in payload
    assert "source_path" not in payload


def test_pro_cover_queues_project_asset_job(monkeypatch, tmp_path):
    asset = SimpleNamespace(id="asset-1", kind="audio", path="input/assets/song.wav")

    class FakeLibrary:
        def __init__(self, project): self.project = project
        def get(self, asset_id):
            assert asset_id == "asset-1"
            return asset

    captured = {}

    class FakeQueue:
        def submit(self, user_id, project_name, *, job_type, priority, payload):
            captured.update(user_id=user_id, project_name=project_name, job_type=job_type, priority=priority, payload=payload)
            return {"id": "job-1", "user_id": user_id, "project_name": project_name, "job_type": job_type, "status": "queued", "payload_json": "private", "result_json": None}

    monkeypatch.setattr(engineering_job_api, "_project", lambda name: tmp_path)
    monkeypatch.setattr(engineering_job_api, "AssetLibrary", FakeLibrary)
    monkeypatch.setattr(engineering_job_api, "queue", FakeQueue())
    body = EngineeringJobRequest(operation="cover", asset_id="asset-1", transform_prompt="Create a cinematic orchestral cover")
    result = engineering_job_api.submit_engineering_job("project-a", body, _request("pro"))
    assert result["id"] == "job-1"
    assert "payload_json" not in result
    assert captured["job_type"] == "engineering:cover"
    assert captured["payload"]["asset_id"] == "asset-1"
    assert "src_audio" not in captured["payload"]


def test_cover_worker_returns_only_project_relative_output(monkeypatch, tmp_path):
    project = tmp_path / "project"
    source = project / "input" / "assets" / "song.wav"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"audio")
    record = SimpleNamespace(id="asset-1", name="song.wav", kind="audio", path="input/assets/song.wav")

    class FakeLibrary:
        def __init__(self, root): self.root = root
        def get(self, asset_id): return record
        def ingest(self, output, **kwargs):
            assert Path(output).is_file()
            assert kwargs["kind"] == "audio"
            return SimpleNamespace(
                id="generated-cover-1",
                name=Path(output).name,
                kind="audio",
                path="input/assets/generated-cover.wav",
            )

    class FakeAceStepClient:
        def cover(self, source_path, output_dir, *, prompt, strength):
            assert source_path == source.resolve()
            output_dir.mkdir(parents=True, exist_ok=True)
            rendered = output_dir / "ace_step_cover_01.wav"
            rendered.write_bytes(b"rendered")
            return rendered

    import aura_music_studio.engineering_jobs as jobs
    monkeypatch.setattr(jobs, "AssetLibrary", FakeLibrary)
    monkeypatch.setattr(jobs, "AceStepClient", FakeAceStepClient)
    result = run_engineering_job(project, EngineeringJobRequest(operation="cover", asset_id="asset-1", transform_prompt="Create a cinematic cover").model_dump(mode="json"))
    assert result["operation"] == "cover"
    assert result["source_asset_id"] == "asset-1"
    assert result["output_ref"].startswith("output/transformations/asset-1/cover_")
    assert result["asset"]["asset_id"] == "generated-cover-1"
    assert result["asset"]["asset_ref"] == "input/assets/generated-cover.wav"
    assert not Path(result["output_ref"]).is_absolute()
    assert "output" not in result
    assert "path" not in result
    assert "source_path" not in result


def test_worker_rejects_asset_path_outside_project(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"audio")
    record = SimpleNamespace(id="asset-1", name="outside.wav", kind="audio", path="../outside.wav")

    class FakeLibrary:
        def __init__(self, root): self.root = root
        def get(self, asset_id): return record

    import aura_music_studio.engineering_jobs as jobs
    monkeypatch.setattr(jobs, "AssetLibrary", FakeLibrary)
    with pytest.raises(ValueError, match="outside the member project"):
        run_engineering_job(project, EngineeringJobRequest(operation="cover", asset_id="asset-1", transform_prompt="Create a safe cover").model_dump(mode="json"))
