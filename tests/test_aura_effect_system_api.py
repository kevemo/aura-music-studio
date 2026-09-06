from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import app as production_app
from aura_music_studio import aura_effect_system_api as effect_api
from aura_music_studio.aura_effect_system_api import (
    EffectSystemApplyRequest,
    EffectSystemDefinitionRequest,
    EffectSystemGraphRequest,
    EffectSystemNodeRequest,
    EffectSystemPromptRequest,
    apply_member_effect_system,
    compose_member_effect_system,
    get_member_effect_system,
    list_member_effect_systems,
    preview_member_effect_system,
    restore_member_effect_system_revision,
    save_member_effect_system,
)
from aura_music_studio.daw import load_session
from aura_music_studio.session import StudioSession


class StubEntitlements:
    def __init__(self, denied: set[str] | None = None):
        self.denied = denied or set()

    def has_entitlement(self, user_id: str, effect_id: str) -> dict:
        owned = effect_id not in self.denied
        core = effect_id in {"music.fx.gain", "music.fx.reverb", "music.fx.highpass", "music.fx.lowpass"}
        return {
            "effect_id": effect_id,
            "owned": owned,
            "included": core,
            "entitlement_band": "core" if core else "silver",
            "coin_price": 0 if core else 200,
            "source": "test" if owned else "",
        }


def _request(plan: str = "pro", user_id: str = "member-a"):
    return SimpleNamespace(
        state=SimpleNamespace(member=SimpleNamespace(plan=SimpleNamespace(id=plan), user_id=user_id))
    )


@pytest.fixture()
def member_project(tmp_path, monkeypatch):
    project = tmp_path / "song"
    project.mkdir()
    session = StudioSession(name="song")
    session.add_track("Master", "master")
    track = session.add_track("Lead Vocal", "vocals")
    session.save(project / "aura_session.json")

    def resolve(name: str, *, must_exist: bool = True):
        if name != "song":
            raise FileNotFoundError(name)
        if must_exist and not project.is_dir():
            raise FileNotFoundError(name)
        return project

    monkeypatch.setattr(effect_api, "project_path", resolve)
    monkeypatch.setattr(effect_api, "effect_entitlement_store", StubEntitlements())
    return project, track.id


def _definition(version: int = 1) -> EffectSystemDefinitionRequest:
    return EffectSystemDefinitionRequest(
        id="aura.vocal.air",
        name="Aura Vocal Air",
        description="Editable member effect chain",
        version=version,
        nodes=[
            EffectSystemNodeRequest(
                id="clean",
                catalogue_item_id="music.fx.gain",
                parameters={"db": 1.5},
            ),
            EffectSystemNodeRequest(
                id="space",
                catalogue_item_id="music.fx.reverb",
                parameters={"mix": 0.2},
            ),
        ],
    )


def _prompt_hash() -> str:
    return hashlib.sha256(b"High pass then add reverb").hexdigest()


def test_effect_system_routes_are_mounted_before_universal_catchall():
    paths = [getattr(route, "path", "") for route in production_app.router.routes]
    expected = [
        "/command-center/api/universal-library/effect-systems/compose",
        "/command-center/api/universal-library/effect-systems/projects/{project_name}",
        "/command-center/api/universal-library/effect-systems/projects/{project_name}/save",
        "/command-center/api/universal-library/effect-systems/projects/{project_name}/tracks/{track_id}/preview",
        "/command-center/api/universal-library/effect-systems/projects/{project_name}/tracks/{track_id}/apply",
        "/command-center/api/universal-library/effect-systems/projects/{project_name}/restore/{revision_id}",
        "/command-center/api/universal-library/effect-systems/projects/{project_name}/{system_id}",
    ]
    catchall = "/command-center/api/universal-library/{item_id:path}"
    assert catchall in paths
    catchall_index = paths.index(catchall)
    for path in expected:
        assert path in paths
        assert paths.index(path) < catchall_index


def test_prompt_compose_returns_editable_graph_and_authoritative_entitlement_state(monkeypatch):
    monkeypatch.setattr(effect_api, "effect_entitlement_store", StubEntitlements(denied={"music.fx.limiter"}))
    payload = compose_member_effect_system(
        EffectSystemPromptRequest(prompt="High pass at 120 Hz then add limiter"),
        _request(),
    )
    assert payload["editable_graph"] is True
    assert payload["project_mutated"] is False
    assert payload["prompt_fingerprint"]
    assert payload["can_apply"] is False
    assert payload["missing_entitlement_effect_ids"] == ["music.fx.limiter"]
    assert payload["coin_unit"] == "COSMIC_CREATION_COIN"


def test_save_list_and_load_round_trip_preserves_prompt_provenance(member_project):
    project, _track_id = member_project
    body = EffectSystemGraphRequest(system=_definition(), source_prompt_fingerprint=_prompt_hash())
    saved = save_member_effect_system("song", body, _request())
    assert saved["saved"] is True
    assert saved["source_prompt_fingerprint"] == _prompt_hash()
    assert saved["reuse_available"] is True

    listing = list_member_effect_systems("song", _request())
    assert listing["count"] == 1
    assert listing["items"][0]["source_prompt_fingerprint"] == _prompt_hash()

    loaded = get_member_effect_system("song", "aura.vocal.air", _request())
    assert loaded["system"]["id"] == "aura.vocal.air"
    assert loaded["source_prompt_fingerprint"] == _prompt_hash()
    assert loaded["fingerprint"] == saved["fingerprint"]
    assert (project / "aura_session.json").is_file()


