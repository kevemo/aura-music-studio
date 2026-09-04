from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import aura_music_studio.aura_effect_system_api as api
from aura_music_studio.aura_effect_system_api import EffectNodeInput, EffectSystemInput, PromptComposeRequest


class _Entitlements:
    def __init__(self, owned: bool = True):
        self.owned = owned

    def has_entitlement(self, user_id: str, effect_id: str):
        return {
            "user_id": user_id,
            "effect_id": effect_id,
            "owned": self.owned or effect_id.endswith("gain"),
            "source": "test",
        }


def _request(*, user_id: str = "member-1", plan_id: str = "pro"):
    member = SimpleNamespace(user_id=user_id, plan=SimpleNamespace(id=plan_id))
    return SimpleNamespace(state=SimpleNamespace(member=member))


def _system(*, system_id: str = "aura.test.chain", version: int = 1):
    return EffectSystemInput(
        id=system_id,
        name="Editable Test Chain",
        version=version,
        nodes=[
            EffectNodeInput(id="fx01", catalogue_item_id="music.fx.highpass", parameters={"hz": 120}),
            EffectNodeInput(id="fx02", catalogue_item_id="music.fx.reverb", parameters={"mix": 0.3}),
        ],
    )


def test_effect_system_creator_routes_mount_on_canonical_and_production_apps():
    import aura_music_studio.universal_creative_catalogue_api  # noqa: F401
    from aura_music_studio.api import app as canonical_app

    canonical_paths = {getattr(route, "path", None) for route in canonical_app.routes}
    assert "/command-center/api/effect-systems/capabilities" in canonical_paths
    assert "/command-center/api/effect-systems/compose" in canonical_paths
    assert "/command-center/api/effect-systems/compile" in canonical_paths
    assert "/command-center/api/effect-systems/projects/{project_name}/preview/{track_id}" in canonical_paths
    assert "/command-center/api/effect-systems/projects/{project_name}/{system_id}/apply/{track_id}" in canonical_paths

    production = importlib.import_module("app").app
    production_paths = {getattr(route, "path", None) for route in production.routes}
    assert "/command-center/api/effect-systems/capabilities" in production_paths
    assert "/command-center/api/effect-systems/compose" in production_paths


def test_capabilities_are_authenticated_and_truthful():
    with pytest.raises(HTTPException) as exc:
        api.effect_system_capabilities(SimpleNamespace(state=SimpleNamespace(member=None)))
    assert exc.value.status_code == 401

    payload = api.effect_system_capabilities(_request())
    assert payload["plan"] == "pro"
    assert payload["prompt_to_executable_graph"] is True
    assert payload["editable_typed_nodes"] is True
    assert payload["versioned_project_save"] is True
    assert payload["entitlement_checked_apply"] is True
    assert payload["revision_backed_undo"] is True
    assert payload["visual_node_editor"] is False
    assert payload["marketplace_publish"] is False
    assert payload["arbitrary_command_execution"] is False


def test_prompt_composition_returns_editable_non_mutating_graph(monkeypatch):
    monkeypatch.setattr(api, "effect_entitlement_store", _Entitlements(owned=True))
    payload = api.compose_effect_system(
        PromptComposeRequest(
            prompt="High pass 120 hz, add reverb 30%, then widen the stereo width 65%",
            system_id="aura.test.prompt_chain",
            name="Prompt Chain",
        ),
        _request(),
    )
    assert payload["system"]["id"] == "aura.test.prompt_chain"
    assert payload["editable_system"] == payload["system"]
    assert [node["catalogue_item_id"] for node in payload["system"]["nodes"]] == [
        "music.fx.highpass",
        "music.fx.reverb",
        "music.fx.stereo_width",
    ]
    assert payload["backend_executable"] is True
    assert payload["compile_is_non_mutating"] is True
    assert payload["project_mutated"] is False
    assert payload["can_apply"] is True
    assert payload["preview_required_before_apply"] is True
    assert payload["arbitrary_command_execution"] is False
    assert payload["ffmpeg_filter_chain"]


def test_compile_reports_missing_entitlement_without_granting_access(monkeypatch):
    monkeypatch.setattr(api, "effect_entitlement_store", _Entitlements(owned=False))
    payload = api.compile_editable_effect_system(_system(), _request())
    assert payload["backend_executable"] is True
    assert payload["can_apply"] is False
    assert "music.fx.highpass" in payload["missing_entitlement_effect_ids"]
    assert "music.fx.reverb" in payload["missing_entitlement_effect_ids"]
    assert payload["project_mutated"] is False


def test_unsupported_prompt_and_unknown_effect_fail_closed(monkeypatch):
    monkeypatch.setattr(api, "effect_entitlement_store", _Entitlements())
    with pytest.raises(HTTPException) as exc:
        api.compose_effect_system(PromptComposeRequest(prompt="Create an arbitrary shell script"), _request())
    assert exc.value.status_code == 400

    bad = EffectSystemInput(
        id="aura.test.bad",
        name="Bad",
        nodes=[EffectNodeInput(id="fx01", catalogue_item_id="music.fx.not_real")],
    )
    with pytest.raises(HTTPException) as exc:
        api.compile_editable_effect_system(bad, _request())
    assert exc.value.status_code == 400


def test_save_api_rejects_path_body_identity_mismatch(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_project", lambda _name: tmp_path)
    body = _system(system_id="aura.test.saved")
    with pytest.raises(HTTPException) as exc:
        api.save_editable_effect_system("song", "aura.test.other", body, _request())
    assert exc.value.status_code == 409


def test_save_api_preserves_version_contract_and_never_grants_entitlement(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_project", lambda _name: tmp_path)
    first = api.save_editable_effect_system("song", "aura.test.saved", _system(system_id="aura.test.saved"), _request())
    assert first["saved"] is True
    assert first["save_does_not_grant_entitlement"] is True
    assert first["source_media_mutated"] is False

    changed_same_version = EffectSystemInput(
        id="aura.test.saved",
        name="Editable Test Chain",
        version=1,
        nodes=[EffectNodeInput(id="fx01", catalogue_item_id="music.fx.gain", parameters={"db": 1.5})],
    )
    with pytest.raises(HTTPException) as exc:
        api.save_editable_effect_system("song", "aura.test.saved", changed_same_version, _request())
    assert exc.value.status_code == 409

    second = api.save_editable_effect_system(
        "song",
        "aura.test.saved",
        changed_same_version.model_copy(update={"version": 2}),
        _request(),
    )
    assert second["saved"] is True
    assert second["editable_system"]["version"] == 2
