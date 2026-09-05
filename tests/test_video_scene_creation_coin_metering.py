from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from aura_music_studio.plans import get_plan
from aura_music_studio import video_scene_render as subject


def _member(plan_id: str, user_id: str = "member-1"):
    return SimpleNamespace(user_id=user_id, plan=get_plan(plan_id))


def test_basic_and_pro_scene_renders_do_not_enter_free_coin_meter(monkeypatch):
    monkeypatch.setattr(
        subject,
        "free_video_render_quote",
        lambda _user_id: (_ for _ in ()).throw(AssertionError("paid tiers must not query Free render pricing")),
    )
    assert subject._free_scene_video_charge(_member("base"), project_name="p", directive_id="d") is None
    assert subject._free_scene_video_charge(_member("pro"), project_name="p", directive_id="d") is None


def test_free_scene_render_fails_closed_when_coin_price_is_not_configured(monkeypatch):
    monkeypatch.setattr(
        subject,
        "free_video_render_quote",
        lambda _user_id: {
            "enabled": False,
            "cost": None,
            "balance": 100,
            "affordable": False,
            "unit": "CREATION_COIN",
            "membership_effect": "none",
            "esp_role_effect": "none",
        },
    )

    with pytest.raises(HTTPException) as exc:
        subject._free_scene_video_charge(_member("free"), project_name="p", directive_id="d")

    assert exc.value.status_code == 403


def test_free_scene_render_rejects_insufficient_balance_before_charge(monkeypatch):
    monkeypatch.setattr(
        subject,
        "free_video_render_quote",
        lambda _user_id: {
            "enabled": True,
            "cost": 50,
            "balance": 20,
            "affordable": False,
            "unit": "CREATION_COIN",
            "membership_effect": "none",
            "esp_role_effect": "none",
        },
    )
    monkeypatch.setattr(
        subject,
        "charge_free_video_render",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("charge must not run")),
    )

    with pytest.raises(HTTPException) as exc:
        subject._free_scene_video_charge(_member("free"), project_name="p", directive_id="d")

    assert exc.value.status_code == 402


def test_free_scene_render_uses_server_authoritative_coin_charge(monkeypatch):
    transaction = {"id": "tx-1", "balance_after": 75}
    charge = subject.CreationCoinCharge(cost=25, transaction=transaction, refund_reference="refund-1")
    monkeypatch.setattr(
        subject,
        "free_video_render_quote",
        lambda _user_id: {
            "enabled": True,
            "cost": 25,
            "balance": 100,
            "affordable": True,
            "unit": "CREATION_COIN",
            "membership_effect": "none",
            "esp_role_effect": "none",
        },
    )
    seen = {}

    def fake_charge(user_id, *, project_id, directive_id):
        seen.update(user_id=user_id, project_id=project_id, directive_id=directive_id)
        return charge

    monkeypatch.setattr(subject, "charge_free_video_render", fake_charge)

    result = subject._free_scene_video_charge(
        _member("free"), project_name="project-a", directive_id="directive-a"
    )

    assert result is charge
    assert seen == {
        "user_id": "member-1",
        "project_id": "project-a",
        "directive_id": "directive-a",
    }


def test_free_scene_coin_state_reports_charge_without_changing_membership(monkeypatch):
    charge = subject.CreationCoinCharge(
        cost=25,
        transaction={"id": "tx-1", "balance_after": 75},
        refund_reference="refund-1",
    )
    monkeypatch.setattr(
        subject,
        "free_video_render_quote",
        lambda _user_id: {
            "enabled": True,
            "cost": 25,
            "balance": 75,
            "affordable": True,
            "unit": "CREATION_COIN",
            "membership_effect": "none",
            "esp_role_effect": "none",
        },
    )

    state = subject._scene_coin_state(_member("free"), charge)

    assert state["required"] is True
    assert state["charged"] is True
    assert state["charged_amount"] == 25
    assert state["charge_transaction_id"] == "tx-1"
    assert state["membership_effect"] == "none"
    assert state["esp_role_effect"] == "none"


def test_paid_scene_coin_state_is_explicitly_not_required():
    state = subject._scene_coin_state(_member("pro"), None)
    assert state["required"] is False
    assert state["charged"] is False
    assert state["reason"] == "included_subscription_behavior"
