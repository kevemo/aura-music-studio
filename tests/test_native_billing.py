from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from aura_music_studio.native_billing import (
    NativeEntitlementLedger,
    VerifiedNativeLifecycleEvent,
    VerifiedNativePayment,
)
from aura_music_studio.native_products import AURA_OS_ENTITLEMENT, AURA_SEC_ENTITLEMENT


NOW = datetime(2026, 1, 31, 12, 0, tzinfo=timezone.utc)


class StubVerifier:
    def __init__(self, evidence):
        self.evidence = evidence
        self.calls = 0

    def verify(self, raw_event: bytes, headers):
        self.calls += 1
        assert raw_event == b"signed-provider-event"
        assert headers["x-provider-signature"] == "verified-by-adapter"
        return self.evidence


def payment(**overrides) -> VerifiedNativePayment:
    values = {
        "provider": "paypal",
        "event_id": "WH-EVENT-1",
        "payment_id": "CAPTURE-1",
        "verification_id": "VERIFY-1",
        "user_id": "user-1",
        "product_id": "aura_sec",
        "billing_period": "monthly",
        "amount_minor": 499,
        "currency": "GBP",
        "occurred_at": NOW,
        "founding_offer": False,
    }
    values.update(overrides)
    return VerifiedNativePayment(**values)


