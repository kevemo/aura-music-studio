from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from aura_music_studio.cosmic_economy import (
    BASELINE_PACK_ID,
    EconomyError,
    EligibilityDecision,
    LiveGiftContext,
)
from aura_music_studio.cosmic_economy_integrations import IntegratedCosmicEconomy


class AllowEligibility:
    def check(self, **kwargs):
        return EligibilityDecision(True)


class Live:
    def gift_context(self, *, live_session_id, recipient_creator_id):
        return LiveGiftContext(live_session_id, recipient_creator_id, True, True, True)


def make_economy(tmp_path):
    return IntegratedCosmicEconomy(
        tmp_path / "economy.sqlite3",
        live_sessions=Live(),
        eligibility=AllowEligibility(),
    )


def purchase(economy, *, user_id="viewer-1", key="purchase-1"):
    return economy.create_purchase(
        user_id=user_id,
        pack_id=BASELINE_PACK_ID,
        pack_version=1,
        provider="fake",
        idempotency_key=key,
    )


def gift(economy, *, user_id="viewer-1", key="gift-1"):
    return economy.send_gift(
        sender_user_id=user_id,
        recipient_creator_id="creator-1",
        live_session_id="live-1",
        gift_id="starlight-spark",
        gift_version=1,
        quantity=1,
        idempotency_key=key,
    )


def test_purchase_rate_limit_is_account_scoped_and_retry_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("LSS_COIN_PURCHASE_RATE_LIMIT", "1")
    monkeypatch.setenv("LSS_ECONOMY_RATE_WINDOW_SECONDS", "60")
    economy = make_economy(tmp_path)

    first = purchase(economy, user_id="viewer-1", key="purchase-1")
    replay = purchase(economy, user_id="viewer-1", key="purchase-1")
    assert replay["id"] == first["id"]

    with pytest.raises(EconomyError) as exc:
        purchase(economy, user_id="viewer-1", key="purchase-2")
    assert exc.value.code == "RATE_LIMITED"
    assert exc.value.status_code == 429
    assert exc.value.details["action"] == "coin_purchase"
    assert exc.value.details["retry_after_seconds"] >= 1

    blocked = economy.operational_events(
        event_type="economy.rate_limit_blocked",
        user_id="viewer-1",
    )
    assert len(blocked) == 1
    assert blocked[0]["details"]["action"] == "coin_purchase"

    other = purchase(economy, user_id="viewer-2", key="purchase-other")
    assert other["user_id"] == "viewer-2"


def test_concurrent_same_purchase_idempotency_key_uses_one_rate_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("LSS_COIN_PURCHASE_RATE_LIMIT", "1")
    monkeypatch.setenv("LSS_ECONOMY_RATE_WINDOW_SECONDS", "60")
    economy = make_economy(tmp_path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: purchase(economy, user_id="viewer-1", key="purchase-concurrent"),
                range(2),
            )
        )

    assert results[0]["id"] == results[1]["id"]
    with economy._connect() as con:
        rate_count = con.execute(
            """SELECT COUNT(*) AS n FROM economy_rate_events
               WHERE user_id='viewer-1' AND action='coin_purchase'"""
        ).fetchone()["n"]
        reservation_count = con.execute(
            """SELECT COUNT(*) AS n FROM economy_rate_idempotency
               WHERE user_id='viewer-1' AND action='coin_purchase'
                 AND idempotency_key='purchase-concurrent'"""
        ).fetchone()["n"]
    assert rate_count == 1
    assert reservation_count == 1


def test_gift_rate_limit_blocks_new_debit_but_allows_idempotent_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("LSS_GIFT_SEND_RATE_LIMIT", "1")
    monkeypatch.setenv("LSS_ECONOMY_RATE_WINDOW_SECONDS", "60")
    economy = make_economy(tmp_path)
    economy.promotional_credit(
        user_id="viewer-1",
        coin_quantity=100,
        campaign_ref="rate-limit-fixture",
        idempotency_key="credit-1",
    )

    first = gift(economy, key="gift-1")
    replay = gift(economy, key="gift-1")
    assert replay["gift_transaction"]["id"] == first["gift_transaction"]["id"]
    assert replay["idempotent_replay"] is True

    with pytest.raises(EconomyError) as exc:
        gift(economy, key="gift-2")
    assert exc.value.code == "RATE_LIMITED"
    assert economy.get_balance("viewer-1")["available_coins"] == 90


def test_insufficient_balance_creates_operational_evidence_without_ledger_debit(tmp_path):
    economy = make_economy(tmp_path)
    with pytest.raises(EconomyError) as exc:
        gift(economy, key="gift-no-balance")
    assert exc.value.code == "INSUFFICIENT_COIN_BALANCE"
    assert economy.get_balance("viewer-1")["available_coins"] == 0
    events = economy.operational_events(
        event_type="economy.insufficient_balance",
        user_id="viewer-1",
    )
    assert len(events) == 1
    with economy._connect() as con:
        debits = con.execute(
            "SELECT COUNT(*) AS n FROM coin_ledger_entries WHERE entry_type='GIFT_DEBIT'"
        ).fetchone()["n"]
    assert debits == 0


def test_invalid_finance_rate_limit_configuration_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("LSS_COIN_PURCHASE_RATE_LIMIT", "0")
    economy = make_economy(tmp_path)
    with pytest.raises(EconomyError) as exc:
        purchase(economy)
    assert exc.value.code == "ECONOMY_RATE_LIMIT_CONFIG_INVALID"
    assert exc.value.status_code == 503
