from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from aura_music_studio.cosmic_economy import (
    BASELINE_COINS, BASELINE_CURRENCY, BASELINE_PACK_ID, BASELINE_PRICE_MINOR,
    CHARGEBACK_DEBIT, EconomyError, EligibilityDecision, GIFT_DEBIT,
    LiveGiftContext, VerifiedPaymentEvent,
)
from aura_music_studio.cosmic_economy_integrations import IntegratedCosmicEconomy


class AllowEligibility:
    def check(self, **kwargs):
        return EligibilityDecision(True)


class Live:
    def __init__(self, active=True, gift_eligible=True, recipient_eligible=True):
        self.active, self.gift_eligible, self.recipient_eligible = active, gift_eligible, recipient_eligible

    def gift_context(self, *, live_session_id, recipient_creator_id):
        return LiveGiftContext(live_session_id, recipient_creator_id, self.active, self.gift_eligible, self.recipient_eligible)


def econ(tmp_path, *, live=None, eligibility=None):
    return IntegratedCosmicEconomy(
        tmp_path / "economy.sqlite3",
        live_sessions=live or Live(),
        eligibility=eligibility or AllowEligibility(),
    )


def credit(e, user="viewer-1", coins=100, key="seed"):
    return e.promotional_credit(user_id=user, coin_quantity=coins, campaign_ref="fixture", idempotency_key=key)


def payment(purchase, kind, event_id):
    return VerifiedPaymentEvent(
        provider=purchase["provider"], provider_event_id=event_id,
        provider_payment_id="provider-payment-1", purchase_id=purchase["id"],
        event_type=kind, verified=True, occurred_at=datetime.now(timezone.utc).isoformat(),
    )


def purchase(e, user="viewer-1", key="purchase-1"):
    return e.create_purchase(user_id=user, pack_id=BASELINE_PACK_ID, pack_version=1, provider="fake", idempotency_key=key)


def send(e, user="viewer-1", creator="creator-1", key="gift-1", quantity=1, gift="starlight-spark", version=1):
    return e.send_gift(
        sender_user_id=user, recipient_creator_id=creator, live_session_id="live-1",
        gift_id=gift, gift_version=version, quantity=quantity, idempotency_key=key,
    )


def test_baseline_pack_is_1000_coins_for_500_minor_gbp(tmp_path):
    p = econ(tmp_path).get_pack(BASELINE_PACK_ID, 1)
    assert (p["coin_quantity"], p["fiat_amount_minor"], p["fiat_currency"]) == (BASELINE_COINS, BASELINE_PRICE_MINOR, BASELINE_CURRENCY) == (1000, 500, "GBP")
    assert isinstance(p["coin_quantity"], int) and isinstance(p["fiat_amount_minor"], int)


def test_purchase_redirect_state_never_credits_coins(tmp_path):
    e = econ(tmp_path); p = purchase(e)
    assert p["status"] == "pending" and e.get_balance("viewer-1")["available_coins"] == 0


def test_verified_payment_credits_exactly_once(tmp_path):
    e = econ(tmp_path); p = e.bind_provider_payment(purchase(e)["id"], provider_payment_id="provider-payment-1")
    first = e.apply_verified_payment_event(payment(p, "confirmed", "evt-1")); second = e.apply_verified_payment_event(payment(p, "confirmed", "evt-1"))
    assert first["idempotent_replay"] is False and second["idempotent_replay"] is True
    assert e.get_balance("viewer-1")["available_coins"] == 1000


def test_unverified_webhook_is_rejected(tmp_path):
    e = econ(tmp_path); p = purchase(e)
    bad = payment(p, "confirmed", "forged"); bad = VerifiedPaymentEvent(**{**bad.__dict__, "verified": False})
    with pytest.raises(EconomyError) as exc: e.apply_verified_payment_event(bad)
    assert exc.value.code == "INVALID_PAYMENT_WEBHOOK" and e.get_balance("viewer-1")["available_coins"] == 0


def test_purchase_same_key_changed_payload_rejected(tmp_path):
    e = econ(tmp_path)
    e.publish_pack(pack_id="cosmic-2000", version=1, display_name="2,000 Coins", coin_quantity=2000, fiat_amount_minor=1000, fiat_currency="GBP", actor="owner", approval_reference="test")
    purchase(e)
    with pytest.raises(EconomyError) as exc:
        e.create_purchase(user_id="viewer-1", pack_id="cosmic-2000", pack_version=1, provider="fake", idempotency_key="purchase-1")
    assert exc.value.code == "IDEMPOTENCY_KEY_REUSED"


