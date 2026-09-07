from __future__ import annotations

import json

import pytest

from aura_music_studio.aura_effect_system_creator import EffectNodeSpec, make_effect_system
from aura_music_studio.aura_effect_system_project import (
    apply_effect_system,
    list_saved_effect_systems,
    load_effect_system,
    preview_project_effect_system,
    restore_effect_system_revision,
    save_effect_system,
)
from aura_music_studio.daw import load_session
from aura_music_studio.session import StudioSession


class StubEntitlements:
    def __init__(self, denied: set[str] | None = None):
        self.denied = denied or set()

    def has_entitlement(self, user_id: str, effect_id: str) -> dict:
        return {
            "effect_id": effect_id,
            "owned": effect_id not in self.denied,
            "included": effect_id.endswith("gain"),
            "entitlement_band": "core" if effect_id.endswith("gain") else "silver",
            "coin_price": 0 if effect_id.endswith("gain") else 200,
            "source": "test",
        }


def _project(tmp_path):
    project = tmp_path / "song"
    project.mkdir()
    session = StudioSession(name="song")
    session.add_track("Master", "master")
    track = session.add_track("Lead Vocal", "vocals")
    session.save(project / "aura_session.json")
    return project, track.id


def _system(version: int = 1):
    return make_effect_system(
        "aura.vocal.space",
        "Aura Vocal Space",
        [
            EffectNodeSpec(id="clean_gain", catalogue_item_id="music.fx.gain", parameters={"db": 2.0}),
            EffectNodeSpec(id="room", catalogue_item_id="music.fx.reverb", parameters={"mix": 0.2}),
        ],
        description="Reusable vocal-space chain",
        version=version,
    )


def test_save_load_list_reusable_effect_system(tmp_path):
    project, _track_id = _project(tmp_path)
    saved = save_effect_system(project, _system())
    assert saved["saved"] is True
    assert saved["source_media_mutated"] is False
    assert saved["project_relative_path"].startswith("work/effect_systems/")

    loaded = load_effect_system(project, "aura.vocal.space")
    assert loaded.id == "aura.vocal.space"
    assert loaded.version == 1
    assert [node.catalogue_item_id for node in loaded.nodes] == ["music.fx.gain", "music.fx.reverb"]

    rows = list_saved_effect_systems(project)
    assert len(rows) == 1
    assert rows[0]["backend_executable"] is True
    assert rows[0]["node_count"] == 2


def test_saved_system_records_server_canonical_catalogue_provenance(tmp_path):
    project, _track_id = _project(tmp_path)
    saved = save_effect_system(project, _system())

    assert saved["record_schema_version"] == 2
    assert len(saved["catalogue_provenance"]) == 2
    assert saved["catalogue_provenance_fingerprint"]
    assert [row["catalogue_item_id"] for row in saved["catalogue_provenance"]] == [
        "music.fx.gain",
        "music.fx.reverb",
    ]
    for row in saved["catalogue_provenance"]:
        assert row["catalogue_item_version"] == 1
        assert row["metadata_schema_version"] == 1
        assert row["source_kind"] == "esp_original_runtime_mapping"
        assert row["source_author"] == "Elevate Souls Productions"
        assert row["license_id"] is None
        assert row["rights_status"] == "not_asserted"
        assert row["rights_record_id"] is None
        assert row["source_asset_ids"] == []
        assert row["entitlement_granted"] is False
        assert row["execution_authorized"] is False
        assert "command" not in row
        assert "provider_secret" not in row

    rows = list_saved_effect_systems(project)
    assert rows[0]["catalogue_provenance_recorded"] is True
    assert rows[0]["catalogue_provenance"] == saved["catalogue_provenance"]
    assert rows[0]["catalogue_provenance_fingerprint"] == saved["catalogue_provenance_fingerprint"]


def test_saved_catalogue_provenance_tampering_fails_closed(tmp_path):
    project, _track_id = _project(tmp_path)
    saved = save_effect_system(project, _system())
    path = project / saved["project_relative_path"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["catalogue_provenance"][0]["source_author"] = "Client supplied author"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="provenance integrity"):
        load_effect_system(project, "aura.vocal.space")
    assert list_saved_effect_systems(project) == []