def lifecycle(**overrides) -> VerifiedNativeLifecycleEvent:
    values = {
        "provider": "paypal",
        "event_id": "WH-LIFE-1",
        "payment_id": "CAPTURE-1",
        "verification_id": "VERIFY-LIFE-1",
        "user_id": "user-1",
        "product_id": "aura_sec",
        "event_type": "cancel",
        "occurred_at": datetime(2026, 2, 10, 12, 0, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return VerifiedNativeLifecycleEvent(**values)


def process(ledger: NativeEntitlementLedger, evidence: VerifiedNativePayment):
    verifier = StubVerifier(evidence)
    receipt = ledger.process_verified_event(
        raw_event=b"signed-provider-event",
        headers={"x-provider-signature": "verified-by-adapter"},
        verifier=verifier,
    )
    assert verifier.calls == 1
    return receipt


def process_lifecycle(ledger: NativeEntitlementLedger, evidence: VerifiedNativeLifecycleEvent):
    verifier = StubVerifier(evidence)
    receipt = ledger.process_verified_lifecycle_event(
        raw_event=b"signed-provider-event",
        headers={"x-provider-signature": "verified-by-adapter"},
        verifier=verifier,
    )
    assert verifier.calls == 1
    return receipt


def test_activation_requires_verifier_and_grants_only_native_entitlement(tmp_path):
    ledger = NativeEntitlementLedger(tmp_path / "native.sqlite3")

    receipt = process(ledger, payment())

    assert receipt.product_id == "aura_sec"
    assert receipt.entitlements == (AURA_SEC_ENTITLEMENT,)
    assert receipt.period_start.startswith("2026-01-31T12:00:00")
    assert receipt.period_end.startswith("2026-02-28T12:00:00")
    assert ledger.active_entitlements("user-1", at=NOW) == frozenset({AURA_SEC_ENTITLEMENT})
    assert "owner" not in ledger.active_entitlements("user-1", at=NOW)
    assert "admin" not in ledger.active_entitlements("user-1", at=NOW)
    assert "creator" not in ledger.active_entitlements("user-1", at=NOW)
    assert "agent" not in ledger.active_entitlements("user-1", at=NOW)


def test_exact_canonical_amount_and_currency_are_required(tmp_path):
    ledger = NativeEntitlementLedger(tmp_path / "native.sqlite3")

    with pytest.raises(ValueError, match="amount"):
        process(ledger, payment(amount_minor=498))
    with pytest.raises(ValueError, match="currency"):
        process(ledger, payment(event_id="WH-EVENT-2", payment_id="CAPTURE-2", currency="USD"))

    assert ledger.active_entitlements("user-1", at=NOW) == frozenset()


def test_event_and_payment_replays_are_rejected(tmp_path):
    ledger = NativeEntitlementLedger(tmp_path / "native.sqlite3")
    process(ledger, payment())

    with pytest.raises(ValueError, match="event"):
        process(ledger, payment())
    with pytest.raises(ValueError, match="payment"):
        process(ledger, payment(event_id="WH-EVENT-2"))


def test_founding_offer_is_first_annual_aura_sec_term_only(tmp_path):
    ledger = NativeEntitlementLedger(tmp_path / "native.sqlite3")
    founding = payment(
        billing_period="annual",
        amount_minor=2499,
        founding_offer=True,
    )

    receipt = process(ledger, founding)
    assert receipt.period_end.startswith("2027-01-31T12:00:00")

    with pytest.raises(ValueError, match="first product term"):
        process(
            ledger,
            payment(
                event_id="WH-EVENT-2",
                payment_id="CAPTURE-2",
                verification_id="VERIFY-2",
                billing_period="annual",
                amount_minor=2499,
                founding_offer=True,
                occurred_at=datetime(2027, 1, 31, 12, 1, tzinfo=timezone.utc),
            ),
        )


def test_founding_offer_cannot_be_applied_to_other_products_or_monthly(tmp_path):
    ledger = NativeEntitlementLedger(tmp_path / "native.sqlite3")

    with pytest.raises(ValueError, match="Founding pricing"):
        process(ledger, payment(founding_offer=True))
    with pytest.raises(ValueError, match="Founding pricing"):
        process(
            ledger,
            payment(
                event_id="WH-EVENT-2",
                payment_id="CAPTURE-2",
                verification_id="VERIFY-2",
                product_id="aura_os",
                billing_period="annual",
                amount_minor=4999,
                founding_offer=True,
            ),
        )


def test_bundle_grants_exactly_aura_os_and_aura_sec(tmp_path):
    ledger = NativeEntitlementLedger(tmp_path / "native.sqlite3")
    receipt = process(
        ledger,
        payment(product_id="aura_os_sec_bundle", amount_minor=799),
    )

    assert receipt.entitlements == (AURA_OS_ENTITLEMENT, AURA_SEC_ENTITLEMENT)
    assert ledger.active_entitlements("user-1", at=NOW) == frozenset(
        {AURA_OS_ENTITLEMENT, AURA_SEC_ENTITLEMENT}
    )


def test_active_entitlements_expire_without_mutating_other_authority(tmp_path):
    ledger = NativeEntitlementLedger(tmp_path / "native.sqlite3")
    process(ledger, payment())

    after_expiry = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
    assert ledger.active_entitlements("user-1", at=after_expiry) == frozenset()
    with pytest.raises(PermissionError, match="entitlement"):
        ledger.require_entitlement("user-1", AURA_SEC_ENTITLEMENT, at=after_expiry)


def test_missing_raw_event_or_verification_identity_fails_closed(tmp_path):
    ledger = NativeEntitlementLedger(tmp_path / "native.sqlite3")
    verifier = StubVerifier(payment())

    with pytest.raises(ValueError, match="raw provider event"):
        ledger.process_verified_event(raw_event=b"", headers={}, verifier=verifier)
    with pytest.raises(ValueError, match="verification"):
        process(ledger, payment(verification_id=""))


def test_payment_metadata_is_persisted_without_role_or_esp_columns(tmp_path):
    db = tmp_path / "native.sqlite3"
    ledger = NativeEntitlementLedger(db)
    process(ledger, payment())

    with sqlite3.connect(db) as con:
        payment_columns = {row[1] for row in con.execute("PRAGMA table_info(native_payment_events)")}
        entitlement_columns = {row[1] for row in con.execute("PRAGMA table_info(native_entitlements)")}
        lifecycle_columns = {row[1] for row in con.execute("PRAGMA table_info(native_lifecycle_events)")}

    forbidden = {"role", "esp_role", "is_owner", "is_admin", "creator", "agent", "plan_id"}
    assert payment_columns.isdisjoint(forbidden)
    assert entitlement_columns.isdisjoint(forbidden)
    assert lifecycle_columns.isdisjoint(forbidden)


def test_verified_cancellation_keeps_paid_term_then_expires(tmp_path):
    ledger = NativeEntitlementLedger(tmp_path / "native.sqlite3")
    process(ledger, payment())

    receipt = process_lifecycle(ledger, lifecycle(event_type="cancel"))

    assert receipt.event_type == "cancel"
    assert receipt.entitlements == (AURA_SEC_ENTITLEMENT,)
    assert receipt.effective_at.startswith("2026-02-28T12:00:00")
    assert ledger.active_entitlements(
        "user-1", at=datetime(2026, 2, 20, 12, 0, tzinfo=timezone.utc)
    ) == frozenset({AURA_SEC_ENTITLEMENT})
    assert ledger.active_entitlements(
        "user-1", at=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
    ) == frozenset()


def test_verified_refund_revokes_current_term_immediately(tmp_path):
    ledger = NativeEntitlementLedger(tmp_path / "native.sqlite3")
    process(ledger, payment())
    refund_at = datetime(2026, 2, 10, 12, 0, tzinfo=timezone.utc)

    receipt = process_lifecycle(
        ledger,
        lifecycle(event_type="refund", occurred_at=refund_at),
    )

    assert receipt.event_type == "refund"
    assert receipt.entitlements == (AURA_SEC_ENTITLEMENT,)
    assert ledger.active_entitlements(
        "user-1", at=datetime(2026, 2, 10, 12, 1, tzinfo=timezone.utc)
    ) == frozenset()


def test_lifecycle_requires_verified_matching_payment_identity(tmp_path):
    ledger = NativeEntitlementLedger(tmp_path / "native.sqlite3")
    process(ledger, payment())

    with pytest.raises(ValueError, match="verified native payment"):
        process_lifecycle(ledger, lifecycle(payment_id="UNKNOWN"))
    with pytest.raises(ValueError, match="identity"):
        process_lifecycle(ledger, lifecycle(event_id="WH-LIFE-2", user_id="user-2"))
    with pytest.raises(ValueError, match="identity"):
        process_lifecycle(ledger, lifecycle(event_id="WH-LIFE-3", product_id="aura_os"))


def test_lifecycle_replay_and_transition_replay_are_rejected(tmp_path):
    ledger = NativeEntitlementLedger(tmp_path / "native.sqlite3")
    process(ledger, payment())
    process_lifecycle(ledger, lifecycle(event_type="cancel"))

    with pytest.raises(ValueError, match="event"):
        process_lifecycle(ledger, lifecycle(event_type="cancel"))
    with pytest.raises(ValueError, match="transition"):
        process_lifecycle(ledger, lifecycle(event_id="WH-LIFE-2", event_type="cancel"))


def test_old_payment_refund_cannot_revoke_a_later_renewal(tmp_path):
    ledger = NativeEntitlementLedger(tmp_path / "native.sqlite3")
    process(ledger, payment())
    renewal_time = datetime(2026, 2, 28, 12, 0, tzinfo=timezone.utc)
    process(
        ledger,
        payment(
            event_id="WH-EVENT-2",
            payment_id="CAPTURE-2",
            verification_id="VERIFY-2",
            occurred_at=renewal_time,
        ),
    )

    receipt = process_lifecycle(
        ledger,
        lifecycle(
            event_id="WH-LIFE-OLD-REFUND",
            event_type="refund",
            occurred_at=datetime(2026, 3, 5, 12, 0, tzinfo=timezone.utc),
        ),
    )

    assert receipt.entitlements == ()
    assert ledger.active_entitlements(
        "user-1", at=datetime(2026, 3, 5, 12, 1, tzinfo=timezone.utc)
    ) == frozenset({AURA_SEC_ENTITLEMENT})


def test_refund_after_cancel_revokes_remaining_paid_access(tmp_path):
    ledger = NativeEntitlementLedger(tmp_path / "native.sqlite3")
    process(ledger, payment())
    process_lifecycle(ledger, lifecycle(event_type="cancel"))

    process_lifecycle(
        ledger,
        lifecycle(
            event_id="WH-LIFE-2",
            event_type="refund",
            occurred_at=datetime(2026, 2, 15, 12, 0, tzinfo=timezone.utc),
        ),
    )

    assert ledger.active_entitlements(
        "user-1", at=datetime(2026, 2, 15, 12, 1, tzinfo=timezone.utc)
    ) == frozenset()
