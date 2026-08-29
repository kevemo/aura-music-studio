from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from aura_music_studio.accounts import AccountStore
from aura_music_studio.creation_coin_metering import (
    charge_image_poster_overage,
    creation_coin_costs,
    image_poster_overage_quote,
    refund_image_poster_overage,
)
from aura_music_studio.credit_wallet import CreditWalletStore
from aura_music_studio.creative_project_api import QueueRendererRequest
from aura_music_studio.plans import get_plan
import aura_music_studio.commercial_entitlement_routes as routes


def active_member(tmp_path: Path, monkeypatch, plan_id: str = "free"):
    db_path = tmp_path / "coins.sqlite3"
    monkeypatch.setenv("LSS_DB_PATH", str(db_path))
    store = AccountStore(db_path)
    signup = store.signup(
        f"{plan_id}@coins.example",
        "Coin Tester",
        "creation-coin-test-password",
        plan_id,
    )
    user = store.decide_membership(signup.approval_token, "approve", "Test Owner")
    if plan_id != "free":
        user = store.activate_paid_plan(user["id"], plan_id, "test-payment")
    return SimpleNamespace(user_id=user["id"], plan=get_plan(user["plan_id"])), store


def request_for(member):
    return SimpleNamespace(state=SimpleNamespace(member=member))


def _image_snapshot(*_args, **_kwargs):
    return {"kind": "image", "status": "pending", "prompt_id": None}


def test_coin_cost_is_never_invented_when_owner_has_not_configured_it(tmp_path, monkeypatch):
    member, _ = active_member(tmp_path, monkeypatch)
    monkeypatch.delenv("LSS_CREATION_COIN_COSTS_JSON", raising=False)
    assert creation_coin_costs() == {}
    assert image_poster_overage_quote(member.user_id) == {
        "enabled": False,
        "cost": None,
        "balance": 0,
        "affordable": False,
        "unit": "CREATION_COIN",
        "membership_effect": "none",
        "esp_role_effect": "none",
    }


@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        "[]",
        '{"image_poster_overage": 0}',
        '{"image_poster_overage": -1}',
        '{"image_poster_overage": true}',
        '{"image_poster_overage": 1.5}',
        '{"image_poster_overage": 1000001}',
    ],
)
def test_invalid_coin_cost_configuration_fails_closed(monkeypatch, raw):
    monkeypatch.setenv("LSS_CREATION_COIN_COSTS_JSON", raw)
    with pytest.raises(ValueError):
        creation_coin_costs()


def test_charge_and_refund_are_append_only_and_do_not_change_membership(tmp_path, monkeypatch):
    member, accounts = active_member(tmp_path, monkeypatch)
    monkeypatch.setenv("LSS_CREATION_COIN_COSTS_JSON", '{"image_poster_overage": 7}')
    wallet = CreditWalletStore(accounts.db_path)
    wallet.grant(member.user_id, 20, reason="test funds", actor="test")

    charge = charge_image_poster_overage(
        member.user_id,
        project_id="art-project",
        directive_id="directive-1",
    )
    assert charge.cost == 7
    assert wallet.balance(member.user_id) == 13
    assert charge.transaction["kind"] == "spend"

    refund = refund_image_poster_overage(
        member.user_id,
        charge,
        reason="renderer rejected job",
    )
    assert refund["kind"] == "refund"
    assert wallet.balance(member.user_id) == 20
    user = accounts.get_user(member.user_id)
    assert user["plan_id"] == "free"


def test_included_allowance_does_not_spend_creation_coins(tmp_path, monkeypatch):
    member, accounts = active_member(tmp_path, monkeypatch)
    monkeypatch.setenv("LSS_CREATION_COIN_COSTS_JSON", '{"image_poster_overage": 7}')
    wallet = CreditWalletStore(accounts.db_path)
    wallet.grant(member.user_id, 20, reason="test funds", actor="test")

    monkeypatch.setattr(routes, "_directive_render_snapshot", _image_snapshot)
    monkeypatch.setattr(routes, "require_image_poster_generation", lambda _member: {"remaining": 5})
    monkeypatch.setattr(
        routes,
        "base_queue_creative_render",
        lambda *_args, **_kwargs: {"submission": {"prompt_id": "ok"}},
    )
    monkeypatch.setattr(routes, "record_image_poster_generation", lambda *_args, **_kwargs: {"used": 1, "remaining": 4})

    response = routes.render_with_commercial_entitlements(
        "art-project",
        "directive-2",
        QueueRendererRequest(),
        request_for(member),
    )
    assert wallet.balance(member.user_id) == 20
    assert response["commercial_entitlements"]["creation_coin_overage"]["charged"] is False
    assert response["commercial_entitlements"]["creation_coin_overage"]["charged_amount"] == 0
    assert response["render_attempt"]["state"] == "queued"


