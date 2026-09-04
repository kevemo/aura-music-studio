from __future__ import annotations

from pathlib import Path

from aura_music_studio.daw_api import router


def test_midi_humanisation_route_is_mounted_on_production_daw_router():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/projects/{project_name}/daw/midi/{clip_id}/humanize" in paths

    app_source = Path("app.py").read_text(encoding="utf-8")
    assert "from aura_music_studio.daw_api import router as daw_router" in app_source
    assert "app.include_router(daw_router)" in app_source


def test_midi_humanisation_route_preserves_entitlement_and_symbolic_truth():
    source = Path("aura_music_studio/daw_api.py").read_text(encoding="utf-8")
    assert "member.plan.has(AUDIO_TO_MIDI_CONTROL)" in source
    assert '"symbolic_guide_only": True' in source
    assert '"audio_rendered": False' in source
    assert '"final_audio": False' in source
    assert '"action": "midi_humanize"' in source
