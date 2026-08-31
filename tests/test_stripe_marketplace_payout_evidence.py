from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aura_music_studio import stripe_billing_hardening as hardening
from aura_music_studio import stripe_marketplace_payout_evidence as payouts
from aura_music_studio.stripe_marketplace_fee_evidence import StripeMarketplaceFeeEvidenceStore
from aura_music_studio.stripe_marketplace_refund_evidence import StripeMarketplaceRefundEvidenceStore


def _config():
    return SimpleNamespace(
        secret_key="sk_test_private",
        webhook_secret="whsec_test_private",
        public_base_url="https://command.example",
        webhook_configured=True,
    )


def _payout(**overrides):
    payload = {
        "id": "po_marketplace_123",
        "object": "payout",
        "amount": 1900,
        "currency": "gbp",
        "automatic": True,
        "arrival_date": 1788210000,
        "balance_transaction": "txn_payout_123",
        "failure_balance_transaction": None,
        "failure_code": None,
        "reconciliation_status": "in_progress",
        "status": "paid",
    }
    payload.update(overrides)
    return payload


def _marketplace_evidence(db):
    fee = StripeMarketplaceFeeEvidenceStore(db)
    fee.record(
        event_id="evt_payment_123",
        checkout_session_id="cs_marketplace_123",
        order_id="order-marketplace-123",
        payment_intent_id="pi_marketplace_123",
        charge_id="ch_marketplace_123",
        balance_transaction_id="txn_marketplace_charge_123",
        gross_minor=2500,
        provider_fee_minor=100,
        net_minor=2400,
        currency="GBP",
    )
    refunds = StripeMarketplaceRefundEvidenceStore(db)
    refunds.record(
        event_id="evt_refund_123",
        refund_id="re_marketplace_123",
        checkout_session_id="cs_marketplace_123",
        payment_intent_id="pi_marketplace_123",
        charge_id="ch_marketplace_123",
        refund_balance_transaction_id="txn_marketplace_refund_123",
        customer_refund_minor=500,
        provider_balance_amount_minor=-500,
        provider_balance_fee_minor=0,
        provider_balance_net_minor=-500,
        currency="GBP",
        original_gross_minor=2500,
        original_net_minor=2400,
    )
    return fee


def test_paid_payout_is_provider_evidence_not_bank_reconciliation(tmp_path, monkeypatch):
    store = payouts.StripePayoutEvidenceStore(tmp_path / "payout.sqlite3")
    monkeypatch.setattr(
        payouts,
        "_stripe_get",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("payout.paid must not require provider reconciliation lookup")
        ),
    )

    result = payouts.process_verified_stripe_payout_event(
        event_id="evt_payout_paid_123",
        event_type="payout.paid",
        obj=_payout(),
        config=_config(),
        payout_store=store,
        fee_store=StripeMarketplaceFeeEvidenceStore(tmp_path / "payout.sqlite3"),
    )

    assert result["provider_status"] == "paid"
    assert result["provider_reconciled"] is False
    assert result["bank_reconciled"] is False
    assert result["marketplace_allocation_mutated"] is False
    latest = store.latest("po_marketplace_123")
    assert latest["status"] == "paid"
    assert latest["bank_reconciled"] is False


