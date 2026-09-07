from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from aura_music_studio import job_api
from aura_music_studio.tier2_daily_meter import Tier2Admission


class _Plan:
    def __init__(self, plan_id: str, *, priority: bool = False):
        self.id = plan_id
        self._priority = priority

    def has(self, feature: str) -> bool:
        return self._priority and feature == job_api.PRIORITY_QUEUE


class _Queue:
    def __init__(self):
        self.calls: list[tuple[str, str, str, int]] = []

    def submit(self, user_id: str, project_name: str, *, job_type: str, priority: int):
        self.calls.append((user_id, project_name, job_type, priority))
        return {
            "id": "job-1",
            "user_id": user_id,
            "project_name": project_name,
            "job_type": job_type,
            "priority": priority,
            "payload_json": '{"private":"prompt"}',
        }


class _Guard:
    def __init__(self, *, error: Exception | None = None):
        self.calls: list[dict] = []
        self.error = error

    def execute(self, **kwargs):
        self.calls.append({key: value for key, value in kwargs.items() if key != "provider_call"})
        if self.error is not None:
            raise self.error
        result = kwargs["provider_call"]()
        admission = Tier2Admission(
            reservation_id="reservation-1" if kwargs["plan_id"] == "base" else None,
            user_id=kwargs["user_id"],
            plan_id=kwargs["plan_id"],
            operation=kwargs["operation"],
            request_key=kwargs["request_key"],
            utc_day="2026-08-30",
            state="completed" if kwargs["plan_id"] == "base" else "unlimited",
            limit=5 if kwargs["plan_id"] == "base" else None,
            used=1 if kwargs["plan_id"] == "base" else None,
            remaining=4 if kwargs["plan_id"] == "base" else None,
            unlimited=kwargs["plan_id"] == "pro",
        )
        return result, admission


def _request(member, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": headers or []})
    request.state.member = member
    return request


def _member(plan_id: str, *, priority: bool = False):
    return SimpleNamespace(user_id="user-1", plan=_Plan(plan_id, priority=priority))


def _prepare(monkeypatch, guard: _Guard | None = None):
    queue = _Queue()
    monkeypatch.setattr(job_api, "queue", queue)
    monkeypatch.setattr(job_api, "project_path", lambda name, must_exist: f"/projects/{name}")
    if guard is not None:
        monkeypatch.setattr(job_api, "tier2_guard", guard)
    return queue


def test_tier2_music_render_uses_shared_guard_and_skips_legacy_song_slot(monkeypatch):
    guard = _Guard()
    queue = _prepare(monkeypatch, guard)
    legacy_calls: list[str] = []
    monkeypatch.setattr(job_api, "start_full_song_slot", lambda member, project: legacy_calls.append(project))

    request = _request(_member("base"), [(b"idempotency-key", b"music-request-123")])
    result = job_api.submit_render("track-one", request)

    assert result["id"] == "job-1"
    assert "payload_json" not in result
    assert legacy_calls == []
    assert queue.calls == [("user-1", "track-one", "produce", 20)]
    assert guard.calls == [
        {
            "user_id": "user-1",
            "plan_id": "base",
            "operation": "music_create",
            "request_key": "music-request-123",
        }
    ]


def test_unlimited_pro_uses_guard_without_legacy_slot_and_keeps_priority(monkeypatch):
    guard = _Guard()
    queue = _prepare(monkeypatch, guard)
    monkeypatch.setattr(
        job_api,
        "start_full_song_slot",
        lambda *_: pytest.fail("Unlimited Pro must not enter the legacy song/day counter"),
    )

    result = job_api.submit_render("track-pro", _request(_member("pro", priority=True)))

    assert result["id"] == "job-1"
    assert queue.calls == [("user-1", "track-pro", "produce", 100)]
    assert guard.calls[0]["plan_id"] == "pro"
    assert guard.calls[0]["operation"] == "music_create"
    assert guard.calls[0]["request_key"].startswith("music-render-")


def test_free_members_remain_on_separate_legacy_entitlement_path(monkeypatch):
    guard = _Guard()
    queue = _prepare(monkeypatch, guard)
    legacy_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        job_api,
        "start_full_song_slot",
        lambda member, project: legacy_calls.append((member.user_id, project)),
    )

    result = job_api.submit_render("free-track", _request(_member("free")))

    assert result["id"] == "job-1"
    assert guard.calls == []
    assert legacy_calls == [("user-1", "free-track")]
    assert queue.calls == [("user-1", "free-track", "produce", 20)]


def test_tier2_allowance_rejection_never_submits_job(monkeypatch):
    guard = _Guard(error=PermissionError("Tier 2 daily eligible-operation allowance has been reached"))
    queue = _prepare(monkeypatch, guard)
    monkeypatch.setattr(
        job_api,
        "start_full_song_slot",
        lambda *_: pytest.fail("Tier 2 must not enter the legacy song/day counter"),
    )

    with pytest.raises(HTTPException) as caught:
        job_api.submit_render("track-six", _request(_member("base")))

    assert caught.value.status_code == 403
    assert "allowance" in str(caught.value.detail)
    assert queue.calls == []


def test_invalid_client_idempotency_key_is_rejected_before_guard_or_job(monkeypatch):
    guard = _Guard()
    queue = _prepare(monkeypatch, guard)

    with pytest.raises(HTTPException) as caught:
        job_api.submit_render(
            "track-key",
            _request(_member("base"), [(b"idempotency-key", b" " * 4)]),
        )

    assert caught.value.status_code == 400
    assert guard.calls == []
    assert queue.calls == []
