from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request

import aura_music_studio.shared_sky_worker_control as control


def _request(method: str, path: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 10000),
            "server": ("testserver", 80),
        }
    )


def _settings(*, enabled: bool):
    return SimpleNamespace(
        enabled=enabled,
        poll_seconds=5.0,
        lease_seconds=90,
        max_attempts=3,
        retry_seconds=30,
    )


def test_scheduler_controls_require_owner_authentication(monkeypatch):
    monkeypatch.setattr(control, "owner_session_authorized", lambda request: False)
    with pytest.raises(HTTPException) as status_exc:
        control.scheduler_status(_request("GET", "/owner/shared-sky/api/scheduler/status"))
    assert status_exc.value.status_code == 401
    with pytest.raises(HTTPException) as run_exc:
        control.scheduler_run_due(_request("POST", "/owner/shared-sky/api/scheduler/run-due"))
    assert run_exc.value.status_code == 401


def test_scheduler_status_redacts_worker_error_text(monkeypatch):
    class FakeWorker:
        settings = _settings(enabled=True)

        def worker_health(self):
            return [
                {
                    "worker_id": "worker-1",
                    "status": "retry",
                    "last_seen_at": "2026-09-04T15:00:00+00:00",
                    "last_claimed_schedule_id": "schedule-1",
                    "last_error": "rtmp://super-secret.example/live/key",
                    "healthy": True,
                }
            ]

    monkeypatch.setattr(control, "owner_session_authorized", lambda request: True)
    monkeypatch.setattr(control, "worker", FakeWorker())
    payload = control.scheduler_status(_request("GET", "/owner/shared-sky/api/scheduler/status"))
    serialized = json.dumps(payload)
    assert payload["scheduler"]["enabled"] is True
    assert payload["scheduler"]["healthy_workers"] == 1
    assert payload["scheduler"]["workers"][0]["error_present"] is True
    assert payload["scheduler"]["raw_worker_errors_exposed"] is False
    assert "super-secret" not in serialized
    assert "last_error" not in serialized


def test_manual_run_fails_closed_when_scheduler_is_disabled(monkeypatch):
    fake = SimpleNamespace(settings=_settings(enabled=False))
    monkeypatch.setattr(control, "owner_session_authorized", lambda request: True)
    monkeypatch.setattr(control, "worker", fake)
    with pytest.raises(HTTPException) as exc_info:
        control.scheduler_run_due(_request("POST", "/owner/shared-sky/api/scheduler/run-due"))
    assert exc_info.value.status_code == 503
    assert "disabled" in str(exc_info.value.detail).lower()


def test_manual_run_uses_canonical_worker_and_redacts_execution_error(monkeypatch):
    class FakeWorker:
        settings = _settings(enabled=True)

        def run_once(self):
            return {
                "claimed": True,
                "ok": False,
                "schedule_id": "schedule-2",
                "broadcast_id": "broadcast-2",
                "error": "provider rejected rtmp://secret.example/key",
            }

        def heartbeat(self, **kwargs):
            raise AssertionError("heartbeat should not be called for a normal fail-closed result")

    monkeypatch.setattr(control, "owner_session_authorized", lambda request: True)
    monkeypatch.setattr(control, "worker", FakeWorker())
    payload = control.scheduler_run_due(_request("POST", "/owner/shared-sky/api/scheduler/run-due"))
    assert payload == {
        "claimed": True,
        "ok": False,
        "schedule_id": "schedule-2",
        "broadcast_id": "broadcast-2",
        "error_present": True,
    }
    assert "secret.example" not in json.dumps(payload)


def test_owner_scheduler_routes_are_directly_mounted_once_on_esp_overlay():
    from aura_music_studio.esp_creator_plan_overlay import router

    status_routes = [
        route for route in router.routes
        if getattr(route, "path", None) == "/owner/shared-sky/api/scheduler/status"
        and "GET" in (getattr(route, "methods", None) or set())
    ]
    run_routes = [
        route for route in router.routes
        if getattr(route, "path", None) == "/owner/shared-sky/api/scheduler/run-due"
        and "POST" in (getattr(route, "methods", None) or set())
    ]
    assert len(status_routes) == 1
    assert len(run_routes) == 1
