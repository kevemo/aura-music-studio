from __future__ import annotations

import pytest

from aura_music_studio.game_forge_accessibility import harden_game_runtime_html


def _document() -> str:
    return """<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'></head><body><canvas id='game'></canvas><div id='status'>Starting</div><div id='fallback'></div><button id='audio-toggle'>Audio</button><button id='video-toggle'>Video</button><video id='cutscene'></video></body></html>"""


def test_runtime_hardening_adds_mobile_touch_keyboard_and_screen_reader_contract():
    html = harden_game_runtime_html(_document())
    assert "id='aura-mobile-controls'" in html
    assert "data-aura-key='ArrowUp'" in html
    assert "data-aura-key='ArrowLeft'" in html
    assert "data-aura-key='ArrowDown'" in html
    assert "data-aura-key='ArrowRight'" in html
    assert "min-height:44px" in html
    assert "min-width:44px" in html
    assert "prefers-reduced-motion:reduce" in html
    assert "canvas.tabIndex=0" in html
    assert "Interactive Aura 3D game canvas" in html
    assert "aria-live='polite'" in html
    assert "role='status'" in html
    assert "viewport-fit=cover" in html
    assert "safe-area-inset-bottom" in html


def test_runtime_hardening_is_idempotent():
    once = harden_game_runtime_html(_document())
    twice = harden_game_runtime_html(once)
    assert twice == once
    assert twice.count("id='aura-runtime-accessibility'") == 1
    assert twice.count("id='aura-mobile-controls'") == 1


def test_runtime_hardening_refuses_non_document_fragment():
    with pytest.raises(ValueError):
        harden_game_runtime_html("<canvas id='game'></canvas>")


def test_runtime_controls_use_pointer_events_without_external_network_calls():
    html = harden_game_runtime_html(_document())
    assert "pointerdown" in html
    assert "pointerup" in html
    assert "KeyboardEvent" in html
    assert "fetch(" not in html
    assert "XMLHttpRequest" not in html
    assert "WebSocket" not in html
