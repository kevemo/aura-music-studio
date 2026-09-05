from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from aura_music_studio import jobs
from aura_music_studio.professional_editor_render_api import EditorRenderRequest
from aura_music_studio import professional_editor_render_jobs as render_jobs
from aura_music_studio.professional_editor_api import router as professional_editor_router
from aura_music_studio.professional_editor_security_overlay import install_professional_editor_patch_guard


class Plan:
    def __init__(self, allowed=()):
        self.allowed = set(allowed)

    def has(self, feature):
        return feature in self.allowed


def _request(plan, user_id="member-1"):
    return SimpleNamespace(state=SimpleNamespace(member=SimpleNamespace(user_id=user_id, plan=plan)))


def test_public_job_never_exposes_private_payload():
    job = {
        "id": "job-1",
        "payload_json": json.dumps({"sequence_id": "private-sequence", "prompt": "private"}),
        "result_json": json.dumps({"ok": True}),
    }
    public = render_jobs._public_job(job)
    assert "payload_json" not in public
    assert "result_json" not in public
    assert public["result"] == {"ok": True}


def test_basic_gate_runs_before_project_lookup(monkeypatch):
    called = False

    def project_lookup(_name):
        nonlocal called
        called = True
        raise AssertionError("project lookup must not run for denied membership")

    monkeypatch.setattr(render_jobs, "_project", project_lookup)
    body = EditorRenderRequest(format="mp4")
    with pytest.raises(HTTPException) as exc:
        render_jobs.submit_editor_render_job("other-project", "seq-1", body, _request(Plan()))
    assert exc.value.status_code == 403
    assert called is False


def test_worker_rechecks_current_plan_before_renderer(monkeypatch, tmp_path):
    renderer_called = False

    def renderer(_project):
        nonlocal renderer_called
        renderer_called = True
        raise AssertionError("renderer must not open after downgrade")

    monkeypatch.setattr(render_jobs, "_active_plan", lambda _store, _user_id: Plan())
    monkeypatch.setattr(render_jobs, "_renderer", renderer)
    with pytest.raises(PermissionError, match="Basic membership"):
        render_jobs.run_editor_render_job(
            tmp_path,
            {"sequence_id": "seq-1", "render": {"format": "mp4"}},
            user_id="member-1",
            account_store=SimpleNamespace(),
        )
    assert renderer_called is False


def test_worker_dispatches_editor_render_inside_job_user_context(monkeypatch, tmp_path):
    captured = {}
    fake_store = SimpleNamespace()
    fake_queue = SimpleNamespace(store=fake_store)
    worker = jobs.AuraJobWorker(fake_queue)
    monkeypatch.setattr(jobs, "project_path", lambda name, must_exist=True: tmp_path)

    def execute(project, payload, *, user_id, account_store):
        captured.update(project=project, payload=payload, user_id=user_id, account_store=account_store)
        return {"ok": True}

    monkeypatch.setattr(render_jobs, "run_editor_render_job", execute)
    result = worker.run_job({
        "user_id": "member-1",
        "project_name": "project-1",
        "job_type": "editor_render",
        "payload_json": json.dumps({"sequence_id": "seq-1", "render": {"format": "mp4"}}),
    })
    assert result == {"ok": True}
    assert captured["project"] == tmp_path
    assert captured["user_id"] == "member-1"
    assert captured["account_store"] is fake_store


def test_render_job_route_installation_is_idempotent():
    install_professional_editor_patch_guard()
    install_professional_editor_patch_guard()
    signature = ("/creative/projects/{project_name}/editor/sequences/{sequence_id}/render-jobs", frozenset({"POST"}))
    matches = [route for route in professional_editor_router.routes if (getattr(route, "path", None), frozenset(getattr(route, "methods", set()))) == signature]
    assert len(matches) == 1


def test_render_job_request_has_no_raw_path_fields():
    fields = set(EditorRenderRequest.model_fields)
    assert not fields.intersection({"path", "source_path", "output_path", "filesystem_path", "url"})
