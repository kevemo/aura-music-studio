from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from aura_music_studio.aura_effect_system_creator import EffectNodeSpec, make_effect_system
from aura_music_studio.aura_effect_system_extended_api import (
    EffectSystemAutosaveRequest,
    effect_system_extended_route_registrations,
)
from aura_music_studio.aura_effect_system_project import load_effect_system, save_effect_system
from aura_music_studio.aura_effect_system_recovery import (
    discard_effect_system_autosave,
    load_effect_system_autosave,
    save_effect_system_autosave,
)
from aura_music_studio.session import StudioSession


def _project(tmp_path):
    project = tmp_path / "song"
    project.mkdir()
    session = StudioSession(name="song")
    session.add_track("Master", "master")
    session.add_track("Lead Vocal", "vocals")
    session.save(project / "aura_session.json")
    return project


def _system(*, version: int = 1, db: float = 2.0):
    return make_effect_system(
        "aura.vocal.space",
        "Aura Vocal Space",
        [
            EffectNodeSpec(
                id="clean_gain",
                catalogue_item_id="music.fx.gain",
                parameters={"db": db},
            ),
            EffectNodeSpec(
                id="room",
                catalogue_item_id="music.fx.reverb",
                parameters={"mix": 0.2},
            ),
        ],
        description="Recoverable vocal-space chain",
        version=version,
    )


def test_autosave_round_trip_is_non_authorizing_and_non_mutating(tmp_path):
    project = _project(tmp_path)
    save_effect_system(project, _system())
    session_before = (project / "aura_session.json").read_bytes()
    canonical_before = load_effect_system(project, "aura.vocal.space")

    result = save_effect_system_autosave(project, _system(version=2, db=4.0))
    assert result["autosaved"] is True
    assert result["autosaved_at"]
    assert result["entitlement_granted"] is False
    assert result["execution_authorized"] is False
    assert result["canonical_saved_system_mutated"] is False
    assert result["project_session_mutated"] is False
    assert result["source_media_mutated"] is False
    assert result["baseline_saved_version"] == 1
    assert result["catalogue_provenance_fingerprint"]

    recovered = load_effect_system_autosave(project, "aura.vocal.space")
    assert recovered["system"]["version"] == 2
    assert recovered["canonical_conflict"] is False
    assert recovered["canonical_save_requires_explicit_action"] is True
    assert recovered["safe_to_replace_canonical_without_reconciliation"] is True
    assert recovered["entitlement_granted"] is False
    assert recovered["execution_authorized"] is False

    canonical_after = load_effect_system(project, "aura.vocal.space")
    assert canonical_after.version == canonical_before.version == 1
    assert (project / "aura_session.json").read_bytes() == session_before


def test_recovery_detects_canonical_save_advanced_after_autosave(tmp_path):
    project = _project(tmp_path)
    save_effect_system(project, _system())
    save_effect_system_autosave(project, _system(version=2, db=4.0))

    save_effect_system(project, _system(version=2, db=6.0))
    recovered = load_effect_system_autosave(project, "aura.vocal.space")

    assert recovered["baseline_saved_version"] == 1
    assert recovered["current_saved_version"] == 2
    assert recovered["canonical_conflict"] is True
    assert recovered["safe_to_replace_canonical_without_reconciliation"] is False
    assert recovered["canonical_save_requires_explicit_action"] is True


def test_recovery_provenance_tampering_fails_closed(tmp_path):
    project = _project(tmp_path)
    saved = save_effect_system_autosave(project, _system())
    path = project / saved["project_relative_path"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["catalogue_provenance"][0]["source_author"] = "Client supplied"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="provenance integrity"):
        load_effect_system_autosave(project, "aura.vocal.space")


def test_recovery_graph_tampering_fails_closed(tmp_path):
    project = _project(tmp_path)
    saved = save_effect_system_autosave(project, _system())
    path = project / saved["project_relative_path"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["system"]["nodes"][0]["parameters"]["db"] = 9.0
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="integrity"):
        load_effect_system_autosave(project, "aura.vocal.space")


def test_recovery_read_rejects_oversized_draft(tmp_path):
    project = _project(tmp_path)
    saved = save_effect_system_autosave(project, _system())
    path = project / saved["project_relative_path"]
    path.write_bytes(b"{" + (b" " * (513 * 1024)) + b"}")

    with pytest.raises(ValueError, match="allowed size"):
        load_effect_system_autosave(project, "aura.vocal.space")


def test_recovery_missing_read_has_no_directory_creation_side_effect(tmp_path):
    project = _project(tmp_path)
    recovery_root = project / "work" / "effect_system_autosaves"
    assert not recovery_root.exists()

    with pytest.raises(FileNotFoundError):
        load_effect_system_autosave(project, "aura.vocal.space")

    assert not recovery_root.exists()


def test_recovery_discard_removes_only_draft(tmp_path):
    project = _project(tmp_path)
    save_effect_system(project, _system())
    save_effect_system_autosave(project, _system(version=2, db=4.0))

    discarded = discard_effect_system_autosave(project, "aura.vocal.space")
    assert discarded["discarded"] is True
    assert discarded["canonical_saved_system_mutated"] is False
    assert load_effect_system(project, "aura.vocal.space").version == 1
    with pytest.raises(FileNotFoundError):
        load_effect_system_autosave(project, "aura.vocal.space")


def test_recovery_identifier_is_project_confined(tmp_path):
    project = _project(tmp_path)
    bad = _system()
    object.__setattr__(bad, "id", "../escape")
    with pytest.raises(ValueError):
        save_effect_system_autosave(project, bad)


def test_recovery_api_is_strict_and_routes_are_existing_extension_contract():
    with pytest.raises(ValidationError):
        EffectSystemAutosaveRequest.model_validate(
            {
                "system": {
                    "id": "aura.vocal.space",
                    "name": "Aura Vocal Space",
                    "nodes": [{"id": "n", "catalogue_item_id": "music.fx.gain"}],
                },
                "client_entitlement": True,
            }
        )

    routes = {(path, method) for path, _handler, method in effect_system_extended_route_registrations("/command-center/api")}
    base = "/command-center/api/effect-systems/projects/{project_name}/autosave"
    assert (base, "POST") in routes
    assert (f"{base}/{{system_id}}", "GET") in routes
    assert (f"{base}/{{system_id}}", "DELETE") in routes
