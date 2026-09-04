from __future__ import annotations

import json
from pathlib import Path

from aura_music_studio.drum_studio_api import _persist_pattern, router


def test_drum_studio_routes_are_bounded_and_production_mounted():
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/projects/{project_name}/daw/drums" in paths
    assert "/projects/{project_name}/daw/drums/patterns" in paths
    assert "/projects/{project_name}/daw/drums/starter/four-on-the-floor" in paths

    source = Path("app.py").read_text(encoding="utf-8")
    assert "from aura_music_studio.drum_studio_api import router as drum_studio_router" in source
    assert "app.include_router(drum_studio_router)" in source


def test_drum_pattern_manifest_is_project_confined_and_truthful(tmp_path):
    project = tmp_path / "member-project"
    project.mkdir()
    payload = {
        "pattern_id": "drums_test",
        "midi_ref": "input/midi/drums/drums_test.mid",
        "symbolic_guide_only": True,
        "audio_rendered": False,
        "final_audio": False,
    }
    ref = _persist_pattern(project, "drums_test", payload)

    assert ref == "work/drum_patterns/drums_test.json"
    target = project / ref
    assert target.is_file()
    stored = json.loads(target.read_text(encoding="utf-8"))
    assert stored["symbolic_guide_only"] is True
    assert stored["audio_rendered"] is False
    assert stored["final_audio"] is False
    assert str(tmp_path) not in target.read_text(encoding="utf-8")


def test_drum_api_reuses_existing_midi_entitlement_and_public_clip_contract():
    source = Path("aura_music_studio/drum_studio_api.py").read_text(encoding="utf-8")
    # _member is the existing DAW MIDI gate backed by AUDIO_TO_MIDI_CONTROL.
    assert "_member(request)" in source
    assert "_public_clip" in source
    assert '"symbolic_guide_only": True' in source
    assert '"audio_rendered": False' in source
    assert '"final_audio": False' in source
