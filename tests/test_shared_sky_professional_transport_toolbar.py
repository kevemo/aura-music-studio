from __future__ import annotations

from aura_music_studio import shared_sky_professional_canvas as canvas
from aura_music_studio.shared_sky_professional_transport_toolbar import (
    TOOLBAR_HTML,
    TOOLBAR_JS,
    enhanced_professional_html,
    install_professional_transport_toolbar,
)


def _minimal_page(_project_id: str) -> str:
    return (
        "<html><head><style>.base{}</style></head><body>"
        "<aside class='right panel'><h2>Inspector</h2></aside>"
        "<script>function render(){};const state={session:null,transport:null};"
        "const $=()=>null,$$=()=>[];const api=async()=>({});"
        "function handle(){};async function refreshProject(){};function esc(v){return v;}</script>"
        "</body></html>"
    )


def test_toolbar_injection_is_idempotent_and_keeps_existing_studio_markup():
    once = enhanced_professional_html("project-1", _minimal_page)
    twice = enhanced_professional_html("project-1", lambda _project_id: once)

    assert once == twice
    assert once.count("id='transportConsole'") == 1
    assert "<h2>Inspector</h2>" in once
    assert "Broadcast transport controls" in once
    assert "preflight passed" not in TOOLBAR_HTML.lower()
    assert ">LIVE<" not in TOOLBAR_HTML


def test_toolbar_uses_only_chat2_chat3_operator_contracts_and_unique_idempotency_keys():
    required_routes = {
        "/broadcast",
        "/transport/status",
        "/transport/preflight",
        "/transport/start",
        "/transport/stop",
        "/transport/retry-destination",
        "/transport/recordings",
        "/markers",
    }
    for route in required_routes:
        assert route in TOOLBAR_JS

    assert "crypto?.randomUUID" in TOOLBAR_JS
    assert "idempotency_key:idem('studio-start')" in TOOLBAR_JS
    assert "idempotency_key:idem('studio-stop')" in TOOLBAR_JS
    assert "idempotency_key:idem('studio-retry')" in TOOLBAR_JS
    assert "preflightTransportUI" in TOOLBAR_JS
    assert "if(!ready)return" in TOOLBAR_JS


def test_toolbar_does_not_claim_live_or_recording_from_button_clicks():
    assert "LIVE is shown only from authoritative Chat 2 state" in TOOLBAR_JS
    assert "will not claim active recording until Chat 2 reports it" in TOOLBAR_JS
    assert "A request is not displayed as recording until Chat 2 reports its state" in TOOLBAR_JS
    assert "state.transport=await api" in TOOLBAR_JS
    assert "setInterval" in TOOLBAR_JS


def test_toolbar_exposes_preflight_evidence_destination_retry_and_stop_confirmation():
    assert "blocking_errors" in TOOLBAR_JS
    assert "warnings" in TOOLBAR_JS
    assert "data-retry-destination" in TOOLBAR_JS
    assert "confirm('Stop the active Shared Sky broadcast and its delivery paths?')" in TOOLBAR_JS
    assert "role='status'" not in TOOLBAR_HTML
    assert "aria-live='polite'" in TOOLBAR_HTML


def test_runtime_preflight_message_is_only_set_after_authoritative_response():
    assert "const d=await api(`/shared-sky/studio/api/sessions/${state.session.id}/transport/preflight`" in TOOLBAR_JS
    assert "transportUI.preflight=d.preflight||null" in TOOLBAR_JS
    assert "transportUI.preflight?.ready?'Authoritative transport preflight passed.'" in TOOLBAR_JS
    assert "Transport preflight is blocked; review the listed evidence." in TOOLBAR_JS


def test_installer_wraps_canvas_renderer_once(monkeypatch):
    monkeypatch.setattr(canvas, "professional_html", _minimal_page)

    install_professional_transport_toolbar(object())
    first = canvas.professional_html
    install_professional_transport_toolbar(object())
    second = canvas.professional_html

    assert first is second
    page = second("project-1")
    assert page.count("id='transportConsole'") == 1
    assert getattr(second, "_shared_sky_transport_toolbar", False) is True
