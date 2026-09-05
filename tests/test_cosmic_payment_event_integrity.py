from __future__ import annotations

import pytest

from aura_music_studio.cosmic_economy import (
    BASELINE_PACK_ID,
    EconomyError,
    EligibilityDecision,
    VerifiedPaymentEvent,
)
from aura_music_studio.cosmic_economy_personal_limits import PersonalLimitCosmicEconomy


class AllowEligibility:
    def check(self, **kwargs):
        return EligibilityDecision(True)


def economy(tmp_path):
    return PersonalLimitCosmicEconomy(
        tmp_path / "economy.sqlite3",
        eligibility=AllowEligibility(),
    )


def purchase(e, *, user_id="viewer-1", key="purchase-1", payment_id="pay-1"):
    row = e.create_purchase(
        user_id=user_id,
        pack_id=BASELINE_PACK_ID,
        pack_version=1,
        provider="fake",
        idempotency_key=key,
    )
    return e.bind_provider_payment(row["id"], provider_payment_id=payment_id)


def event(
    purchase_row,
    *,
    event_id="evt-1",
    event_type="confirmed",
    payment_id="pay-1",
    occurred_at="2026-09-05T02:00:00+00:00",
):
    return VerifiedPaymentEvent(
        provider="fake",
        provider_event_id=event_id,
        provider_payment_id=payment_id,
        purchase_id=purchase_row["id"],
        event_type=event_type,
        verified=True,
        occurred_at=occurred_at,
    )


def test_exact_verified_provider_event_replay_is_idempotent(tmp_path):
    e = economy(tmp_path)
    p = purchase(e)
    verified = event(p)

    first = e.apply_verified_payment_event(verified)
    replay = e.apply_verified_payment_event(verified)

    assert first["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert e.get_balance("viewer-1")["available_coins"] == 1000


def test_provider_event_id_reuse_with_changed_event_type_is_rejected(tmp_path):
    e = economy(tmp_path)
    p = purchase(e)
    original = event(p, event_id="evt-collision", event_type="confirmed")
    e.apply_verified_payment_event(original)

    changed = event(p, event_id="evt-collision", event_type="refunded")
    with pytest.raises(EconomyError) as exc:
        e.apply_verified_payment_event(changed)

    assert exc.value.code == "PAYMENT_EVENT_ID_REUSED"
    assert exc.value.status_code == 409
    assert e.get_balance("viewer-1")["available_coins"] == 1000
    events = e.operational_events(event_type="economy.payment_event_id_conflict")
    assert len(events) == 1
    assert events[0]["details"]["provider_event_id"] == "evt-collision"
    assert events[0]["details"]["presented_event_type"] == "refunded"


def test_provider_event_id_reuse_with_changed_purchase_is_rejected(tmp_path):
    e = economy(tmp_path)
    first_purchase = purchase(e, user_id="viewer-1", key="purchase-1", payment_id="pay-1")
    second_purchase = purchase(e, user_id="viewer-2", key="purchase-2", payment_id="pay-2")
    original = event(first_purchase, event_id="evt-shared", payment_id="pay-1")
    e.apply_verified_payment_event(original)

    changed = event(second_purchase, event_id="evt-shared", payment_id="pay-2")
    with pytest.raises(EconomyError) as exc:
        e.apply_verified_payment_event(changed)

    assert exc.value.code == "PAYMENT_EVENT_ID_REUSED"
    assert e.get_balance("viewer-1")["available_coins"] == 1000
    assert e.get_balance("viewer-2")["available_coins"] == 0


def test_failed_event_id_cannot_be_replayed_as_cancelled(tmp_path):
    e = economy(tmp_path)
    p = purchase(e)
    failed = event(p, event_id="evt-terminal", event_type="failed")
    first = e.apply_verified_payment_event(failed)
    assert first["purchase"]["status"] == "failed"

    cancelled = event(p, event_id="evt-terminal", event_type="cancelled")
    with pytest.raises(EconomyError) as exc:
        e.apply_verified_payment_event(cancelled)

    assert exc.value.code == "PAYMENT_EVENT_ID_REUSED"
    with e._connect() as con:
        stored = con.execute(
            "SELECT status FROM coin_purchases WHERE id=?",
            (p["id"],),
        ).fetchone()
    assert stored["status"] == "failed"
    assert e.get_balance("viewer-1")["available_coins"] == 0


def test_failed_purchase_cannot_confirm_with_a_new_provider_event(tmp_path):
    e = economy(tmp_path)
    p = purchase(e)
    e.apply_verified_payment_event(event(p, event_id="evt-failed", event_type="failed"))

    with pytest.raises(EconomyError) as exc:
        e.apply_verified_payment_event(
            event(p, event_id="evt-late-confirm", event_type="confirmed")
        )

    assert exc.value.code == "PAYMENT_STATE_CONFLICT"
    assert exc.value.details["current_status"] == "failed"
    assert e.get_balance("viewer-1")["available_coins"] == 0
    conflicts = e.operational_events(event_type="economy.payment_state_conflict")
    assert len(conflicts) == 1
    assert conflicts[0]["details"]["presented_event_type"] == "confirmed"


def test_second_chargeback_after_dispute_recovery_requires_review(tmp_path):
    e = economy(tmp_path)
    p = purchase(e)
    e.apply_verified_payment_event(event(p, event_id="evt-confirm", event_type="confirmed"))
    e.apply_verified_payment_event(event(p, event_id="evt-chargeback-1", event_type="chargeback"))
    assert e.get_balance("viewer-1")["available_coins"] == 0

    restored = e.apply_verified_payment_event(
        event(p, event_id="evt-dispute-won", event_type="dispute_won")
    )
    assert restored["purchase"]["status"] == "confirmed"
    assert e.get_balance("viewer-1")["available_coins"] == 1000

    with pytest.raises(EconomyError) as exc:
        e.apply_verified_payment_event(
            event(p, event_id="evt-chargeback-2", event_type="chargeback")
        )

    assert exc.value.code == "PAYMENT_DISPUTE_CYCLE_REQUIRES_REVIEW"
    assert e.get_balance("viewer-1")["available_coins"] == 1000
    with e._connect() as con:
        stored = con.execute(
            "SELECT status FROM coin_purchases WHERE id=?",
            (p["id"],),
        ).fetchone()
    assert stored["status"] == "confirmed"
    review_events = e.operational_events(event_type="economy.payment_dispute_cycle_review")
    assert len(review_events) == 1
