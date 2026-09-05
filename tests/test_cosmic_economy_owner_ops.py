from __future__ import annotations

import pytest

from aura_music_studio.cosmic_economy import (
    BASELINE_PACK_ID,
    EconomyError,
    EligibilityDecision,
    LiveGiftContext,
    PROMOTIONAL_CREDIT,
)
from aura_music_studio.cosmic_economy_integrations import IntegratedCosmicEconomy
from aura_music_studio.cosmic_economy_owner_ops import EconomyOwnerOperations


class AllowEligibility:
    def check(self, **kwargs):
        return EligibilityDecision(True)


class Live:
    def gift_context(self, *, live_session_id, recipient_creator_id):
        return LiveGiftContext(live_session_id, recipient_creator_id, True, True, True)


def economy(tmp_path):
    return IntegratedCosmicEconomy(
        tmp_path / "economy.sqlite3",
        live_sessions=Live(),
        eligibility=AllowEligibility(),
    )


def seed_gift(e):
    e.promotional_credit(
        user_id="viewer-1",
        coin_quantity=100,
        campaign_ref="fixture",
        idempotency_key="seed",
    )
    return e.send_gift(
        sender_user_id="viewer-1",
        recipient_creator_id="creator-1",
        live_session_id="live-1",
        gift_id="starlight-spark",
        gift_version=1,
        quantity=1,
        idempotency_key="gift-1",
    )


def test_finance_snapshot_keeps_unknown_accounting_values_unknown(tmp_path):
    e = economy(tmp_path)
    seed_gift(e)
    snap = EconomyOwnerOperations(e).finance_snapshot()
    assert snap["wallet_liability_state"]["available_coins"] == 90
    assert snap["promotional_coins_issued"] == 100
    assert snap["recognised_esp_revenue_minor"] is None
    assert snap["creator_payable_fiat_minor"] is None
    assert snap["processor_fees_minor"] is None
    assert snap["tax_minor"] is None
    assert snap["profit_minor"] is None


def test_risk_case_can_be_reviewed_without_automatic_sanction(tmp_path):
    e = economy(tmp_path)
    e.promotional_credit(
        user_id="same",
        coin_quantity=100,
        campaign_ref="fixture",
        idempotency_key="seed",
    )
    with pytest.raises(EconomyError):
        e.send_gift(
            sender_user_id="same",
            recipient_creator_id="same",
            live_session_id="live-1",
            gift_id="starlight-spark",
            gift_version=1,
            quantity=1,
            idempotency_key="gift-self",
        )
    ops = EconomyOwnerOperations(e)
    cases = ops.list_risk_cases()
    assert len(cases) == 1
    reviewed = ops.review_risk_case(
        cases[0]["id"],
        reviewer="owner-test",
        decision="allow",
        reason="manual evidence cleared",
    )
    assert reviewed["status"] == "closed"
    assert reviewed["decision"] == "allow"
    assert e.get_balance("same")["available_coins"] == 100


def test_creator_receipt_hold_and_release_do_not_create_payout(tmp_path):
    e = economy(tmp_path)
    result = seed_gift(e)
    receipt_id = result["creator_receipt"]["id"]
    ops = EconomyOwnerOperations(e)
    held = ops.set_creator_receipt_hold(
        receipt_id,
        held=True,
        actor="owner-test",
        reason="chargeback review",
        reference="case-1",
    )
    assert held["status"] == "held"
    assert held["payable_amount_minor"] is None
    released = ops.set_creator_receipt_hold(
        receipt_id,
        held=False,
        actor="owner-test",
        reason="review cleared",
        reference="case-1",
    )
    assert released["status"] == "pending"
    assert released["payable_amount_minor"] is None


def test_promotional_grant_stays_separate_from_purchase_revenue(tmp_path):
    e = economy(tmp_path)
    ops = EconomyOwnerOperations(e)
    entry = ops.grant_promotional_coins(
        user_id="viewer-1",
        coin_quantity=250,
        campaign_ref="launch-bonus",
        idempotency_key="promo-1",
        actor="owner-test",
        reason="approved launch campaign",
    )
    assert entry["entry_type"] == PROMOTIONAL_CREDIT
    assert entry["promotion_id"] == "launch-bonus"
    assert e.get_balance("viewer-1")["available_coins"] == 250
    snapshot = ops.finance_snapshot()
    assert snapshot["promotional_coins_issued"] == 250
    assert snapshot["coin_purchases_by_currency_and_status"] == []


def test_catalogue_availability_changes_do_not_rewrite_price_or_version(tmp_path):
    e = economy(tmp_path)
    ops = EconomyOwnerOperations(e)

    pack_before = e.get_pack(BASELINE_PACK_ID, 1)
    disabled_pack = ops.set_coin_pack_active(
        BASELINE_PACK_ID,
        1,
        active=False,
        actor="owner-test",
        reason="incident response",
    )
    assert disabled_pack["active"] == 0
    assert disabled_pack["coin_quantity"] == pack_before["coin_quantity"] == 1000
    assert disabled_pack["fiat_amount_minor"] == pack_before["fiat_amount_minor"] == 500
    assert all(row["pack_id"] != BASELINE_PACK_ID for row in e.list_packs())

    gift_before = e.get_gift("starlight-spark", 1)
    disabled_gift = ops.set_gift_active(
        "starlight-spark",
        1,
        active=False,
        actor="owner-test",
        reason="asset review",
    )
    assert disabled_gift["active"] == 0
    assert disabled_gift["coin_cost"] == gift_before["coin_cost"] == 10
    assert disabled_gift["version"] == gift_before["version"] == 1
    assert all(row["gift_id"] != "starlight-spark" for row in e.list_gifts())


def test_discrepancy_queue_surfaces_and_resolves_without_auto_repair(tmp_path):
    e = economy(tmp_path)
    e.promotional_credit(
        user_id="viewer-1",
        coin_quantity=100,
        campaign_ref="fixture",
        idempotency_key="seed",
    )
    account = e.get_account("viewer-1")
    with e._connect() as con:
        con.execute(
            "UPDATE coin_accounts SET available_balance=99 WHERE id=?",
            (account["id"],),
        )
    result = e.reconcile()
    assert result["ok"] is False
    ops = EconomyOwnerOperations(e)
    rows = ops.list_reconciliation_discrepancies()
    mismatch = next(
        row for row in rows if row["discrepancy_type"] == "ACCOUNT_LEDGER_MISMATCH"
    )
    resolved = ops.resolve_reconciliation_discrepancy(
        mismatch["id"],
        actor="owner-test",
        resolution_note="reviewed; source data requires separate repair approval",
    )
    assert resolved["status"] == "resolved"
    assert resolved["resolved_at"] is not None
    assert e.get_balance("viewer-1")["available_coins"] == 99

    rerun = e.reconcile()
    assert rerun["ok"] is False
    reopened = ops.list_reconciliation_discrepancies(status="open")
    assert any(row["discrepancy_type"] == "ACCOUNT_LEDGER_MISMATCH" for row in reopened)
