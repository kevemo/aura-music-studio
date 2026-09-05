from __future__ import annotations

from aura_music_studio import shared_sky_professional_canvas as canvas


def test_professional_surface_exposes_real_history_and_scene_controls():
    html = canvas.professional_html("p1")
    for control in (
        "id='undo'",
        "id='redo'",
        "id='newScene'",
        "id='duplicateScene'",
        "id='renameScene'",
        "id='lockScene'",
        "id='noteScene'",
        "id='folderScene'",
        "id='deleteScene'",
        "id='addLowerThird'",
        "id='addTitle'",
        "id='addBanner'",
    ):
        assert control in html
    assert "PREVIEW" in html and "PROGRAMME" in html
    assert "Preview edits are isolated from Programme" in html


def test_pointer_and_multi_select_persistence_use_one_atomic_batch_endpoint():
    js = canvas.PRO_JS
    assert "/sources/batch-transform" in js
    assert "expected_version:state.session.version" in js
    assert "data-rotate=1" in js
    assert "Shift while rotating snaps 15°" in canvas.professional_html("p1")
    assert "Ctrl/Cmd+Z undo" in canvas.professional_html("p1")


def test_source_create_delete_and_history_are_server_backed():
    js = canvas.PRO_JS
    assert "/sources/tracked" in js
    assert "/sources/batch-delete" in js
    assert "historyAction('undo')" in js
    assert "historyAction('redo')" in js
    assert "/history" in js
    assert "state.streams.delete(id)" in js
    assert "getTracks().forEach(t=>t.stop())" in js


def test_typed_graphics_have_actual_dom_renderer_path():
    js = canvas.PRO_JS
    assert "graphicMedia(src)" in js
    assert "src.config?.graphic" in js
    assert "background_opacity" in js
    assert "secondary_text" in js
    assert "graphic-${esc(kind)}" in js


def test_hotkeys_remain_focus_safe():
    js = canvas.PRO_JS
    assert "input,textarea,select,[contenteditable=true]" in js
    assert "historyAction(e.shiftKey?'redo':'undo')" in js
    assert "take('cut')" in js
    assert "take('transition')" in js