def test_reconciliation_completed_maps_payment_and_refund_balance_transactions(tmp_path, monkeypatch):
    db = tmp_path / "payout.sqlite3"
    store = payouts.StripePayoutEvidenceStore(db)
    fee = _marketplace_evidence(db)
    calls = []

    def fake_get(config, path):
        calls.append(path)
        if path == "/v1/payouts/po_marketplace_123":
            return _payout(reconciliation_status="completed")
        if path == "/v1/balance_transactions?payout=po_marketplace_123&limit=100":
            return {
                "object": "list",
                "has_more": False,
                "data": [
                    {
                        "id": "txn_marketplace_charge_123",
                        "type": "charge",
                        "source": "ch_marketplace_123",
                        "amount": 2500,
                        "fee": 100,
                        "net": 2400,
                        "currency": "gbp",
                    },
                    {
                        "id": "txn_marketplace_refund_123",
                        "type": "refund",
                        "source": "re_marketplace_123",
                        "amount": -500,
                        "fee": 0,
                        "net": -500,
                        "currency": "gbp",
                    },
                    {
                        "id": "txn_unrelated_123",
                        "type": "charge",
                        "source": "ch_unrelated_123",
                        "amount": 900,
                        "fee": 50,
                        "net": 850,
                        "currency": "gbp",
                    },
                ],
            }
        raise AssertionError(path)

    monkeypatch.setattr(payouts, "_stripe_get", fake_get)
    result = payouts.process_verified_stripe_payout_event(
        event_id="evt_payout_reconciled_123",
        event_type="payout.reconciliation_completed",
        obj=_payout(reconciliation_status="completed"),
        config=_config(),
        payout_store=store,
        fee_store=fee,
    )

    assert result["provider_reconciled"] is True
    assert result["marketplace_transaction_count"] == 2
    assert result["marketplace_contribution_minor"] == 1900
    assert result["bank_reconciled"] is False
    assert result["marketplace_allocation_mutated"] is False
    memberships = store.memberships("po_marketplace_123")
    assert [row["evidence_kind"] for row in memberships] == ["payment", "refund"]
    assert sum(row["provider_net_minor"] for row in memberships) == 1900
    assert calls == [
        "/v1/payouts/po_marketplace_123",
        "/v1/balance_transactions?payout=po_marketplace_123&limit=100",
    ]


def test_reconciliation_paginates(monkeypatch):
    responses = iter(
        [
            {"data": [{"id": "txn_page_1"}], "has_more": True},
            {"data": [{"id": "txn_page_2"}], "has_more": False},
        ]
    )
    calls = []

    def fake_get(config, path):
        calls.append(path)
        return next(responses)

    monkeypatch.setattr(payouts, "_stripe_get", fake_get)
    result = payouts._fetch_transactions(_config(), "po_marketplace_123")
    assert [row["id"] for row in result] == ["txn_page_1", "txn_page_2"]
    assert calls[1].endswith("starting_after=txn_page_1")


def test_manual_payout_cannot_be_presented_as_provider_reconciled(tmp_path, monkeypatch):
    store = payouts.StripePayoutEvidenceStore(tmp_path / "payout.sqlite3")
    monkeypatch.setattr(
        payouts,
        "_stripe_get",
        lambda config, path: _payout(automatic=False, reconciliation_status="not_applicable"),
    )

    with pytest.raises(ValueError, match="manual payout"):
        payouts.process_verified_stripe_payout_event(
            event_id="evt_manual_recon_123",
            event_type="payout.reconciliation_completed",
            obj=_payout(automatic=False, reconciliation_status="not_applicable"),
            config=_config(),
            payout_store=store,
            fee_store=StripeMarketplaceFeeEvidenceStore(tmp_path / "payout.sqlite3"),
        )
    assert store.reconciliation("po_marketplace_123") is None


def test_paid_payout_can_later_fail_without_ever_becoming_bank_proof(tmp_path):
    store = payouts.StripePayoutEvidenceStore(tmp_path / "payout.sqlite3")
    fee = StripeMarketplaceFeeEvidenceStore(tmp_path / "payout.sqlite3")
    payouts.process_verified_stripe_payout_event(
        event_id="evt_paid_before_failure",
        event_type="payout.paid",
        obj=_payout(status="paid"),
        config=_config(),
        payout_store=store,
        fee_store=fee,
    )
    result = payouts.process_verified_stripe_payout_event(
        event_id="evt_late_failure",
        event_type="payout.failed",
        obj=_payout(
            status="failed",
            failure_balance_transaction="txn_payout_failure_123",
            failure_code="could_not_process",
        ),
        config=_config(),
        payout_store=store,
        fee_store=fee,
    )

    assert result["provider_status"] == "failed"
    assert result["bank_reconciled"] is False
    assert store.latest("po_marketplace_123")["status"] == "failed"


