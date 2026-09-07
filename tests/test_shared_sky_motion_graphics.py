from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from aura_music_studio import shared_sky_motion_graphics as motion
from aura_music_studio.shared_sky_control_room import StudioInvariantError
from aura_music_studio.shared_sky_professional_motion_graphics_ui import (
    MOTION_CSS,
    MOTION_JS,
    enhanced_motion_html,
    install_professional_motion_graphics_ui,
)


def test_static_ticker_requires_content(monkeypatch):
    with pytest.raises(StudioInvariantError):
        motion.create_ticker(
            "user-1",
            "session-1",
            motion.TickerCreateRequest(items=[], binding="static", expected_version=1),
        )


def test_ticker_binding_is_bounded_and_never_requests_external_network(monkeypatch):
    captured = {}

    def fake_create(user_id, session_id, body):
        captured.update(user_id=user_id, session_id=session_id, body=body)
        return "source-ticker"

    monkeypatch.setattr(motion.history_repo, "create_source", fake_create)
    source_id = motion.create_ticker(
        "user-1",
        "session-1",
        motion.TickerCreateRequest(
            items=["fallback"],
            binding="transport_state",
            prefix="Live",
            speed_seconds=15,
            direction="right",
            expected_version=7,
        ),
    )
    assert source_id == "source-ticker"
    body = captured["body"]
    assert body.source_type == "text"
    assert body.expected_version == 7
    graphic = body.config["graphic"]
    assert graphic["binding"] == "transport_state"
    assert graphic["authoritative_binding"] is True
    assert graphic["external_network_fetch"] is False
    assert body.config["privacy"] == "programme_safe"


def test_ticker_items_are_cleaned_and_bounded():
    body = motion.TickerCreateRequest(
        items=["  hello   world  ", "\x00second"],
        binding="static",
        expected_version=1,
    )
    assert body.items == ["hello world", "second"]


def test_countdown_requires_timezone():
    with pytest.raises(ValidationError):
        motion.CountdownCreateRequest(
            target_at=datetime(2026, 9, 5, 20, 0, 0),
            expected_version=1,
        )


def test_countdown_persists_timezone_target_and_is_preview_only(monkeypatch):
    captured = {}

    def fake_create(user_id, session_id, body):
        captured["body"] = body
        return "source-countdown"

    monkeypatch.setattr(motion.history_repo, "create_source", fake_create)
    source_id = motion.create_countdown(
        "user-1",
        "session-1",
        motion.CountdownCreateRequest(
            label="Starts in",
            target_at=datetime(2026, 9, 5, 20, 0, tzinfo=timezone.utc),
            complete_text="We are live",
            expected_version=4,
        ),
    )
    assert source_id == "source-countdown"
    body = captured["body"]
    assert body.expected_version == 4
    assert body.config["graphic"]["target_at"].endswith("+00:00")
    assert body.config["graphic"]["external_network_fetch"] is False


def test_motion_graphics_reject_arbitrary_binding_and_bad_colour():
    with pytest.raises(ValidationError):
        motion.TickerCreateRequest(binding="external_url", expected_version=1)
    with pytest.raises(StudioInvariantError):
        motion.create_ticker(
            "user-1",
            "session-1",
            motion.TickerCreateRequest(
                items=["safe"], style={"text_color": "red"}, expected_version=1
            ),
        )


def test_motion_route_installer_is_idempotent_on_production_app():
    from app import app

    expected = {
        "/shared-sky/studio/api/sessions/{session_id}/graphics/ticker",
        "/shared-sky/studio/api/sessions/{session_id}/graphics/countdown",
    }

    def matching_paths():
        return [
            getattr(route, "path", "")
            for route in app.router.routes
            if getattr(route, "path", "") in expected
        ]

    before = matching_paths()
    motion.install_shared_sky_motion_graphics(app)
    after_once = matching_paths()
    motion.install_shared_sky_motion_graphics(app)
    after_twice = matching_paths()
    assert set(after_once) == expected
    assert after_twice == after_once
    assert len(after_twice) == 2
    assert set(before).issubset(expected)


def _base(_project_id: str) -> str:
    return (
        "<html><head><style></style></head><body>"
        "<button id='addBanner'>Banner</button>"
        "<script>function graphicMedia(){return ''};function render(){};function rgba(){return ''};"
        "const state={session:{id:'s',version:1},transport:{state:'live',recordings:[]}};"
        "const $=()=>null,$$=()=>[];function esc(v){return v};const api=async()=>({});"
        "async function refreshProject(){};function handle(){}</script></body></html>"
    )


def test_motion_ui_injection_and_reduced_motion_are_real():
    page = enhanced_motion_html("project-1", _base)
    assert page.count("id='addTicker'") == 1
    assert page.count("id='addCountdown'") == 1
    assert "@keyframes shared-sky-ticker" in MOTION_CSS
    assert "prefers-reduced-motion:reduce" in MOTION_CSS
    assert "animation:none!important" in MOTION_CSS


def test_ticker_ui_reads_only_authoritative_in_memory_transport_state():
    assert "state.transport?.state" in MOTION_JS
    assert "state.transport?.recordings" in MOTION_JS
    assert "transport_state" in MOTION_JS
    assert "recording_state" in MOTION_JS
    assert "fetch(" not in MOTION_JS
    assert "external_url" not in MOTION_JS


def test_countdown_renderer_uses_timezone_timestamp_and_completion_text():
    assert "Date.parse(node.dataset.countdownTarget" in MOTION_JS
    assert "Date.now()" in MOTION_JS
    assert "dataset.countdownComplete" in MOTION_JS
    assert "setInterval(updateCountdowns,1000)" in MOTION_JS


def test_motion_ui_installer_is_idempotent(monkeypatch):
    from aura_music_studio import shared_sky_professional_canvas as canvas

    monkeypatch.setattr(canvas, "professional_html", _base)
    install_professional_motion_graphics_ui(object())
    first = canvas.professional_html
    install_professional_motion_graphics_ui(object())
    second = canvas.professional_html
    assert first is second
    assert second("project-1").count("id='addTicker'") == 1
