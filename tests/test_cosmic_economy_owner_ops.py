from __future__ import annotations

import pytest

from aura_music_studio.cosmic_economy import EconomyError, EligibilityDecision, LiveGiftContext
from aura_music_studio.cosmic_economy_integrations import IntegratedCosmicEconomy
from aura_music_studio.cosmic_economy_owner_ops import EconomyOwnerOperations


class AllowEligibility:
    def check(self, **kwargs): return EligibilityDecision(True)


class Live:
    def gift_context(self, *, live_session_id, recipient_creator_id):
        return LiveGiftContext(live_session_id, recipient_creator_id, True, True, True)


def economy(tmp_path):
    return IntegratedCosmicEconomy(tmp_path / "economy.sqlite3", live_sessions=Live(), eligibility=AllowEligibility())


def seed_gift(e):
    e.promotional_credit(user_id="viewer-1", coin_quantity=100, campaign_ref="fixture", idempotency_key="seed")
    return e.send_gift(sender_user_id="viewer-1", recipient_creator_id="creator-1", live_session_id="live-1", gift_id="starlight-spark", gift_version=1, quantity=1, idempotency_key="gift-1")


def test_finance_snapshot_keeps_unknown_accounting_values_unknown(tmp_path):
    e = economy(tmp_path); seed_gift(e); snap = EconomyOwnerOperations(e).finance_snapshot()
    assert snap["wallet_liability_state"]["available_coins"] == 90
    assert snap["promotional_coins_issued"] == 100
    assert snap["recognised_esp_revenue_minor"] is None
    assert snap["creator_payable_fiat_minor"] is None
    assert snap["processor_fees_minor"] is None and snap["tax_minor"] is None and snap["profit_minor"] is None


def test_risk_case_can_be_reviewed_without_automatic_sanction(tmp_path):
    e = economy(tmp_path)
    e.promotional_credit(user_id="same", coin_quantity=100, campaign_ref="fixture", idempotency_key="seed")
    with pytest.raises(EconomyError):
        e.send_gift(sender_user_id="same", recipient_creator_id="same", live_session_id="live-1", gift_id="starlight-spark", gift_version=1, quantity=1, idempotency_key="gift-self")
    ops = EconomyOwnerOperations(e); cases = ops.list_risk_cases(); assert len(cases) == 1
    reviewed = ops.review_risk_case(cases[0]["id"], reviewer="owner-test", decision="allow", reason="manual evidence cleared")
    assert reviewed["status"] == "closed" and reviewed["decision"] == "allow"
    assert e.get_balance("same")["available_coins"] == 100


def test_creator_receipt_hold_and_release_do_not_create_payout(tmp_path):
    e = economy(tmp_path); result = seed_gift(e); receipt_id = result["creator_receipt"]["id"]; ops = EconomyOwnerOperations(e)
    held = ops.set_creator_receipt_hold(receipt_id, held=True, actor="owner-test", reason="chargeback review", reference="case-1")
    assert held["status"] == "held" and held["payable_amount_minor"] is None
    released = ops.set_creator_receipt_hold(receipt_id, held=False, actor="owner-test", reason="review cleared", reference="case-1")
    assert released["status"] == "pending" and released["payable_amount_minor"] is None


def test_discrepancy_queue_surfaces_materialised_balance_mismatch(tmp_path):
    e = economy(tmp_path); e.promotional_credit(user_id="viewer-1", coin_quantity=100, campaign_ref="fixture", idempotency_key="seed")
    account = e.get_account("viewer-1")
    with e._connect() as con: con.execute("UPDATE coin_accounts SET available_balance=99 WHERE id=?", (account["id"],))
    result = e.reconcile(); assert result["ok"] is False
    rows = EconomyOwnerOperations(e).list_reconciliation_discrepancies()
    assert any(row["discrepancy_type"] == "ACCOUNT_LEDGER_MISMATCH" for row in rows)
