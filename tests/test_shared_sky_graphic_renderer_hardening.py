from __future__ import annotations

from aura_music_studio.shared_sky_professional_motion_graphics_ui import MOTION_JS


def test_renderer_whitelists_all_style_primitives_even_for_generic_graphic_config():
    assert "function boundedNumber" in MOTION_JS
    assert "function safeGraphicHex" in MOTION_JS
    assert "function safeGraphicAlign" in MOTION_JS
    assert "['left','center','right'].includes(v)" in MOTION_JS
    assert "Math.max(min,Math.min(max,n))" in MOTION_JS
    assert "^#[0-9a-f]{6}$" in MOTION_JS


def test_non_motion_graphics_are_rendered_through_safe_style_path():
    assert "if(g)return staticGraphicMedia(src,g)" in MOTION_JS
    assert "style='${motionStyle(g)}'" in MOTION_JS
    assert "replace(/[^a-z0-9_-]/gi,'')" in MOTION_JS
    assert "baseMotionGraphicMedia(src)" in MOTION_JS


def test_ticker_text_and_countdown_attributes_are_bounded_and_escaped():
    assert ".map(v=>String(v).slice(0,160))" in MOTION_JS
    assert "String(g.separator||' • ').slice(0,12)" in MOTION_JS
    assert "esc(String(g.target_at||'').slice(0,80))" in MOTION_JS
    assert "esc(String(g.label||'Starting in').slice(0,120))" in MOTION_JS
    assert "esc(String(g.complete_text||'Starting now').slice(0,120))" in MOTION_JS
