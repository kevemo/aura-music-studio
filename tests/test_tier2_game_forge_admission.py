from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import aura_music_studio.game_forge_world_api as game_api


def _request(headers: dict[str, str] | None = None, *, method: str = "POST") -> Request:
    raw = []
    for key, value in (headers or {}).items():
        raw.append((key.lower().encode("latin-1"), value.encode("latin-1")))
    return Request({"type": "http", "method": method, "path": "/", "headers": raw})


def _member(plan_id: str, user_id: str = "member-game-123"):
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


def test_game_create_and_edit_routes_are_authoritative_on_world_router():
    routes = [
        (route.path, set(route.methods or set()))
        for route in game_api.router.routes
        if getattr(route, "path", None) in {"/api/game-forge/games", "/api/game-forge/games/{game_id}"}
    ]
    assert ("/api/game-forge/games", {"POST"}) in routes
    assert ("/api/game-forge/games/{game_id}", {"PATCH"}) in routes


def test_tier2_game_create_uses_shared_paid_admission(monkeypatch):
    guard = RecordingGuard()
    monkeypatch.setattr(game_api, "tier2_guard", guard)

    result = game_api._execute_game_operation(
        _member("base"),
        _request({"Idempotency-Key": "game-create-request-1"}),
        operation="game_create",
        provider_call=lambda: "created-game",
    )

    assert result == "created-game"
    assert len(guard.calls) == 1
    call = guard.calls[0]
    assert call["user_id"] == "member-game-123"
    assert call["plan_id"] == "base"
    assert call["operation"] == "game_create"
    assert call["request_key"] == "game-create-request-1"


def test_tier2_game_edit_uses_shared_paid_admission(monkeypatch):
    guard = RecordingGuard()
    monkeypatch.setattr(game_api, "tier2_guard", guard)

    result = game_api._execute_game_operation(
        _member("base"),
        _request({"X-Request-ID": "game-edit-request-1"}, method="PATCH"),
        operation="game_edit",
        provider_call=lambda: "edited-game",
    )

    assert result == "edited-game"
    call = guard.calls[0]
    assert call["operation"] == "game_edit"
    assert call["request_key"] == "game-edit-request-1"


def test_unlimited_pro_uses_same_game_execution_boundary(monkeypatch):
    guard = RecordingGuard()
    monkeypatch.setattr(game_api, "tier2_guard", guard)

    assert game_api._execute_game_operation(
        _member("pro"),
        _request(),
        operation="game_create",
        provider_call=lambda: "ok",
    ) == "ok"
    assert guard.calls[0]["plan_id"] == "pro"
    assert guard.calls[0]["operation"] == "game_create"
    assert guard.calls[0]["request_key"].startswith("game_create-")


def test_non_tier2_membership_does_not_receive_paid_capacity(monkeypatch):
    class ExplodingGuard:
        def execute(self, **kwargs):  # pragma: no cover - must remain unreachable
            raise AssertionError("Non-Tier-2 membership must not enter paid Game Forge admission")

    monkeypatch.setattr(game_api, "tier2_guard", ExplodingGuard())
    calls = []

    result = game_api._execute_game_operation(
        _member("legacy"),
        _request({"Idempotency-Key": "ignored-for-legacy"}),
        operation="game_edit",
        provider_call=lambda: calls.append("persisted") or "legacy-result",
    )

    assert result == "legacy-result"
    assert calls == ["persisted"]


def test_admission_failure_prevents_game_mutation(monkeypatch):
    guard = RecordingGuard(error=PermissionError("Tier 2 daily eligible-operation allowance has been reached"))
    monkeypatch.setattr(game_api, "tier2_guard", guard)
    persisted = []

    with pytest.raises(PermissionError, match="allowance has been reached"):
        game_api._execute_game_operation(
            _member("base"),
            _request({"Idempotency-Key": "capacity-check"}),
            operation="game_edit",
            provider_call=lambda: persisted.append("should-not-persist"),
        )

    assert persisted == []


def test_game_operation_request_key_is_bounded_and_prefers_idempotency_key():
    with pytest.raises(HTTPException) as exc:
        game_api._game_operation_request_key(_request({"Idempotency-Key": "x" * 181}), "game_create")
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        game_api._game_operation_request_key(_request({"Idempotency-Key": "   "}), "game_create")
    assert exc.value.status_code == 400

    request = _request({"Idempotency-Key": "canonical-key", "X-Request-ID": "fallback-key"})
    assert game_api._game_operation_request_key(request, "game_create") == "canonical-key"