def test_purchase_key_cannot_move_to_another_user(tmp_path):
    e = econ(tmp_path); purchase(e, "viewer-1", "shared-key")
    with pytest.raises(EconomyError) as exc: purchase(e, "viewer-2", "shared-key")
    assert exc.value.code == "IDEMPOTENCY_KEY_SCOPE_MISMATCH"


def test_refund_before_spend_uses_compensation_without_debt(tmp_path):
    e = econ(tmp_path); p = e.bind_provider_payment(purchase(e)["id"], provider_payment_id="provider-payment-1")
    e.apply_verified_payment_event(payment(p, "confirmed", "evt-c")); e.apply_verified_payment_event(payment(p, "refunded", "evt-r"))
    b = e.get_balance("viewer-1"); assert b["available_coins"] == 0 and b["recovery_debt_coins"] == 0


def test_chargeback_after_spend_creates_recovery_debt(tmp_path):
    e = econ(tmp_path); p = e.bind_provider_payment(purchase(e)["id"], provider_payment_id="provider-payment-1")
    e.apply_verified_payment_event(payment(p, "confirmed", "evt-c"))
    e.publish_gift_definition(gift_id="aurora-wave", version=1, display_name="Aurora Wave", description="Original", coin_cost=800, actor="owner", approval_reference="test")
    send(e, gift="aurora-wave"); e.apply_verified_payment_event(payment(p, "chargeback", "evt-ch"))
    b = e.get_balance("viewer-1"); assert b["available_coins"] == 0 and b["recovery_debt_coins"] == 800
    assert any(x["entry_type"] == CHARGEBACK_DEBIT for x in e.transaction_history("viewer-1")["entries"])


def test_gift_commit_is_atomic_and_exact(tmp_path):
    e = econ(tmp_path); credit(e)
    r = send(e, quantity=3); tx = r["gift_transaction"]
    assert (tx["status"], tx["unit_coin_cost"], tx["total_coin_cost"]) == ("committed", 10, 30)
    assert e.get_balance("viewer-1")["available_coins"] == 70
    assert r["creator_receipt"]["status"] == "pending" and r["creator_receipt"]["payable_amount_minor"] is None
    assert any(x["event_type"] == "shared_sky.gift.committed" for x in e.pending_outbox())


def test_duplicate_gift_returns_original_and_debits_once(tmp_path):
    e = econ(tmp_path); credit(e); a = send(e, quantity=2); b = send(e, quantity=2)
    assert a["gift_transaction"]["id"] == b["gift_transaction"]["id"] and b["idempotent_replay"] is True
    assert len([x for x in e.transaction_history("viewer-1")["entries"] if x["entry_type"] == GIFT_DEBIT]) == 1


def test_gift_same_key_changed_quantity_rejected(tmp_path):
    e = econ(tmp_path); credit(e); send(e)
    with pytest.raises(EconomyError) as exc: send(e, quantity=2)
    assert exc.value.code == "IDEMPOTENCY_KEY_REUSED"


def test_gift_key_cannot_move_to_another_user(tmp_path):
    e = econ(tmp_path); credit(e, "viewer-1", 20, "s1"); credit(e, "viewer-2", 20, "s2"); send(e, user="viewer-1", key="shared")
    with pytest.raises(EconomyError) as exc: send(e, user="viewer-2", key="shared")
    assert exc.value.code == "IDEMPOTENCY_KEY_SCOPE_MISMATCH" and e.get_balance("viewer-2")["available_coins"] == 20


def test_concurrent_gifts_cannot_overspend(tmp_path):
    db = tmp_path / "economy.sqlite3"; e = IntegratedCosmicEconomy(db, live_sessions=Live(), eligibility=AllowEligibility()); credit(e, coins=10)
    def attempt(key):
        x = IntegratedCosmicEconomy(db, live_sessions=Live(), eligibility=AllowEligibility())
        try: send(x, key=key); return "ok"
        except EconomyError as exc: return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(attempt, ["g1", "g2"]))
    assert results.count("ok") == 1 and results.count("INSUFFICIENT_COIN_BALANCE") == 1 and e.get_balance("viewer-1")["available_coins"] == 0


def test_ended_live_rejects_without_balance_change(tmp_path):
    e = econ(tmp_path, live=Live(active=False)); credit(e)
    with pytest.raises(EconomyError) as exc: send(e)
    assert exc.value.code == "LIVE_SESSION_NOT_GIFT_ELIGIBLE" and e.get_balance("viewer-1")["available_coins"] == 100


