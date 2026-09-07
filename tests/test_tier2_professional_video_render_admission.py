from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import aura_music_studio.professional_editor_render_api as render_api


def _request(headers: dict[str, str] | None = None) -> Request:
    raw = []
    for key, value in (headers or {}).items():
        raw.append((key.lower().encode("latin-1"), value.encode("latin-1")))
    return Request({"type": "http", "method": "POST", "path": "/", "headers": raw})


def _member(plan_id: str, user_id: str = "member-123"):
    return SimpleNamespace(user_id=user_id, plan=SimpleNamespace(id=plan_id))


class RecordingGuard:
    def __init__(self, *, error: Exception | None = None):
        self.calls: list[dict] = []
        self.error = error

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return kwargs["provider_call"](), SimpleNamespace(state="completed")


def test_tier2_video_edit_uses_shared_paid_admission(monkeypatch):
    guard = RecordingGuard()
    monkeypatch.setattr(render_api, "tier2_guard", guard)

    result = render_api._execute_video_render(
        _member("base"),
        _request({"Idempotency-Key": "video-edit-request-1"}),
        lambda: "rendered.mp4",
    )

    assert result == "rendered.mp4"
    assert len(guard.calls) == 1
    call = guard.calls[0]
    assert call["user_id"] == "member-123"
    assert call["plan_id"] == "base"
    assert call["operation"] == "video_edit"
    assert call["request_key"] == "video-edit-request-1"


def test_unlimited_pro_uses_same_execution_boundary(monkeypatch):
    guard = RecordingGuard()
    monkeypatch.setattr(render_api, "tier2_guard", guard)

    assert render_api._execute_video_render(_member("pro"), _request(), lambda: "ok") == "ok"
    assert guard.calls[0]["plan_id"] == "pro"
    assert guard.calls[0]["operation"] == "video_edit"
    assert guard.calls[0]["request_key"].startswith("video-edit-render-")


def test_free_and_legacy_plans_do_not_receive_tier2_capacity(monkeypatch):
    class ExplodingGuard:
        def execute(self, **kwargs):  # pragma: no cover - the assertion is that this is unreachable
            raise AssertionError("Free/legacy rendering must not enter the paid Tier 2 guard")

    monkeypatch.setattr(render_api, "tier2_guard", ExplodingGuard())
    calls = []

    result = render_api._execute_video_render(
        _member("free"),
        _request({"Idempotency-Key": "ignored-for-free"}),
        lambda: calls.append("rendered") or "free-result",
    )

    assert result == "free-result"
    assert calls == ["rendered"]


def test_admission_failure_happens_before_self_hosted_compositor(monkeypatch):
    guard = RecordingGuard(error=PermissionError("Tier 2 daily eligible-operation allowance has been reached"))
    monkeypatch.setattr(render_api, "tier2_guard", guard)
    provider_calls = []

    with pytest.raises(PermissionError, match="allowance has been reached"):
        render_api._execute_video_render(
            _member("base"),
            _request({"X-Request-ID": "capacity-check"}),
            lambda: provider_calls.append("should-not-run"),
        )

    assert provider_calls == []


def test_video_render_idempotency_key_is_bounded():
    with pytest.raises(HTTPException) as exc:
        render_api._video_render_request_key(_request({"Idempotency-Key": "x" * 181}))
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        render_api._video_render_request_key(_request({"Idempotency-Key": "   "}))
    assert exc.value.status_code == 400


def test_video_render_prefers_explicit_idempotency_key():
    request = _request(
        {
            "Idempotency-Key": "canonical-key",
            "X-Request-ID": "fallback-key",
        }
    )
    assert render_api._video_render_request_key(request) == "canonical-key"
