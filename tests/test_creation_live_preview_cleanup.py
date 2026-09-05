from __future__ import annotations

from aura_music_studio import creation_live as cl
from aura_music_studio.creation_live_hardening import install_creation_live_hardening


install_creation_live_hardening()


def test_any_new_preview_stops_previous_workspace_capture_first():
    script = cl.LIVE_UI_SCRIPT
    needle = (
        "async function preview(){const s=state.selected;if(!s)return msg('Select a source first.',true);"
        "const box=$('clPreview');box.replaceChildren();"
        "if(state.previewStream){state.previewStream.getTracks().forEach(t=>t.stop());state.previewStream=null;}"
    )
    assert needle in script


def test_changing_selected_source_immediately_stops_workspace_capture():
    script = cl.LIVE_UI_SCRIPT
    assert "state.selected=state.sources.find(s=>s.source_adapter_id===x.value);if(state.previewStream)" in script
    assert "const p=$('clPreview');if(p)p.replaceChildren();" in script


def test_drawer_close_still_cleans_capture_and_community_timer():
    script = cl.LIVE_UI_SCRIPT
    assert "clearInterval(state.communityTimer)" in script
    assert "state.previewStream.getTracks().forEach(t=>t.stop())" in script
