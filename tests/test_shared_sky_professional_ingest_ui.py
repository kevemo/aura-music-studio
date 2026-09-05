from __future__ import annotations

from aura_music_studio import shared_sky_professional_canvas as canvas
from aura_music_studio.shared_sky_professional_ingest_ui import (
    INGEST_HTML,
    INGEST_JS,
    enhanced_ingest_html,
    install_professional_ingest_ui,
)


def _base(_project_id: str) -> str:
    return (
        "<html><head><style></style></head><body>"
        "<section id='transportConsole'><div class='transport-subhead'>Destinations</div></section>"
        "<script>const state={session:{},transport:{}};const transportUI={busy:false};"
        "function renderTransportPanel(){};async function refreshTransport(){};"
        "function activeTransport(){return false};const $=()=>null;"
        "const api=async()=>({});function handle(){};</script></body></html>"
    )


def test_ingest_ui_injects_once_inside_transport_console():
    once = enhanced_ingest_html("project-1", _base)
    twice = enhanced_ingest_html("project-1", lambda _p: once)
    assert once == twice
    assert once.count("id='studioIngestState'") == 1
    assert once.index("id='studioIngestState'") < once.index("Destinations")


def test_ingest_ui_never_persists_or_recovers_secret():
    forbidden = ("localStorage", "sessionStorage", "document.cookie", "?credential=", "token_hash")
    for value in forbidden:
        assert value not in INGEST_JS
    assert "secret_recoverable" not in INGEST_JS
    assert "studioIngestUI.secret=null" in INGEST_JS
    assert "cannot be recovered by Studio" in INGEST_JS


def test_ingest_ui_disables_mutation_while_transport_active():
    assert "activeTransport()" in INGEST_JS
    assert "Stop transport before rotating signed ingest credentials" in INGEST_JS
    assert "Stop transport before revoking signed ingest credentials" in INGEST_JS


def test_ingest_ui_uses_only_studio_ingest_routes_and_explicit_confirmation():
    assert "/ingest`" in INGEST_JS
    assert "/ingest/revoke`" in INGEST_JS
    assert "expected_studio_version:state.session.version" in INGEST_JS
    assert "confirm('Issue a new short-lived signed contribution-ingest credential?" in INGEST_JS
    assert "confirm('Revoke the attached signed ingest credential?" in INGEST_JS


def test_ui_states_media_plane_truth_boundary():
    assert "does not prove an RTMP/SRT/WebRTC termination service is deployed" in INGEST_HTML
    assert "control-plane" in INGEST_HTML


def test_installer_wraps_once_and_preserves_transport_marker(monkeypatch):
    def base(_project_id: str) -> str:
        return _base(_project_id)

    setattr(base, "_shared_sky_transport_toolbar", True)
    monkeypatch.setattr(canvas, "professional_html", base)
    install_professional_ingest_ui(object())
    first = canvas.professional_html
    install_professional_ingest_ui(object())
    second = canvas.professional_html
    assert first is second
    assert getattr(second, "_shared_sky_ingest_ui", False) is True
    assert getattr(second, "_shared_sky_transport_toolbar", False) is True
    assert second("project-1").count("id='studioIngestState'") == 1
