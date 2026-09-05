from __future__ import annotations

from fastapi import FastAPI

from aura_music_studio.shared_skies_live_assist import (
    auto_cue_html,
    guardian_projection,
    install_shared_skies_live_assist,
)


def test_auto_cue_is_browser_local_and_has_required_operator_controls():
    html = auto_cue_html("test-nonce")

    assert "Shared Skies does not upload or persist the text" in html
    assert "localStorage" not in html
    assert "sessionStorage" not in html
    assert "fetch(" not in html
    assert "XMLHttpRequest" not in html
    assert "navigator.sendBeacon" not in html
    assert "3s Countdown" in html
    assert "Mirror" in html
    assert "Fullscreen" in html
    assert "Second screen" in html
    assert "requestAnimationFrame" in html
    assert 'nonce="test-nonce"' in html


def test_guardian_is_advisory_and_never_acquires_consequential_authority():
    state = guardian_projection(
        {"id": "live-1", "state": "live", "user_id": "creator-1"},
        effective_moderators=2,
    )

    assert state["product"] == "Rhiannon LIVE Guardian"
    assert state["live_active"] is True
    assert state["effective_assigned_moderators"] == 2
    assert state["assistant_authority"] == "read_only_advisory"
    assert state["provider_moderation_write"]["ready"] is False
    assert state["permission_model"]["agent_role_alone_grants_moderation"] is False
    assert state["permission_model"]["delegated_moderator_requires_owner_enabled_permission"] is True
    assert state["permission_model"]["delegated_moderator_requires_live_assignment"] is True
    assert "execute_provider_moderation_write" in state["prohibited_authority"]
    assert "mutate_battle_score" in state["prohibited_authority"]
    assert "mutate_coin_or_gift_finance" in state["prohibited_authority"]


def test_guardian_reports_non_live_state_without_fabricating_readiness():
    state = guardian_projection({"id": "live-2", "state": "ended"})
    assert state["live_active"] is False
    assert state["broadcast_state"] == "ended"
    assert state["provider_moderation_write"]["ready"] is False


def test_live_assist_routes_mount_exactly_once():
    app = FastAPI()
    install_shared_skies_live_assist(app)
    install_shared_skies_live_assist(app)

    signatures = [
        (
            getattr(route, "path", ""),
            tuple(sorted(getattr(route, "methods", set()) or set())),
        )
        for route in app.router.routes
    ]
    auto_cue = [item for item in signatures if item[0] == "/shared-sky/live/auto-cue"]
    guardian = [
        item
        for item in signatures
        if item[0] == "/shared-sky/live/api/watch/{broadcast_id}/rhiannon-guardian/readiness"
    ]
    assert auto_cue == [("/shared-sky/live/auto-cue", ("GET",))]
    assert guardian == [
        ("/shared-sky/live/api/watch/{broadcast_id}/rhiannon-guardian/readiness", ("GET",))
    ]