class _Evidence:
    def __init__(self, duplicate=False, status="received"):
        self.duplicate = duplicate
        self.status = status
        self.finished = []

    def begin_event(self, event, raw):
        return {
            "event_id": event["id"],
            "processing_status": self.status,
            "duplicate": self.duplicate,
        }

    def finish_event(self, event_id, status, error=None):
        self.finished.append((event_id, status, error))


def _webhook_client():
    app = FastAPI()
    app.include_router(hardening.router)
    return TestClient(app)


def _post(event):
    return _webhook_client().post(
        "/billing/stripe/webhook",
        content=json.dumps(event),
        headers={"stripe-signature": "test"},
    )


def _patch_webhook(monkeypatch, evidence):
    monkeypatch.setattr(hardening, "evidence_store", evidence)
    monkeypatch.setattr(hardening.StripeConfig, "from_env", classmethod(lambda cls: _config()))
    monkeypatch.setattr(hardening, "verify_webhook_signature", lambda *args, **kwargs: None)


def test_hardened_webhook_intercepts_payout_before_marketplace_or_legacy(monkeypatch):
    evidence = _Evidence()
    _patch_webhook(monkeypatch, evidence)
    processed = []
    monkeypatch.setattr(
        hardening,
        "process_verified_stripe_payout_event",
        lambda **kwargs: processed.append(kwargs)
        or {
            "processed": True,
            "kind": "provider_payout",
            "provider_status": "paid",
            "bank_reconciled": False,
        },
    )
    monkeypatch.setattr(
        hardening,
        "is_marketplace_stripe_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("payout must be routed before marketplace classification")
        ),
    )

    async def forbidden_legacy(request):
        raise AssertionError("payout must not reach legacy billing processor")

    monkeypatch.setattr(hardening, "base_stripe_webhook", forbidden_legacy)
    response = _post(
        {
            "id": "evt_payout_webhook_123",
            "type": "payout.paid",
            "data": {"object": _payout()},
        }
    )

    assert response.status_code == 200
    assert response.json()["provider_payout"] is True
    assert response.json()["bank_reconciled"] is False
    assert processed[0]["event_id"] == "evt_payout_webhook_123"
    assert evidence.finished == [("evt_payout_webhook_123", "processed", None)]


def test_payout_provider_lookup_failure_is_retryable(monkeypatch):
    evidence = _Evidence()
    _patch_webhook(monkeypatch, evidence)
    monkeypatch.setattr(
        hardening,
        "process_verified_stripe_payout_event",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("Stripe payout lookup unavailable")),
    )
    response = _post(
        {
            "id": "evt_payout_retry_123",
            "type": "payout.reconciliation_completed",
            "data": {"object": _payout(reconciliation_status="completed")},
        }
    )

    assert response.status_code == 502
    assert evidence.finished == [
        ("evt_payout_retry_123", "failed", "Stripe payout lookup unavailable")
    ]


def test_terminal_duplicate_payout_event_does_not_replay(monkeypatch):
    evidence = _Evidence(duplicate=True, status="processed")
    _patch_webhook(monkeypatch, evidence)
    monkeypatch.setattr(
        hardening,
        "process_verified_stripe_payout_event",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("duplicate must not replay")),
    )
    response = _post(
        {
            "id": "evt_payout_duplicate_123",
            "type": "payout.paid",
            "data": {"object": _payout()},
        }
    )

    assert response.status_code == 200
    assert response.json()["duplicate"] is True
    assert response.json()["provider_payout"] is True
    assert response.json()["bank_reconciled"] is False
    assert evidence.finished == []
