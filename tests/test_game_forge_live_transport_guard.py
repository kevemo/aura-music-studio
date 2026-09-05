from __future__ import annotations

from aura_music_studio import game_forge_live_transport_guard as guard
from aura_music_studio.game_forge_live_integration import EmergencyHideRequest, PromoteLiveVersionRequest, TransitionLiveSourceRequest
from aura_music_studio.game_forge_project_binding import router as project_router


def _fake_auth(monkeypatch):
    member = object()
    monkeypatch.setattr(guard, "_creator", lambda request: member)
    monkeypatch.setattr(guard, "_member_identity", lambda value: "creator_1")


def test_guard_routes_precede_legacy_live_routes():
    expectations = [
        ("PATCH", "/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/presentation", "guarded_transition_game_live_source"),
        ("POST", "/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/promote-version", "guarded_promote_game_live_version"),
        ("POST", "/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}/emergency-hide", "guarded_emergency_hide_game_live_source"),
        ("DELETE", "/api/game-forge/games/{game_id}/live/sources/{source_adapter_id}", "guarded_detach_game_live_source"),
    ]
    for method, path, endpoint_name in expectations:
        matches = [
            route
            for route in project_router.routes
            if getattr(route, "path", None) == path and method in (getattr(route, "methods", set()) or set())
        ]
        assert len(matches) >= 2
        assert matches[0].endpoint.__module__.endswith("game_forge_live_transport_guard")
        assert matches[0].endpoint.__name__ == endpoint_name


def test_emergency_hide_forces_bound_transport_not_ready(monkeypatch):
    _fake_auth(monkeypatch)
    monkeypatch.setattr(
        guard,
        "emergency_hide_game_live_source",
        lambda game_id, source_adapter_id, body, request: {
            "source": {"source_adapter_id": source_adapter_id, "status": "hidden"},
            "brb_requested": True,
        },
    )
    observed = {}

    def sync(**kwargs):
        observed.update(kwargs)
        return {"id": "programme_1", "state": "failed"}

    monkeypatch.setattr(guard, "_sync_bound_programme_source", sync)
    result = guard.guarded_emergency_hide_game_live_source(
        "game_1",
        "source_1",
        EmergencyHideRequest(revoke=False),
        object(),
    )

    assert observed == {
        "user_id": "creator_1",
        "game_id": "game_1",
        "source_adapter_id": "source_1",
        "reason_code": "game_forge_emergency_hide",
        "force_not_ready": True,
    }
    assert result["transport_source_state"] == "failed"
    assert result["transport_state_synchronised"] is True


def test_emergency_revoke_uses_revocation_reason(monkeypatch):
    _fake_auth(monkeypatch)
    monkeypatch.setattr(guard, "emergency_hide_game_live_source", lambda *args: {"brb_requested": True})
    observed = {}
    monkeypatch.setattr(
        guard,
        "_sync_bound_programme_source",
        lambda **kwargs: observed.update(kwargs) or {"id": "programme_1", "state": "failed"},
    )

    guard.guarded_emergency_hide_game_live_source(
        "game_1",
        "source_1",
        EmergencyHideRequest(revoke=True),
        object(),
    )
    assert observed["reason_code"] == "game_forge_source_revoked"
    assert observed["force_not_ready"] is True


def test_detach_forces_bound_transport_not_ready(monkeypatch):
    _fake_auth(monkeypatch)
    monkeypatch.setattr(guard, "detach_game_live_source", lambda *args: {"detached": True})
    observed = {}
    monkeypatch.setattr(
        guard,
        "_sync_bound_programme_source",
        lambda **kwargs: observed.update(kwargs) or {"id": "programme_1", "state": "failed"},
    )

    result = guard.guarded_detach_game_live_source("game_1", "source_1", object())
    assert observed["reason_code"] == "game_forge_source_detached"
    assert observed["force_not_ready"] is True
    assert result["transport_source_state"] == "failed"


def test_brb_and_resume_synchronise_same_programme_source(monkeypatch):
    _fake_auth(monkeypatch)
    monkeypatch.setattr(guard, "transition_game_live_source", lambda *args: {"same_live_session": True})
    calls = []

    def sync(**kwargs):
        calls.append(kwargs)
        state = "failed" if kwargs["reason_code"] == "game_forge_brb" else "ready"
        return {"id": "programme_1", "state": state}

    monkeypatch.setattr(guard, "_sync_bound_programme_source", sync)

    brb = guard.guarded_transition_game_live_source(
        "game_1",
        "source_1",
        TransitionLiveSourceRequest(presentation_mode="brb"),
        object(),
    )
    resumed = guard.guarded_transition_game_live_source(
        "game_1",
        "source_1",
        TransitionLiveSourceRequest(presentation_mode="playtest"),
        object(),
    )

    assert calls[0]["reason_code"] == "game_forge_brb"
    assert calls[1]["reason_code"] == "game_forge_presentation_ready"
    assert brb["transport_source_state"] == "failed"
    assert resumed["transport_source_state"] == "ready"
    assert brb["same_live_session"] is True
    assert resumed["same_live_session"] is True


def test_version_promotion_refreshes_transport_capabilities(monkeypatch):
    _fake_auth(monkeypatch)
    monkeypatch.setattr(guard, "promote_game_live_version", lambda *args: {"explicit_promotion": True})
    observed = {}
    monkeypatch.setattr(
        guard,
        "_sync_bound_programme_source",
        lambda **kwargs: observed.update(kwargs) or {"id": "programme_1", "state": "ready"},
    )

    result = guard.guarded_promote_game_live_version(
        "game_1",
        "source_1",
        PromoteLiveVersionRequest(expected_project_version=3),
        object(),
    )
    assert observed["reason_code"] == "game_forge_version_promoted"
    assert observed["force_not_ready"] is False
    assert result["transport_source_state"] == "ready"


def test_unbound_source_remains_truthfully_unbound():
    result = guard._with_transport({"ok": True}, None)
    assert result["ok"] is True
    assert result["transport_source_state"] == "unbound"
    assert result["transport_state_synchronised"] is False