def test_preview_is_non_mutating_and_issues_server_authoritative_one_time_proof(member_project):
    project, track_id = member_project
    before = (project / "aura_session.json").read_bytes()
    body = EffectSystemGraphRequest(system=_definition(), source_prompt_fingerprint=_prompt_hash())
    payload = preview_member_effect_system("song", track_id, body, _request())
    assert payload["can_apply"] is True
    assert len(payload["preview_token"]) == 64
    assert payload["preview_token"] != payload["fingerprint"]
    assert payload["preview_token_one_time"] is True
    assert payload["preview_token_server_authoritative"] is True
    assert payload["preview_evidence_persisted"] is True
    assert payload["preview_token_expires_in_seconds"] > 0
    assert payload["apply_requires_matching_preview_token"] is True
    assert payload["project_mutated"] is False
    assert payload["source_media_mutated"] is False
    assert payload["source_prompt_fingerprint"] == _prompt_hash()
    assert (project / "aura_session.json").read_bytes() == before


def test_apply_rejects_changed_graph_preview_proof_before_revision_or_mutation(member_project):
    project, track_id = member_project
    before = (project / "aura_session.json").read_bytes()
    preview_body = EffectSystemGraphRequest(system=_definition(), source_prompt_fingerprint=_prompt_hash())
    preview = preview_member_effect_system("song", track_id, preview_body, _request())
    changed = _definition(version=2)
    body = EffectSystemApplyRequest(
        system=changed,
        source_prompt_fingerprint=_prompt_hash(),
        expected_fingerprint=preview["preview_token"],
    )
    with pytest.raises(HTTPException) as exc:
        apply_member_effect_system("song", track_id, body, _request())
    assert exc.value.status_code in {403, 409}
    assert "preview" in str(exc.value.detail).casefold() or "graph changed" in str(exc.value.detail).casefold()
    assert (project / "aura_session.json").read_bytes() == before
    revision_root = project / "work" / "revisions"
    assert not revision_root.exists() or not any(revision_root.iterdir())


def test_apply_rechecks_entitlements_after_preview(member_project, monkeypatch):
    project, track_id = member_project
    allowed = StubEntitlements()
    monkeypatch.setattr(effect_api, "effect_entitlement_store", allowed)
    graph = EffectSystemGraphRequest(system=_definition(), source_prompt_fingerprint=_prompt_hash())
    preview = preview_member_effect_system("song", track_id, graph, _request())
    assert preview["can_apply"] is True

    monkeypatch.setattr(effect_api, "effect_entitlement_store", StubEntitlements(denied={"music.fx.reverb"}))
    apply_body = EffectSystemApplyRequest(
        system=_definition(),
        source_prompt_fingerprint=_prompt_hash(),
        expected_fingerprint=preview["preview_token"],
    )
    with pytest.raises(HTTPException) as exc:
        apply_member_effect_system("song", track_id, apply_body, _request())
    assert exc.value.status_code == 403
    assert len(load_session(project).find_track(track_id).effects) == 0
    revision_root = project / "work" / "revisions"
    assert not revision_root.exists() or not any(revision_root.iterdir())


def test_apply_then_restore_uses_revision_and_preserves_prompt_provenance(member_project):
    project, track_id = member_project
    graph = EffectSystemGraphRequest(system=_definition(), source_prompt_fingerprint=_prompt_hash())
    preview = preview_member_effect_system("song", track_id, graph, _request())
    applied = apply_member_effect_system(
        "song",
        track_id,
        EffectSystemApplyRequest(
            system=_definition(),
            source_prompt_fingerprint=_prompt_hash(),
            expected_fingerprint=preview["preview_token"],
        ),
        _request(),
    )
    assert applied["applied"] is True
    assert applied["preview_token_verified"] is True
    assert applied["entitlements_rechecked_at_apply"] is True
    assert applied["revision_id"]
    assert applied["source_prompt_fingerprint"] == _prompt_hash()

    session = load_session(project)
    track = session.find_track(track_id)
    assert len(track.effects) == 2
    assert track.metadata["effect_systems"][0]["source_prompt_fingerprint"] == _prompt_hash()

    restored = restore_member_effect_system_revision("song", applied["revision_id"], _request())
    assert restored["effect_system_undo"] is True
    assert len(load_session(project).find_track(track_id).effects) == 0


def test_invalid_prompt_fingerprint_fails_closed(member_project):
    _project, track_id = member_project
    body = EffectSystemGraphRequest(system=_definition(), source_prompt_fingerprint="not-a-sha")
    with pytest.raises(HTTPException) as exc:
        preview_member_effect_system("song", track_id, body, _request())
    assert exc.value.status_code == 400
    assert "SHA-256" in str(exc.value.detail)


def test_missing_membership_context_is_denied(member_project):
    _project, _track_id = member_project
    with pytest.raises(HTTPException) as exc:
        list_member_effect_systems("song", SimpleNamespace(state=SimpleNamespace()))
    assert exc.value.status_code == 401
