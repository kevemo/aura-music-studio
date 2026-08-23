import json
from pathlib import Path

import pytest

from aura_music_studio import __version__
from aura_music_studio.build_around import BuildAroundRequest
from aura_music_studio.fx_designer import FxDesignRequest, design_fx
from aura_music_studio.job_api import _public
from aura_music_studio.plans import AI_FX_DESIGNER, MULTITRACK_DAW, PLUGIN_RACK, get_plan
from aura_music_studio.plugin_rack import load_plugin_catalog, public_plugin_catalog
from aura_music_studio.source_detection import detect_source_role


def test_version_is_visual_daw_suite_0140():
    assert __version__ == "0.14.0"


def test_public_job_never_exposes_private_payload_json():
    public = _public({
        "id": "job1",
        "status": "queued",
        "payload_json": json.dumps({"lyrics": "private draft lyric", "extra_direction": "private"}),
        "result_json": None,
    })
    assert "payload_json" not in public
    assert "private draft lyric" not in json.dumps(public)


def test_pro_owns_multitrack_ai_fx_and_plugin_rack_entitlements():
    base = get_plan("base")
    pro = get_plan("pro")
    assert not base.has(MULTITRACK_DAW)
    assert not base.has(AI_FX_DESIGNER)
    assert not base.has(PLUGIN_RACK)
    assert pro.has(MULTITRACK_DAW)
    assert pro.has(AI_FX_DESIGNER)
    assert pro.has(PLUGIN_RACK)


def test_build_around_request_supports_complete_and_multitrack_modes():
    complete = BuildAroundRequest(asset_id="a", source_role="vocals")
    multitrack = BuildAroundRequest(asset_id="a", source_role="guitar", output_mode="multitrack")
    assert complete.output_mode == "complete_mix"
    assert multitrack.output_mode == "multitrack"


def test_fx_designer_safe_fallback_uses_whitelisted_effects(monkeypatch):
    monkeypatch.setenv("AURA_PRODUCER_USE_OLLAMA", "false")
    result = design_fx(FxDesignRequest(
        description="warm wide vocal with subtle double and short room reverb",
        category="vocal",
        max_effects=8,
    ))
    assert result.source == "deterministic_fallback"
    assert result.effects
    assert all(effect.type != "custom_safe_chain" for effect in result.effects)


def test_plugin_catalog_allows_only_admin_configured_directories(tmp_path, monkeypatch):
    allowed = tmp_path / "approved"
    allowed.mkdir()
    plugin = allowed / "Test.vst3"
    plugin.write_bytes(b"placeholder")
    catalog = tmp_path / "plugins.json"
    catalog.write_text(json.dumps({"plugins": [{
        "id": "test",
        "name": "Test",
        "category": "test",
        "format": "vst3",
        "path": str(plugin),
        "enabled": True,
    }]}), encoding="utf-8")
    monkeypatch.setenv("AURA_PLUGIN_ALLOWED_DIRS", str(allowed))
    monkeypatch.setenv("AURA_PLUGIN_CATALOG_PATH", str(catalog))
    rows = load_plugin_catalog()
    assert rows[0].id == "test"
    assert public_plugin_catalog()[0]["installed"] is True


def test_plugin_catalog_rejects_path_outside_allowlist(tmp_path, monkeypatch):
    allowed = tmp_path / "approved"
    allowed.mkdir()
    outside = tmp_path / "outside.vst3"
    outside.write_bytes(b"placeholder")
    catalog = tmp_path / "plugins.json"
    catalog.write_text(json.dumps({"plugins": [{
        "id": "outside",
        "name": "Outside",
        "format": "vst3",
        "path": str(outside),
        "enabled": True,
    }]}), encoding="utf-8")
    monkeypatch.setenv("AURA_PLUGIN_ALLOWED_DIRS", str(allowed))
    monkeypatch.setenv("AURA_PLUGIN_CATALOG_PATH", str(catalog))
    with pytest.raises(PermissionError):
        load_plugin_catalog()


def test_source_role_prefers_explicit_upload_metadata_without_reading_audio():
    result = detect_source_role(Path("/does/not/need/to/exist.wav"), name="Kev_lead_vocal_take.wav", tags=["vocal"])
    assert result["role"] == "vocals"
    assert result["confidence"] > .9
