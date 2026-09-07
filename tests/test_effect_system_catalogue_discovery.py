from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import app as production_app
from aura_music_studio.aura_effect_system_api import list_member_effect_catalogue


def _request(plan: str = "pro", user_id: str = "member-a"):
    return SimpleNamespace(
        state=SimpleNamespace(member=SimpleNamespace(plan=SimpleNamespace(id=plan), user_id=user_id))
    )


def test_catalogue_discovery_route_is_mounted_before_universal_catchall():
    paths = [getattr(route, "path", "") for route in production_app.router.routes]
    catalogue = "/command-center/api/universal-library/effect-systems/catalogue"
    catchall = "/command-center/api/universal-library/{item_id:path}"
    assert catalogue in paths
    assert catchall in paths
    assert paths.index(catalogue) < paths.index(catchall)


def test_catalogue_discovery_reuses_canonical_public_music_catalogue():
    payload = list_member_effect_catalogue(_request(), query="reverb", studio="music", limit=20)
    assert payload["count"] >= 1
    assert payload["count"] <= 20
    assert payload["total_matches"] >= payload["count"]
    assert payload["query"] == "reverb"
    assert payload["studio"] == "music"
    assert payload["plan"] == "pro"
    assert payload["public_metadata_only"] is True
    assert payload["project_mutated"] is False
    assert payload["entitlement_granted"] is False
    assert payload["execution_authorized"] is False
    assert all(item["studio"] == "music" for item in payload["items"])
    assert any(item["id"] == "music.fx.reverb" for item in payload["items"])


def test_catalogue_discovery_limit_is_server_bounded():
    with pytest.raises(HTTPException) as low:
        list_member_effect_catalogue(_request(), limit=0)
    assert low.value.status_code == 400
    with pytest.raises(HTTPException) as high:
        list_member_effect_catalogue(_request(), limit=101)
    assert high.value.status_code == 400


def test_catalogue_discovery_rejects_unbounded_or_malformed_filters():
    with pytest.raises(HTTPException) as query_error:
        list_member_effect_catalogue(_request(), query="x" * 161)
    assert query_error.value.status_code == 400
    with pytest.raises(HTTPException) as studio_error:
        list_member_effect_catalogue(_request(), studio="music/../../owner")
    assert studio_error.value.status_code == 400


def test_catalogue_discovery_requires_active_member_context():
    with pytest.raises(HTTPException) as missing_member:
        list_member_effect_catalogue(SimpleNamespace(state=SimpleNamespace()))
    assert missing_member.value.status_code == 401

    no_user = SimpleNamespace(
        state=SimpleNamespace(member=SimpleNamespace(plan=SimpleNamespace(id="pro"), user_id=""))
    )
    with pytest.raises(HTTPException) as missing_user:
        list_member_effect_catalogue(no_user)
    assert missing_user.value.status_code == 401


def test_catalogue_discovery_returns_canonical_metadata_without_granting_authority():
    payload = list_member_effect_catalogue(_request(), query="compressor", studio="music", limit=10)
    assert payload["items"]
    for item in payload["items"]:
        assert "parameters" in item
        assert "entitlement" in item
        assert "runtime" in item
        assert "status" in item
        assert "rights_status" in item
        assert "runtime_requirements" in item
        assert "provider_compatibility" in item
        assert "model_compatibility" in item
        assert "backend_executable" not in item
        assert "execution_authorized" not in item
    assert payload["entitlement_granted"] is False
    assert payload["execution_authorized"] is False
