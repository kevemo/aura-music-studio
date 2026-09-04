from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import app as production_app
from aura_music_studio.studio_catalogue_menus import EFFECT_BANDS
from aura_music_studio.universal_creative_catalogue_api import (
    RuntimePreviewRequest,
    universal_runtime_effect_item,
    universal_runtime_effect_preview_plan,
    universal_runtime_effects,
    universal_studio_menus,
)


def _request(plan: str = "free"):
    return SimpleNamespace(state=SimpleNamespace(member=SimpleNamespace(plan=SimpleNamespace(id=plan))))


def test_universal_routes_are_reachable_on_production_app_and_specific_before_catchall():
    paths = [getattr(route, "path", "") for route in production_app.router.routes]
    assert "/command-center/api/universal-library/menus" in paths
    assert "/command-center/api/universal-library/runtime-effects" in paths
    assert "/command-center/api/universal-library" in paths
    assert "/command-center/api/universal-library/{item_id:path}" in paths
    assert paths.index("/command-center/api/universal-library/menus") < paths.index(
        "/command-center/api/universal-library/{item_id:path}"
    )


def test_studio_menu_api_returns_canonical_effect_bands():
    payload = universal_studio_menus(_request(), domain="music")
    assert payload["plan"] == "free"
    assert payload["domains"] == ["music"]
    assert [(row["id"], row["coin_price"]) for row in payload["effect_bands"]] == [
        ("core", 0),
        ("silver", 200),
        ("gold", 500),
    ]


def test_runtime_effect_api_exposes_only_real_catalogue_rows_with_truthful_state():
    payload = universal_runtime_effects(_request("base"), q="limiter", studio="music", entitlement="silver")
    assert payload["count"] == 1
    assert payload["backend_executable_count"] == 1
    row = payload["items"][0]
    assert row["id"] == "music.fx.limiter"
    assert row["status"] == "BACKEND_FUNCTIONAL"
    assert row["backend_executable"] is True
    assert row["ccc_price"] == 200
    assert payload["owned_state_included"] is False


def test_runtime_item_has_authoritative_band_price():
    payload = universal_runtime_effect_item("music.fx.stereo_width", _request("pro"))
    assert payload["item"]["entitlement"] == "gold"
    assert payload["item"]["ccc_price"] == 500
    assert {band.id: band.coin_price for band in EFFECT_BANDS}["gold"] == 500


def test_preview_plan_compiles_real_renderer_chain_and_bounds_parameters():
    payload = universal_runtime_effect_preview_plan(
        "music.fx.highpass",
        RuntimePreviewRequest(parameters={"hz": 999999}),
        _request(),
    )
    assert payload["backend_executable"] is True
    assert payload["project_media_mutated"] is False
    assert payload["renderer_effect"]["parameters"]["hz"] == 1000.0
    assert payload["filter_chain"]
    assert "highpass" in payload["filter_chain"] or "highpass" in payload["renderer_effect"]["type"]


def test_preview_plan_rejects_unknown_parameters():
    with pytest.raises(HTTPException) as exc:
        universal_runtime_effect_preview_plan(
            "music.fx.gain",
            RuntimePreviewRequest(parameters={"made_up": 10}),
            _request(),
        )
    assert exc.value.status_code == 400


def test_invalid_studio_and_entitlement_fail_closed():
    with pytest.raises(HTTPException) as studio_exc:
        universal_runtime_effects(_request(), studio="mystery")
    assert studio_exc.value.status_code == 400
    with pytest.raises(HTTPException) as band_exc:
        universal_runtime_effects(_request(), entitlement="platinum")
    assert band_exc.value.status_code == 400


def test_missing_membership_context_is_denied():
    with pytest.raises(HTTPException) as exc:
        universal_studio_menus(SimpleNamespace(state=SimpleNamespace()), domain="music")
    assert exc.value.status_code == 401
