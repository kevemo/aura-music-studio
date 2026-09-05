from __future__ import annotations

import aura_music_studio.creative_version_autopromotion as overlay
from aura_music_studio.deep_daw_automation_ui import (
    DEEP_DAW_AUTOMATION_JS,
    DEEP_DAW_AUTOMATION_MARKER,
    enhance_daw_mixer_javascript,
)


def test_deep_automation_extension_is_idempotent_and_preserves_base_script():
    base = "console.log('legacy mixer stays intact');\n"
    enhanced = enhance_daw_mixer_javascript(base)

    assert base.strip() in enhanced
    assert enhanced.count(DEEP_DAW_AUTOMATION_MARKER) == 1
    assert enhance_daw_mixer_javascript(enhanced) == enhanced


def test_deep_automation_ui_targets_real_v2_audio_automation_contract():
    script = DEEP_DAW_AUTOMATION_JS

    assert "/automation-catalog" in script
    assert "/automation-v2" in script
    assert "method:'PUT'" in script
    assert "method:'DELETE'" in script
    assert "catalog.track" in script
    assert "catalog.clips" in script
    assert "catalog.sends" in script
    assert "catalog.effects" in script
    assert '<option value="linear">Linear</option>' in script
    assert '<option value="hold">Hold</option>' in script
    assert '<option value="smooth">Smooth</option>' in script
    assert "rendered into real audio" in script
    assert "Source audio is never overwritten" in script
    assert "FLAGS.automation" in script


def test_integration_overlay_serves_enhanced_mixer_script_once():
    paths = [getattr(route, "path", None) for route in overlay.router.routes]
    assert paths.count("/daw/mixer-ui.js") == 1

    response = overlay.daw_mixer_ui_with_deep_automation()
    body = response.body.decode("utf-8")
    assert body.count(DEEP_DAW_AUTOMATION_MARKER) == 1
    assert "ESP CHANNEL MIXER" in body
    assert "Deep DAW Automation" in body
    assert response.media_type == "application/javascript"
    assert response.headers["cache-control"] == "private, no-store"
