from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from aura_music_studio.cosmic_economy import EconomyError, EligibilityDecision, LiveGiftContext
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


def test_owner_adjustment_key_is_bound_to_exact_account_and_payload(tmp_path):
    e = economy(tmp_path)
    first = e.owner_adjustment(
        user_id="viewer-1",
        coin_delta=100,
        actor="owner-test",
        reason="support correction",
        reference="case-1",
        idempotency_key="adjust-1",
    )
    replay = e.owner_adjustment(
        user_id="viewer-1",
        coin_delta=100,
        actor="owner-test",
        reason="support correction",
        reference="case-1",
        idempotency_key="adjust-1",
    )
    assert replay["id"] == first["id"]
    assert e.get_balance("viewer-1")["available_coins"] == 100

    with pytest.raises(EconomyError) as changed:
        e.owner_adjustment(
            user_id="viewer-1",
            coin_delta=200,
            actor="owner-test",
            reason="support correction",
            reference="case-1",
            idempotency_key="adjust-1",
        )
    assert changed.value.code == "IDEMPOTENCY_KEY_REUSED"

    with pytest.raises(EconomyError) as cross_user:
        e.owner_adjustment(
            user_id="viewer-2",
            coin_delta=100,
            actor="owner-test",
            reason="support correction",
            reference="case-1",
            idempotency_key="adjust-1",
        )
    assert cross_user.value.code == "IDEMPOTENCY_KEY_REUSED"
    assert e.get_balance("viewer-2")["available_coins"] == 0


def test_concurrent_owner_adjustment_same_key_credits_once(tmp_path):
    e = economy(tmp_path)

    def adjust(_):
        return e.owner_adjustment(
            user_id="viewer-1",
            coin_delta=75,
            actor="owner-test",
            reason="concurrency fixture",
            reference="case-concurrent",
            idempotency_key="adjust-concurrent",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(adjust, range(2)))
    assert results[0]["id"] == results[1]["id"]
    assert e.get_balance("viewer-1")["available_coins"] == 75


def test_promotional_credit_key_rejects_changed_campaign_amount_or_user(tmp_path):
    e = economy(tmp_path)
    first = e.promotional_credit(
        user_id="viewer-1",
        coin_quantity=50,
        campaign_ref="launch",
        idempotency_key="promo-1",
        actor="owner-test",
    )
    replay = e.promotional_credit(
        user_id="viewer-1",
        coin_quantity=50,
        campaign_ref="launch",
        idempotency_key="promo-1",
        actor="owner-test",
    )
    assert replay["id"] == first["id"]
    assert e.get_balance("viewer-1")["available_coins"] == 50

    with pytest.raises(EconomyError) as changed:
        e.promotional_credit(
            user_id="viewer-1",
            coin_quantity=75,
            campaign_ref="launch",
            idempotency_key="promo-1",
            actor="owner-test",
        )
    assert changed.value.code == "IDEMPOTENCY_KEY_REUSED"

    with pytest.raises(EconomyError) as other_campaign:
        e.promotional_credit(
            user_id="viewer-1",
            coin_quantity=50,
            campaign_ref="different-campaign",
            idempotency_key="promo-1",
            actor="owner-test",
        )
    assert other_campaign.value.code == "IDEMPOTENCY_KEY_REUSED"

    with pytest.raises(EconomyError) as cross_user:
        e.promotional_credit(
            user_id="viewer-2",
            coin_quantity=50,
            campaign_ref="launch",
            idempotency_key="promo-1",
            actor="owner-test",
        )
    assert cross_user.value.code == "IDEMPOTENCY_KEY_REUSED"


def test_gift_reversal_key_cannot_reverse_two_gifts_or_credit_twice(tmp_path):
    e = economy(tmp_path)
    e.promotional_credit(
        user_id="viewer-1",
        coin_quantity=100,
        campaign_ref="fixture",
        idempotency_key="seed",
    )
    first = e.send_gift(
        sender_user_id="viewer-1",
        recipient_creator_id="creator-1",
        live_session_id="live-1",
        gift_id="starlight-spark",
        gift_version=1,
        quantity=1,
        idempotency_key="gift-1",
    )
    second = e.send_gift(
        sender_user_id="viewer-1",
        recipient_creator_id="creator-1",
        live_session_id="live-1",
        gift_id="starlight-spark",
        gift_version=1,
        quantity=1,
        idempotency_key="gift-2",
    )
    assert e.get_balance("viewer-1")["available_coins"] == 80

    reversed_first = e.reverse_gift(
        first["gift_transaction"]["id"],
        actor="owner-test",
        reason="technical correction",
        reference="rev-case-1",
        idempotency_key="reverse-1",
    )
    assert reversed_first["idempotent_replay"] is False
    assert e.get_balance("viewer-1")["available_coins"] == 90

    replay = e.reverse_gift(
        first["gift_transaction"]["id"],
        actor="owner-test",
        reason="technical correction",
        reference="rev-case-1",
        idempotency_key="reverse-1",
    )
    assert replay["idempotent_replay"] is True
    assert e.get_balance("viewer-1")["available_coins"] == 90

    with pytest.raises(EconomyError) as reused:
        e.reverse_gift(
            second["gift_transaction"]["id"],
            actor="owner-test",
            reason="technical correction",
            reference="rev-case-2",
            idempotency_key="reverse-1",
        )
    assert reused.value.code == "IDEMPOTENCY_KEY_REUSED"
    assert e.get_balance("viewer-1")["available_coins"] == 90
    with e._connect() as con:
        second_row = con.execute(
            "SELECT status FROM gift_transactions WHERE id=?",
            (second["gift_transaction"]["id"],),
        ).fetchone()
    assert second_row["status"] == "committed"

    with pytest.raises(EconomyError) as new_key:
        e.reverse_gift(
            first["gift_transaction"]["id"],
            actor="owner-test",
            reason="technical correction",
            reference="rev-case-1",
            idempotency_key="reverse-new-key",
        )
    assert new_key.value.code == "GIFT_ALREADY_REVERSED"
    assert e.get_balance("viewer-1")["available_coins"] == 90
