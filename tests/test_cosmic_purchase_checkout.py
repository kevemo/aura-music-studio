from __future__ import annotations

import pytest

from aura_music_studio.cosmic_economy import (
    BASELINE_PACK_ID,
    EconomyError,
    EligibilityDecision,
    LiveGiftContext,
)
from aura_music_studio.cosmic_economy_personal_limits import PersonalLimitCosmicEconomy


class AllowEligibility:
    def check(self, **kwargs):
        return EligibilityDecision(True)


class Live:
    def gift_context(self, *, live_session_id, recipient_creator_id):
        return LiveGiftContext(live_session_id, recipient_creator_id, True, True, True)


def economy(tmp_path):
    return PersonalLimitCosmicEconomy(
        tmp_path / "economy.sqlite3",
        live_sessions=Live(),
        eligibility=AllowEligibility(),
    )


def create_purchase(e, *, user_id="viewer-1", key="purchase-1"):
    return e.create_purchase(
        user_id=user_id,
        pack_id=BASELINE_PACK_ID,
        pack_version=1,
        provider="fake-signed",
        idempotency_key=key,
    )


def test_checkout_binding_is_idempotent_for_same_purchase(tmp_path):
    e = economy(tmp_path)
    purchase = create_purchase(e)
    first = e.bind_purchase_checkout(
        purchase["id"],
        provider="fake-signed",
        provider_payment_id="pay-1",
        checkout_url="https://example.invalid/checkout/pay-1",
        status="pending",
    )
    assert first["idempotent_replay"] is False
    assert first["purchase"]["provider_payment_id"] == "pay-1"

    replay = e.bind_purchase_checkout(
        purchase["id"],
        provider="fake-signed",
        provider_payment_id="pay-1",
        checkout_url="https://example.invalid/checkout/pay-1",
        status="pending",
    )
    assert replay["idempotent_replay"] is True
    assert replay["checkout"]["provider_payment_id"] == "pay-1"
    assert e.get_purchase_checkout(purchase["id"])["checkout_url"].endswith("/pay-1")


def test_checkout_binding_rejects_conflicting_checkout_for_same_purchase(tmp_path):
    e = economy(tmp_path)
    purchase = create_purchase(e)
    e.bind_purchase_checkout(
        purchase["id"],
        provider="fake-signed",
        provider_payment_id="pay-1",
        checkout_url="https://example.invalid/checkout/pay-1",
    )
    with pytest.raises(EconomyError) as exc:
        e.bind_purchase_checkout(
            purchase["id"],
            provider="fake-signed",
            provider_payment_id="pay-2",
            checkout_url="https://example.invalid/checkout/pay-2",
        )
    assert exc.value.code == "PAYMENT_CHECKOUT_CONFLICT"


def test_provider_payment_reference_cannot_bind_two_purchases(tmp_path):
    e = economy(tmp_path)
    first = create_purchase(e, user_id="viewer-1", key="purchase-1")
    second = create_purchase(e, user_id="viewer-2", key="purchase-2")
    e.bind_purchase_checkout(
        first["id"],
        provider="fake-signed",
        provider_payment_id="provider-reference-1",
        checkout_url="https://example.invalid/checkout/one",
    )
    with pytest.raises(EconomyError) as exc:
        e.bind_purchase_checkout(
            second["id"],
            provider="fake-signed",
            provider_payment_id="provider-reference-1",
            checkout_url="https://example.invalid/checkout/two",
        )
    assert exc.value.code == "PAYMENT_REFERENCE_REUSED"
    assert e.get_purchase_checkout(second["id"]) is None


def test_checkout_binding_rejects_provider_mismatch(tmp_path):
    e = economy(tmp_path)
    purchase = create_purchase(e)
    with pytest.raises(EconomyError) as exc:
        e.bind_purchase_checkout(
            purchase["id"],
            provider="other-provider",
            provider_payment_id="pay-1",
            checkout_url="https://example.invalid/checkout/pay-1",
        )
    assert exc.value.code == "PAYMENT_PROVIDER_MISMATCH"


def test_checkout_binding_does_not_credit_coins(tmp_path):
    e = economy(tmp_path)
    purchase = create_purchase(e)
    e.bind_purchase_checkout(
        purchase["id"],
        provider="fake-signed",
        provider_payment_id="pay-1",
        checkout_url="https://example.invalid/checkout/pay-1",
    )
    assert e.get_balance("viewer-1")["available_coins"] == 0
    with e._connect() as con:
        purchase_row = con.execute(
            "SELECT status,ledger_credit_id FROM coin_purchases WHERE id=?",
            (purchase["id"],),
        ).fetchone()
    assert purchase_row["status"] == "pending"
    assert purchase_row["ledger_credit_id"] is None
