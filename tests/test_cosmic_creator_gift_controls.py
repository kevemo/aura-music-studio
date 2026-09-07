from __future__ import annotations

import pytest

from aura_music_studio.cosmic_economy import EconomyError, EligibilityDecision, LiveGiftContext
from aura_music_studio.cosmic_economy_owner_api import router as owner_router
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


def send(e, key):
    return e.send_gift(
        sender_user_id="viewer-1",
        recipient_creator_id="creator-1",
        live_session_id="live-1",
        gift_id="starlight-spark",
        gift_version=1,
        quantity=1,
        idempotency_key=key,
    )


def test_disabled_creator_receiving_rolls_back_entire_financial_transaction(tmp_path):
    e = economy(tmp_path)
    e.promotional_credit(
        user_id="viewer-1",
        coin_quantity=100,
        campaign_ref="fixture",
        idempotency_key="seed",
    )
    control = e.set_creator_gift_receiving(
        "creator-1",
        enabled=False,
        actor="owner-test",
        reason="creator safety hold",
    )
    assert control["receiving_enabled"] == 0

    with pytest.raises(EconomyError) as exc:
        send(e, "gift-blocked")
    assert exc.value.code == "CREATOR_GIFT_RECEIVING_DISABLED"
    assert e.get_balance("viewer-1")["available_coins"] == 100

    with e._connect() as con:
        gifts = con.execute("SELECT COUNT(*) AS n FROM gift_transactions").fetchone()["n"]
        receipts = con.execute("SELECT COUNT(*) AS n FROM creator_gift_receipts").fetchone()["n"]
        debits = con.execute(
            "SELECT COUNT(*) AS n FROM coin_ledger_entries WHERE entry_type='GIFT_DEBIT'"
        ).fetchone()["n"]
    assert gifts == 0
    assert receipts == 0
    assert debits == 0

    evidence = e.operational_events(
        event_type="economy.creator_receiving_block",
        user_id="viewer-1",
    )
    assert len(evidence) == 1
    assert evidence[0]["details"]["creator_recipient_id"] == "creator-1"


def test_reenabling_creator_receiving_allows_future_gift(tmp_path):
    e = economy(tmp_path)
    e.promotional_credit(
        user_id="viewer-1",
        coin_quantity=100,
        campaign_ref="fixture",
        idempotency_key="seed",
    )
    e.set_creator_gift_receiving(
        "creator-1",
        enabled=False,
        actor="owner-test",
        reason="temporary safety hold",
    )
    e.set_creator_gift_receiving(
        "creator-1",
        enabled=True,
        actor="owner-test",
        reason="review cleared",
    )
    result = send(e, "gift-after-enable")
    assert result["gift_transaction"]["status"] == "committed"
    assert e.get_balance("viewer-1")["available_coins"] == 90
    state = e.creator_gift_receiving_state("creator-1")
    assert state["receiving_enabled"] == 1


def test_unconfigured_creator_receiving_defaults_enabled(tmp_path):
    e = economy(tmp_path)
    state = e.creator_gift_receiving_state("creator-new")
    assert state["receiving_enabled"] == 1
    assert state["updated_at"] is None


def test_owner_api_exposes_creator_specific_receiving_control():
    routes = {
        (getattr(route, "path", ""), tuple(sorted(getattr(route, "methods", set()))))
        for route in owner_router.routes
    }
    assert (
        "/owner/economy/creators/{creator_recipient_id}/gift-receiving",
        ("POST",),
    ) in routes