def test_exhausted_allowance_spends_configured_coins_only_after_server_policy(tmp_path, monkeypatch):
    member, accounts = active_member(tmp_path, monkeypatch)
    monkeypatch.setenv("LSS_CREATION_COIN_COSTS_JSON", '{"image_poster_overage": 7}')
    wallet = CreditWalletStore(accounts.db_path)
    wallet.grant(member.user_id, 20, reason="test funds", actor="test")

    monkeypatch.setattr(routes, "_directive_render_snapshot", _image_snapshot)

    def exhausted(_member):
        raise PermissionError("Daily image/poster creation allowance reached (5 per day on this plan)")

    monkeypatch.setattr(routes, "require_image_poster_generation", exhausted)
    monkeypatch.setattr(
        routes,
        "base_queue_creative_render",
        lambda *_args, **_kwargs: {"submission": {"prompt_id": "accepted"}},
    )
    monkeypatch.setattr(routes, "record_image_poster_generation", lambda *_args, **_kwargs: {"used": 6, "remaining": 0})

    response = routes.render_with_commercial_entitlements(
        "art-project",
        "directive-3",
        QueueRendererRequest(),
        request_for(member),
    )
    assert wallet.balance(member.user_id) == 13
    coin_state = response["commercial_entitlements"]["creation_coin_overage"]
    assert coin_state["charged"] is True
    assert coin_state["charged_amount"] == 7
    assert coin_state["balance"] == 13
    assert coin_state["subscription_effect"] == "none"
    assert coin_state["esp_role_effect"] == "none"
    assert response["render_attempt"]["state"] == "queued"


def test_renderer_rejection_refunds_prepaid_overage(tmp_path, monkeypatch):
    member, accounts = active_member(tmp_path, monkeypatch)
    monkeypatch.setenv("LSS_CREATION_COIN_COSTS_JSON", '{"image_poster_overage": 7}')
    wallet = CreditWalletStore(accounts.db_path)
    wallet.grant(member.user_id, 20, reason="test funds", actor="test")

    monkeypatch.setattr(routes, "_directive_render_snapshot", _image_snapshot)
    monkeypatch.setattr(
        routes,
        "require_image_poster_generation",
        lambda _member: (_ for _ in ()).throw(PermissionError("Daily image/poster creation allowance reached")),
    )

    def reject(*_args, **_kwargs):
        raise HTTPException(503, "renderer unavailable")

    monkeypatch.setattr(routes, "base_queue_creative_render", reject)
    with pytest.raises(HTTPException) as exc:
        routes.render_with_commercial_entitlements(
            "art-project",
            "directive-4",
            QueueRendererRequest(),
            request_for(member),
        )
    assert exc.value.status_code == 503
    assert wallet.balance(member.user_id) == 20
    history = wallet.transactions(member.user_id)
    assert [row["kind"] for row in history[:2]] == ["refund", "spend"]


def test_insufficient_coins_reject_before_renderer_submission(tmp_path, monkeypatch):
    member, _ = active_member(tmp_path, monkeypatch)
    monkeypatch.setenv("LSS_CREATION_COIN_COSTS_JSON", '{"image_poster_overage": 7}')
    called = {"renderer": False}

    monkeypatch.setattr(routes, "_directive_render_snapshot", _image_snapshot)
    monkeypatch.setattr(
        routes,
        "require_image_poster_generation",
        lambda _member: (_ for _ in ()).throw(PermissionError("Daily image/poster creation allowance reached")),
    )

    def renderer(*_args, **_kwargs):
        called["renderer"] = True
        return {}

    monkeypatch.setattr(routes, "base_queue_creative_render", renderer)
    with pytest.raises(HTTPException) as exc:
        routes.render_with_commercial_entitlements(
            "art-project",
            "directive-5",
            QueueRendererRequest(),
            request_for(member),
        )
    assert exc.value.status_code == 402
    assert called["renderer"] is False


def test_missing_overage_cost_preserves_daily_limit_and_does_not_render(tmp_path, monkeypatch):
    member, _ = active_member(tmp_path, monkeypatch)
    monkeypatch.delenv("LSS_CREATION_COIN_COSTS_JSON", raising=False)
    called = {"renderer": False}

    monkeypatch.setattr(routes, "_directive_render_snapshot", _image_snapshot)
    monkeypatch.setattr(
        routes,
        "require_image_poster_generation",
        lambda _member: (_ for _ in ()).throw(PermissionError("Daily image/poster creation allowance reached")),
    )
    monkeypatch.setattr(routes, "base_queue_creative_render", lambda *_args, **_kwargs: called.update(renderer=True))

    with pytest.raises(HTTPException) as exc:
        routes.render_with_commercial_entitlements(
            "art-project",
            "directive-6",
            QueueRendererRequest(),
            request_for(member),
        )
    assert exc.value.status_code == 429
    assert called["renderer"] is False