def test_missing_live_adapter_fails_closed(tmp_path):
    e = IntegratedCosmicEconomy(tmp_path / "economy.sqlite3", eligibility=AllowEligibility()); credit(e)
    with pytest.raises(EconomyError) as exc: send(e)
    assert exc.value.code == "LIVE_VALIDATION_UNAVAILABLE"


def test_missing_age_region_policy_fails_closed(tmp_path):
    e = IntegratedCosmicEconomy(tmp_path / "economy.sqlite3", live_sessions=Live()); credit(e)
    with pytest.raises(EconomyError) as exc: send(e)
    assert exc.value.code == "ELIGIBILITY_POLICY_UNAVAILABLE"


def test_self_gift_is_blocked_and_not_debited(tmp_path):
    e = econ(tmp_path); credit(e, "same", 100)
    with pytest.raises(EconomyError) as exc: send(e, user="same", creator="same")
    assert exc.value.code == "GIFT_BLOCKED" and e.get_balance("same")["available_coins"] == 100


def test_spending_hard_limit_blocks_server_side(tmp_path):
    e = econ(tmp_path); credit(e); e.set_spending_limits("viewer-1", actor="owner", reason="safety", daily_hard_limit=15); send(e)
    with pytest.raises(EconomyError) as exc: send(e, key="g2")
    assert exc.value.code == "SPENDING_LIMIT_EXCEEDED" and e.get_balance("viewer-1")["available_coins"] == 90


def test_gift_version_preserves_historical_cost(tmp_path):
    e = econ(tmp_path); credit(e, coins=100); old = send(e)
    e.publish_gift_definition(gift_id="starlight-spark", version=2, display_name="Starlight Spark", description="v2", coin_cost=25, actor="owner", approval_reference="v2")
    new = send(e, key="g2", version=2)
    assert old["gift_transaction"]["unit_coin_cost"] == 10 and new["gift_transaction"]["unit_coin_cost"] == 25


def test_gift_reversal_preserves_original_and_compensates(tmp_path):
    e = econ(tmp_path); credit(e); sent = send(e, quantity=2)
    rev = e.reverse_gift(sent["gift_transaction"]["id"], actor="owner", reason="technical duplicate", reference="case-1", idempotency_key="rev-1")
    assert rev["gift_transaction"]["status"] == "reversed" and rev["creator_receipt"]["status"] == "reversed"
    types = [x["entry_type"] for x in e.transaction_history("viewer-1")["entries"]]
    assert GIFT_DEBIT in types and "GIFT_REVERSAL_CREDIT" in types and e.get_balance("viewer-1")["available_coins"] == 100


def test_no_default_creator_payout_formula(tmp_path):
    e = econ(tmp_path); credit(e); send(e); s = e.creator_statement("creator-1")
    assert s["payout_formula_configured"] is False and s["payable_fiat_total_minor"] is None
    assert s["entries"][0]["payout_policy_id"] is None and s["entries"][0]["payable_amount_minor"] is None


def test_owner_adjustment_is_ledgered_and_ledger_is_append_only(tmp_path):
    e = econ(tmp_path); entry = e.owner_adjustment(user_id="viewer-1", coin_delta=25, actor="owner", reason="support compensation", reference="case", idempotency_key="adj-1")
    assert e.get_balance("viewer-1")["available_coins"] == 25
    with e._connect() as con:
        with pytest.raises(Exception): con.execute("UPDATE coin_ledger_entries SET coin_delta=999 WHERE id=?", (entry["id"],))


def test_frozen_account_cannot_gift(tmp_path):
    e = econ(tmp_path); credit(e); e.freeze_account("viewer-1", frozen=True, actor="owner", reason="risk review")
    with pytest.raises(EconomyError) as exc: send(e)
    assert exc.value.code == "COIN_ACCOUNT_RESTRICTED" and e.get_balance("viewer-1")["available_coins"] == 100


def test_gift_kill_switch_is_explicit_and_non_mutating(tmp_path):
    e = econ(tmp_path); credit(e); e.set_feature_flag("gift_sends_enabled", False, actor="owner", reason="incident")
    with pytest.raises(EconomyError) as exc: send(e)
    assert exc.value.code == "GIFT_SENDS_DISABLED" and e.get_balance("viewer-1")["available_coins"] == 100


def test_reconciliation_clean_for_valid_state(tmp_path):
    e = econ(tmp_path); credit(e); send(e, quantity=2); result = e.reconcile()
    assert result == {"ok": True, "discrepancies": []}
