from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import app as production_app
from aura_music_studio import universal_creative_catalogue_api as catalogue_api
from aura_music_studio.credit_wallet import CreditWalletStore
from aura_music_studio.creative_effect_entitlements import CreativeEffectEntitlementStore
from aura_music_studio.studio_catalogue_menus import EFFECT_BANDS
from aura_music_studio.universal_creative_catalogue_api import (
    EffectPurchaseRequest,
    RuntimePreviewRequest,
    universal_runtime_effect_entitlement,
    universal_runtime_effect_item,
    universal_runtime_effect_preview_plan,
    universal_runtime_effect_purchase,
    universal_runtime_effects,
    universal_studio_menus,
)


def _request(plan: str = "free", user_id: str = "member-a"):
    return SimpleNamespace(state=SimpleNamespace(member=SimpleNamespace(plan=SimpleNamespace(id=plan), user_id=user_id)))


@pytest.fixture()
def coin_store(tmp_path, monkeypatch):
    db = tmp_path / "catalogue-api.sqlite3"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
        con.executemany("INSERT INTO users(id) VALUES (?)", [("member-a",), ("member-b",)])
    wallet = CreditWalletStore(db)
    effects = CreativeEffectEntitlementStore(db)
    monkeypatch.setattr(catalogue_api, "effect_entitlement_store", effects)
    return wallet, effects


def test_universal_routes_are_reachable_on_production_app_and_specific_before_catchall():
    paths = [getattr(route, "path", "") for route in production_app.router.routes]
    expected = [
        "/command-center/api/universal-library/menus",
        "/command-center/api/universal-library/runtime-effects",
        "/command-center/api/universal-library/runtime-effects/owned",
        "/command-center/api/universal-library/runtime-effects/{item_id:path}/purchase",
        "/command-center/api/universal-library/admin/runtime-effects/{item_id:path}/refund",
    ]
    for path in expected:
        assert path in paths
    assert "/command-center/api/universal-library" in paths
    assert "/command-center/api/universal-library/{item_id:path}" in paths
    catchall_index = paths.index("/command-center/api/universal-library/{item_id:path}")
    for path in expected:
        assert paths.index(path) < catchall_index


def test_studio_menu_api_returns_canonical_effect_bands():
    payload = universal_studio_menus(_request(), domain="music")
    assert payload["plan"] == "free"
    assert payload["domains"] == ["music"]
    assert payload["coin_unit"] == "COSMIC_CREATION_COIN"
    assert [(row["id"], row["coin_price"]) for row in payload["effect_bands"]] == [("core", 0), ("silver", 200), ("gold", 500)]


def test_runtime_effect_api_has_truthful_owned_state_before_purchase(coin_store):
    payload = universal_runtime_effects(_request("base"), q="limiter", studio="music", entitlement="silver")
    assert payload["count"] == 1
    assert payload["backend_executable_count"] == 1
    assert payload["owned_state_included"] is True
    row = payload["items"][0]
    assert row["id"] == "music.fx.limiter"
    assert row["backend_executable"] is True
    assert row["ccc_price"] == 200
    assert row["owned"] is False


def test_purchase_api_debits_coins_and_owned_state_changes(coin_store):
    wallet, _ = coin_store
    wallet.grant("member-a", 500, reason="test", actor="test", reference="api-grant")
    bought = universal_runtime_effect_purchase("music.fx.limiter", EffectPurchaseRequest(idempotency_key="checkout-1"), _request())
    assert bought["purchased"] is True
    assert bought["coin_price"] == 200
    assert bought["balance_after"] == 300
    assert bought["subscription_changed"] is False
    entitlement = universal_runtime_effect_entitlement("music.fx.limiter", _request())
    assert entitlement["entitlement"]["owned"] is True
    listing = universal_runtime_effects(_request(), q="limiter", studio="music", entitlement="silver")
    assert listing["items"][0]["owned"] is True


def test_purchase_api_returns_payment_required_when_coin_balance_is_too_low(coin_store):
    with pytest.raises(HTTPException) as exc:
        universal_runtime_effect_purchase("music.fx.limiter", EffectPurchaseRequest(idempotency_key="checkout-poor"), _request())
    assert exc.value.status_code == 402


def test_runtime_item_has_authoritative_gold_price_and_owned_state(coin_store):
    payload = universal_runtime_effect_item("music.fx.stereo_width", _request("pro"))
    assert payload["item"]["entitlement"] == "gold"
    assert payload["item"]["ccc_price"] == 500
    assert payload["item"]["owned"] is False
    assert {band.id: band.coin_price for band in EFFECT_BANDS}["gold"] == 500


def test_core_runtime_effect_is_owned_without_purchase(coin_store):
    payload = universal_runtime_effect_item("music.fx.gain", _request())
    assert payload["item"]["entitlement"] == "core"
    assert payload["item"]["owned"] is True
    assert payload["entitlement"]["included"] is True


def test_preview_plan_compiles_real_renderer_chain_and_bounds_parameters(coin_store):
    payload = universal_runtime_effect_preview_plan("music.fx.highpass", RuntimePreviewRequest(parameters={"hz": 999999}), _request())
    assert payload["backend_executable"] is True
    assert payload["project_media_mutated"] is False
    assert payload["renderer_effect"]["parameters"]["hz"] == 1000.0
    assert payload["filter_chain"]
    assert payload["preview_does_not_grant_apply_access"] is True


def test_preview_plan_rejects_unknown_parameters(coin_store):
    with pytest.raises(HTTPException) as exc:
        universal_runtime_effect_preview_plan("music.fx.gain", RuntimePreviewRequest(parameters={"made_up": 10}), _request())
    assert exc.value.status_code == 400


def test_invalid_studio_and_entitlement_fail_closed(coin_store):
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


@pytest.mark.parametrize(
    "member",
    [SimpleNamespace(), SimpleNamespace(plan=None), SimpleNamespace(plan=SimpleNamespace()), SimpleNamespace(plan=SimpleNamespace(id="")), SimpleNamespace(plan=SimpleNamespace(id=123))],
)
def test_malformed_membership_plan_context_is_denied(member):
    request = SimpleNamespace(state=SimpleNamespace(member=member))
    with pytest.raises(HTTPException) as exc:
        universal_studio_menus(request, domain="music")
    assert exc.value.status_code == 401


def test_missing_member_user_id_is_denied_before_entitlement_lookup(coin_store):
    request = SimpleNamespace(state=SimpleNamespace(member=SimpleNamespace(plan=SimpleNamespace(id="free"))))
    with pytest.raises(HTTPException) as exc:
        universal_runtime_effects(request)
    assert exc.value.status_code == 401
