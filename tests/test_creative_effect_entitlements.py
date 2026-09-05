from __future__ import annotations

import sqlite3

import pytest

from aura_music_studio.credit_wallet import CreditWalletStore
from aura_music_studio.creative_effect_entitlements import CreativeEffectEntitlementStore, PUBLIC_COIN_UNIT


def _stores(tmp_path):
    db = tmp_path / "coins.sqlite3"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
        con.executemany("INSERT INTO users(id) VALUES (?)", [("member-a",), ("member-b",)])
    wallet = CreditWalletStore(db)
    effects = CreativeEffectEntitlementStore(db)
    return wallet, effects


def test_public_coin_name_is_cosmic_creation_coin():
    assert PUBLIC_COIN_UNIT == "COSMIC_CREATION_COIN"


def test_silver_purchase_debits_and_grants_in_same_ledger_database(tmp_path):
    wallet, effects = _stores(tmp_path)
    wallet.grant("member-a", 1000, reason="test funds", actor="test", reference="grant:a")
    result = effects.purchase("member-a", "music.fx.limiter", idempotency_key="purchase-1")
    assert result["purchased"] is True
    assert result["coin_price"] == 200
    assert result["balance_after"] == 800
    assert wallet.balance("member-a") == 800
    entitlement = effects.has_entitlement("member-a", "music.fx.limiter")
    assert entitlement["owned"] is True
    assert entitlement["entitlement_band"] == "silver"
    assert wallet.verify_integrity("member-a")["valid"] is True


def test_gold_purchase_cost_is_500_coins(tmp_path):
    wallet, effects = _stores(tmp_path)
    wallet.grant("member-a", 1000, reason="test funds", actor="test", reference="grant:a")
    result = effects.purchase("member-a", "music.fx.stereo_width", idempotency_key="gold-1")
    assert result["coin_price"] == 500
    assert wallet.balance("member-a") == 500
    assert effects.has_entitlement("member-a", "music.fx.stereo_width")["owned"] is True


def test_duplicate_purchase_never_debits_twice_even_with_new_request_key(tmp_path):
    wallet, effects = _stores(tmp_path)
    wallet.grant("member-a", 1000, reason="test funds", actor="test", reference="grant:a")
    first = effects.purchase("member-a", "music.fx.limiter", idempotency_key="retry-1")
    second = effects.purchase("member-a", "music.fx.limiter", idempotency_key="retry-2")
    assert first["purchased"] is True
    assert second["purchased"] is False
    assert second["already_owned"] is True
    assert wallet.balance("member-a") == 800
    spends = [row for row in wallet.transactions("member-a") if row["kind"] == "spend"]
    assert len(spends) == 1


def test_insufficient_balance_rejects_without_partial_entitlement(tmp_path):
    wallet, effects = _stores(tmp_path)
    wallet.grant("member-a", 100, reason="small balance", actor="test", reference="grant:a")
    with pytest.raises(ValueError, match="Insufficient Cosmic Creation Coins"):
        effects.purchase("member-a", "music.fx.limiter", idempotency_key="too-poor")
    assert wallet.balance("member-a") == 100
    assert effects.has_entitlement("member-a", "music.fx.limiter")["owned"] is False
    assert [row for row in wallet.transactions("member-a") if row["kind"] == "spend"] == []


def test_core_effect_is_included_without_coin_debit(tmp_path):
    wallet, effects = _stores(tmp_path)
    result = effects.purchase("member-a", "music.fx.gain", idempotency_key="core")
    assert result["included"] is True
    assert result["coin_price"] == 0
    assert wallet.balance("member-a") == 0
    assert effects.has_entitlement("member-a", "music.fx.gain")["owned"] is True


def test_entitlements_are_isolated_between_member_accounts(tmp_path):
    wallet, effects = _stores(tmp_path)
    wallet.grant("member-a", 500, reason="test funds", actor="test", reference="grant:a")
    effects.purchase("member-a", "music.fx.limiter", idempotency_key="isolation")
    assert effects.has_entitlement("member-a", "music.fx.limiter")["owned"] is True
    assert effects.has_entitlement("member-b", "music.fx.limiter")["owned"] is False


def test_admin_refund_restores_coins_and_revokes_effect_atomically(tmp_path):
    wallet, effects = _stores(tmp_path)
    wallet.grant("member-a", 1000, reason="test funds", actor="test", reference="grant:a")
    effects.purchase("member-a", "music.fx.limiter", idempotency_key="purchase")
    refund = effects.refund_and_revoke("member-a", "music.fx.limiter", reference="refund:1", reason="approved test refund", actor="test-admin")
    assert refund["refunded"] is True
    assert refund["coin_amount"] == 200
    assert wallet.balance("member-a") == 1000
    assert effects.has_entitlement("member-a", "music.fx.limiter")["owned"] is False
    assert wallet.verify_integrity("member-a")["valid"] is True


def test_refunded_effect_can_be_purchased_again_with_new_idempotency_key(tmp_path):
    wallet, effects = _stores(tmp_path)
    wallet.grant("member-a", 1000, reason="test funds", actor="test", reference="grant:a")
    effects.purchase("member-a", "music.fx.limiter", idempotency_key="purchase-1")
    effects.refund_and_revoke("member-a", "music.fx.limiter", reference="refund:1", reason="refund", actor="admin")
    repurchase = effects.purchase("member-a", "music.fx.limiter", idempotency_key="purchase-2")
    assert repurchase["purchased"] is True
    assert wallet.balance("member-a") == 800
    assert effects.has_entitlement("member-a", "music.fx.limiter")["owned"] is True
