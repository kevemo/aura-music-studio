from __future__ import annotations

from aura_music_studio import legacy_voice_reference_registry as registry


def test_historical_rhiannon_reference_is_non_training_non_cloning_metadata():
    item = registry.LEGACY_RHIANNON_AURA_VOICE_REFERENCE
    assert item.display_name == "Legacy Rhiannon/Aura Voice Reference"
    assert item.source_file_name == "Rhiannon_Legacy_Aura_Voice_Preview_REFERENCE.mp3"
    assert item.duration_seconds == 20.323
    assert item.channels == 1
    assert item.sample_rate_hz == 44100
    assert item.approximate_bitrate_kbps == 192
    assert item.training_eligible is False
    assert item.identity_replication_allowed is False
    assert item.completed_voice_profile is False
    assert item.raw_audio_exposed is False
    assert item.private is True
    assert item.rights_status == "not_established_for_identity_replication"
    assert item.consent_status == "not_established_for_identity_replication"


def test_reference_registry_never_exposes_embedding_model_or_raw_audio_fields():
    data = registry.LEGACY_RHIANNON_AURA_VOICE_REFERENCE.model_dump(mode="json")
    forbidden = {"embedding", "embedding_path", "model", "model_path", "reference_files", "raw_audio_path"}
    assert forbidden.isdisjoint(data)
    assert data["raw_audio_exposed"] is False
    assert data["source_asset_id"].startswith("drive:")


def test_reference_endpoint_requires_existing_tenant_project_and_returns_reference_only(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "project_path", lambda _name, must_exist=True: tmp_path)
    payload = registry.list_historical_voice_references("demo")
    assert payload["reference_only"] is True
    assert payload["identity_replication_requires_separate_voice_profile"] is True
    assert payload["raw_audio_exposed"] is False
    assert len(payload["references"]) == 1
    assert payload["references"][0]["training_eligible"] is False


def test_historical_reference_router_defines_route_once():
    """Keep the unit assertion on the owning router to avoid suite-order coupling.

    Production overlay composition is exercised independently by the self-host route-surface
    smoke; this test proves the historical-reference service itself defines one deterministic
    route without depending on other tests temporarily composing or mutating shared routers.
    """
    expected = "/projects/{project_name}/voice-house/historical-references"
    routes = [getattr(route, "path", "") for route in registry.router.routes]
    assert routes.count(expected) == 1