def test_legacy_saved_system_without_provenance_remains_loadable(tmp_path):
    project, _track_id = _project(tmp_path)
    saved = save_effect_system(project, _system())
    path = project / saved["project_relative_path"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("record_schema_version")
    payload.pop("catalogue_provenance")
    payload.pop("catalogue_provenance_fingerprint")
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert load_effect_system(project, "aura.vocal.space").id == "aura.vocal.space"
    rows = list_saved_effect_systems(project)
    assert rows[0]["catalogue_provenance_recorded"] is False
    assert rows[0]["catalogue_provenance"] == []
    assert rows[0]["catalogue_provenance_fingerprint"] is None


def test_same_version_cannot_silently_change_saved_graph(tmp_path):
    project, _track_id = _project(tmp_path)
    save_effect_system(project, _system())
    changed = make_effect_system(
        "aura.vocal.space",
        "Aura Vocal Space",
        [EffectNodeSpec(id="clean_gain", catalogue_item_id="music.fx.gain", parameters={"db": 8.0})],
        version=1,
    )
    with pytest.raises(ValueError, match="version increment"):
        save_effect_system(project, changed)
    upgraded = make_effect_system(
        "aura.vocal.space",
        "Aura Vocal Space",
        [EffectNodeSpec(id="clean_gain", catalogue_item_id="music.fx.gain", parameters={"db": 8.0})],
        version=2,
    )
    assert save_effect_system(project, upgraded)["system"]["version"] == 2


def test_integrity_mismatch_fails_closed_on_load(tmp_path):
    project, _track_id = _project(tmp_path)
    saved = save_effect_system(project, _system())
    path = project / saved["project_relative_path"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["fingerprint"] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        load_effect_system(project, "aura.vocal.space")
    assert list_saved_effect_systems(project) == []


def test_preview_reports_entitlement_without_mutating_project(tmp_path):
    project, track_id = _project(tmp_path)
    before = (project / "aura_session.json").read_bytes()
    preview = preview_project_effect_system(
        project,
        track_id,
        _system(),
        user_id="member-1",
        entitlement_store=StubEntitlements(denied={"music.fx.reverb"}),
    )
    assert preview["can_apply"] is False
    assert preview["missing_entitlement_effect_ids"] == ["music.fx.reverb"]
    assert preview["project_mutated"] is False
    assert preview["source_media_mutated"] is False
    assert "volume=2.0dB" in preview["ffmpeg_filter_chain"]
    assert (project / "aura_session.json").read_bytes() == before


def test_apply_requires_all_effect_entitlements_before_revision_or_mutation(tmp_path):
    project, track_id = _project(tmp_path)
    before = (project / "aura_session.json").read_bytes()
    with pytest.raises(PermissionError, match="not owned"):
        apply_effect_system(
            project,
            track_id,
            _system(),
            user_id="member-1",
            entitlement_store=StubEntitlements(denied={"music.fx.reverb"}),
        )
    assert (project / "aura_session.json").read_bytes() == before
    revision_root = project / "work" / "revisions"
    assert not revision_root.exists() or not any(revision_root.iterdir())


def test_apply_creates_revision_then_real_daw_effects_and_is_idempotent(tmp_path):
    project, track_id = _project(tmp_path)
    result = apply_effect_system(
        project,
        track_id,
        _system(),
        user_id="member-1",
        entitlement_store=StubEntitlements(),
    )
    assert result["applied"] is True
    assert result["undo_available"] is True
    assert result["revision_id"]
    assert result["source_media_mutated"] is False

    session = load_session(project)
    track = session.find_track(track_id)
    assert [effect.type for effect in track.effects] == ["gain", "reverb"]
    assert all(effect.id.startswith("system.") for effect in track.effects)
    assert track.metadata["effect_systems"][0]["fingerprint"] == result["fingerprint"]

    duplicate = apply_effect_system(
        project,
        track_id,
        _system(),
        user_id="member-1",
        entitlement_store=StubEntitlements(),
    )
    assert duplicate["already_applied"] is True
    assert len(load_session(project).find_track(track_id).effects) == 2


def test_revision_restore_undoes_effect_system_application(tmp_path):
    project, track_id = _project(tmp_path)
    applied = apply_effect_system(
        project,
        track_id,
        _system(),
        user_id="member-1",
        entitlement_store=StubEntitlements(),
    )
    assert len(load_session(project).find_track(track_id).effects) == 2

    restored = restore_effect_system_revision(project, applied["revision_id"])
    assert restored["effect_system_undo"] is True
    assert restored["source_media_mutated"] is False
    assert len(load_session(project).find_track(track_id).effects) == 0


def test_project_system_store_rejects_path_traversal_identifier(tmp_path):
    project, _track_id = _project(tmp_path)
    bad = make_effect_system(
        "safe.id",
        "Safe",
        [EffectNodeSpec(id="n", catalogue_item_id="music.fx.gain")],
    )
    object.__setattr__(bad, "id", "../escape")
    with pytest.raises(ValueError):
        save_effect_system(project, bad)
