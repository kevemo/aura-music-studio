from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from aura_music_studio import commercial_entitlement_routes as routes
from aura_music_studio import creation_coin_metering as metering
from aura_music_studio.creation_coin_metering import CreationCoinCharge
from aura_music_studio.creative_project_api import QueueRendererRequest
from aura_music_studio.plans import get_plan


def _request(plan_id: str = "free", user_id: str = "member-1"):
    member = SimpleNamespace(user_id=user_id, plan=get_plan(plan_id))
    return SimpleNamespace(state=SimpleNamespace(member=member))


def _video_snapshot(*_args, **_kwargs):
    return {"kind": "video", "status": "pending", "prompt_id": None}


@pytest.fixture(autouse=True)
def _isolated_render_attempt_store(tmp_path, monkeypatch):
    monkeypatch.setenv("LSS_DB_PATH", str(tmp_path / "state.sqlite3"))


def test_creation_coin_costs_accept_free_video_owner_price(monkeypatch):
    monkeypatch.setenv(
        "LSS_CREATION_COIN_COSTS_JSON",
        '{"image_poster_overage":25,"free_video_render":120}',
    )
    assert metering.creation_coin_costs() == {
        "image_poster_overage": 25,
        "free_video_render": 120,
    }


def test_creation_coin_costs_reject_boolean_video_price(monkeypatch):
    monkeypatch.setenv("LSS_CREATION_COIN_COSTS_JSON", '{"free_video_render":true}')
    with pytest.raises(ValueError, match="positive integer"):
        metering.creation_coin_costs()


def test_free_video_without_config_fails_before_renderer(monkeypatch):
    called = {"renderer": False}
    monkeypatch.setattr(routes, "_directive_render_snapshot", _video_snapshot)
    monkeypatch.setattr(
        routes,
        "free_video_render_quote",
        lambda _user_id: {
            "enabled": False,
            "cost": None,
            "balance": 500,
            "affordable": False,
            "unit": "CREATION_COIN",
            "membership_effect": "none",
            "esp_role_effect": "none",
        },
    )

    def fake_renderer(*_args, **_kwargs):
        called["renderer"] = True
        return {}

    monkeypatch.setattr(routes, "base_queue_creative_render", fake_renderer)
    with pytest.raises(HTTPException) as exc:
        routes.render_with_commercial_entitlements(
            "project-a", "directive-a", QueueRendererRequest(), _request("free")
        )
    assert exc.value.status_code == 403
    assert called["renderer"] is False


def test_free_video_insufficient_coins_fails_before_renderer(monkeypatch):
    called = {"renderer": False}
    monkeypatch.setattr(routes, "_directive_render_snapshot", _video_snapshot)
    monkeypatch.setattr(
        routes,
        "free_video_render_quote",
        lambda _user_id: {
            "enabled": True,
            "cost": 120,
            "balance": 10,
            "affordable": False,
            "unit": "CREATION_COIN",
            "membership_effect": "none",
            "esp_role_effect": "none",
        },
    )

    def fake_renderer(*_args, **_kwargs):
        called["renderer"] = True
        return {}

    monkeypatch.setattr(routes, "base_queue_creative_render", fake_renderer)
    with pytest.raises(HTTPException) as exc:
        routes.render_with_commercial_entitlements(
            "project-a", "directive-a", QueueRendererRequest(), _request("free")
        )
    assert exc.value.status_code == 402
    assert called["renderer"] is False


def test_basic_video_keeps_existing_subscription_behavior(monkeypatch):
    monkeypatch.setattr(routes, "_directive_render_snapshot", _video_snapshot)

    def quote_must_not_run(_user_id):
        raise AssertionError("Basic video must not enter Free-tier coin metering")

    monkeypatch.setattr(routes, "free_video_render_quote", quote_must_not_run)
    monkeypatch.setattr(
        routes,
        "base_queue_creative_render",
        lambda *_args, **_kwargs: {"submission": {"prompt_id": "p1"}},
    )
    response = routes.render_with_commercial_entitlements(
        "project-a", "directive-a", QueueRendererRequest(), _request("base")
    )
    state = response["commercial_entitlements"]["video_generation"]["free_tier_creation_coin_purchase"]
    assert state["required"] is False
    assert state["reason"] == "included_subscription_behavior"
    assert response["render_attempt"]["state"] == "queued"


def test_free_video_charge_is_reported_after_renderer_accepts(monkeypatch):
    charge = CreationCoinCharge(
        cost=120,
        transaction={"id": "tx-1", "balance_after": 380},
        refund_reference="refund-1",
    )
    monkeypatch.setattr(routes, "_directive_render_snapshot", _video_snapshot)
    monkeypatch.setattr(
        routes,
        "free_video_render_quote",
        lambda _user_id: {
            "enabled": True,
            "cost": 120,
            "balance": 380,
            "affordable": True,
            "unit": "CREATION_COIN",
            "membership_effect": "none",
            "esp_role_effect": "none",
        },
    )
    monkeypatch.setattr(routes, "charge_free_video_render", lambda *_args, **_kwargs: charge)
    monkeypatch.setattr(
        routes,
        "base_queue_creative_render",
        lambda *_args, **_kwargs: {"submission": {"prompt_id": "p1"}},
    )
    response = routes.render_with_commercial_entitlements(
        "project-a", "directive-a", QueueRendererRequest(), _request("free")
    )
    state = response["commercial_entitlements"]["video_generation"]["free_tier_creation_coin_purchase"]
    assert state["charged"] is True
    assert state["charged_amount"] == 120
    assert state["charge_transaction_id"] == "tx-1"
    assert state["subscription_effect"] == "none"
    assert state["esp_role_effect"] == "none"
    assert response["render_attempt"]["state"] == "queued"


def test_free_video_renderer_failure_refunds_prepaid_charge(monkeypatch):
    charge = CreationCoinCharge(
        cost=120,
        transaction={"id": "tx-1", "balance_after": 380},
        refund_reference="refund-1",
    )
    refunded = {"called": False}
    monkeypatch.setattr(routes, "_directive_render_snapshot", _video_snapshot)
    monkeypatch.setattr(
        routes,
        "free_video_render_quote",
        lambda _user_id: {
            "enabled": True,
            "cost": 120,
            "balance": 500,
            "affordable": True,
            "unit": "CREATION_COIN",
            "membership_effect": "none",
            "esp_role_effect": "none",
        },
    )
    monkeypatch.setattr(routes, "charge_free_video_render", lambda *_args, **_kwargs: charge)

    def renderer_failure(*_args, **_kwargs):
        raise HTTPException(502, "renderer unavailable")

    def refund(_user_id, actual_charge, *, reason):
        assert actual_charge is charge
        assert "video renderer" in reason
        refunded["called"] = True
        return {"id": "refund-tx"}

    monkeypatch.setattr(routes, "base_queue_creative_render", renderer_failure)
    monkeypatch.setattr(routes, "refund_free_video_render", refund)
    with pytest.raises(HTTPException) as exc:
        routes.render_with_commercial_entitlements(
            "project-a", "directive-a", QueueRendererRequest(), _request("free")
        )
    assert exc.value.status_code == 502
    assert refunded["called"] is True
